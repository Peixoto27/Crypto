import ccxt
import pandas as pd
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--symbols-file", type=str, default="pairs.txt")
parser.add_argument("--tf", type=str, default="1h")
parser.add_argument("--limit", type=int, default=1000)
args = parser.parse_args()

exchange = ccxt.binance()
out_dir = Path("data/history")
out_dir.mkdir(parents=True, exist_ok=True)

with open(args.symbols_file) as f:
    symbols = [s.strip() for s in f.readlines()]

for sym in symbols:
    print(f"Baixando {sym}...")
    candles = exchange.fetch_ohlcv(sym, args.tf, limit=args.limit)
    df = pd.DataFrame(candles, columns=["time","open","high","low","close","volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    df.to_csv(out_dir / f"{sym.replace('/','_')}.csv", index=False)

print("✅ Histórico salvo em data/history/")
