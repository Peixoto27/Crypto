# fetch_history.py
import os, time, argparse, sys
from pathlib import Path
import pandas as pd

try:
    import ccxt
except Exception as e:
    print("Instale ccxt:  pip install ccxt", file=sys.stderr); raise

def read_symbols(args):
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.symbols_file and Path(args.symbols_file).is_file():
        return [l.strip().upper() for l in Path(args.symbols_file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    # fallback básico
    return ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]

def fetch_one(exchange, symbol, tf, limit, outdir, pause):
    mkt = symbol
    sym = symbol
    # ccxt usa o formato com / (ex.: BTC/USDT) para mercados spot
    if "USDT" in symbol and "/" not in symbol:
        base = symbol.replace("USDT","")
        sym = f"{base}/USDT"
    try:
        ohlc = exchange.fetch_ohlcv(sym, timeframe=tf, limit=limit)
    except Exception as e:
        print(f"[skip] {symbol}: {e}")
        return
    if not ohlc:
        print(f"[vazio] {symbol}")
        return
    df = pd.DataFrame(ohlc, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    out = Path(outdir)/f"{mkt}.csv"
    df.to_csv(out, index=False)
    print(f"[OK] {symbol} -> {out.name} ({len(df)})")
    time.sleep(pause)

def main():
    ap = argparse.ArgumentParser(description="Baixa OHLCV da Binance para CSVs (data/history).")
    ap.add_argument("--symbols", help="Lista separada por vírgula (ex: BTCUSDT,ETHUSDT)")
    ap.add_argument("--symbols-file", default="pairs.txt", help="Arquivo com um par por linha")
    ap.add_argument("--tf", default="1h", help="timeframe ccxt (1m,5m,15m,1h,4h,1d...)")
    ap.add_argument("--limit", type=int, default=500, help="quantidade de candles")
    ap.add_argument("--outdir", default="data/history", help="pasta de saída")
    ap.add_argument("--pause", type=float, default=0.8, help="pausa entre pares (s)")
    args = ap.parse_args()

    Path(args.outdir).mkdir(parents=True, exist_ok=True)
    symbols = read_symbols(args)
    print(f"Total pares: {len(symbols)} | tf={args.tf} | limit={args.limit}")

    ex = ccxt.binance({"enableRateLimit": True})
    for s in symbols:
        fetch_one(ex, s, args.tf, args.limit, args.outdir, args.pause)

if __name__ == "__main__":
    main()
