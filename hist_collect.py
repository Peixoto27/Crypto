# hist_collect.py
import os, time, math
import pandas as pd
import ccxt

OUT_DIR = os.path.join("data", "history")
os.makedirs(OUT_DIR, exist_ok=True)

# Edite a lista se quiser
SYMBOLS = [
    "BTC/USDT","ETH/USDT","BNB/USDT","XRP/USDT","SOL/USDT","ADA/USDT",
    "DOGE/USDT","TRX/USDT","AVAX/USDT","LINK/USDT","MATIC/USDT","ATOM/USDT",
]

TIMEFRAME = "1h"
DAYS = 180              # quantos dias voltar
EXCHANGE = ccxt.binance({"enableRateLimit": True})

def fetch_all(symbol, timeframe=TIMEFRAME, days=DAYS, limit=1000):
    ms_per_candle = EXCHANGE.parse_timeframe(timeframe) * 1000
    since = EXCHANGE.milliseconds() - days*24*60*60*1000
    all_rows = []
    while True:
        ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not ohlcv:
            break
        all_rows += ohlcv
        since = ohlcv[-1][0] + ms_per_candle
        # Binance rate limit básico
        time.sleep(0.2)
        if len(ohlcv) < limit:
            break
    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows, columns=["ts","open","high","low","close","volume"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def main():
    for sym in SYMBOLS:
        try:
            print(f"Baixando {sym} {TIMEFRAME}...")
            df = fetch_all(sym)
            if df.empty:
                print(f"  -> vazio.")
                continue
            base = sym.replace("/", "")
            out = os.path.join(OUT_DIR, f"{base}_{TIMEFRAME}.csv")
            df.to_csv(out, index=False)
            print(f"  -> OK: {len(df)} candles -> {out}")
        except Exception as e:
            print(f"Falhou {sym}: {e}")

if __name__ == "__main__":
    main()
