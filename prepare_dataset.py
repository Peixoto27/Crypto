# prepare_dataset.py
import argparse
from pathlib import Path
import pandas as pd
import numpy as np

def add_features(df):
    # retornos passados
    for k in [1,3,5]:
        df[f"ret_{k}"] = df["close"].pct_change(k)
    # volumes médios
    for w in [10,20]:
        df[f"vol_{w}"] = df["volume"].rolling(w).mean()
    # SMAs
    df["sma_5"] = df["close"].rolling(5).mean()
    df["sma_20"] = df["close"].rolling(20).mean()
    df["sma_5_over_20"] = df["sma_5"] / df["sma_20"]
    # candle body / range
    body = (df["close"] - df["open"]).abs()
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["body_over_range"] = body / rng
    # normalizações simples
    df["volume"] = df["volume"].replace(0, np.nan)
    return df

def make_label(df, horizon=3, th=0.01):
    # alvo = se fechar daqui a N candles subir mais que th
    fwd = df["close"].pct_change(horizon).shift(-horizon)
    df["label"] = (fwd > th).astype(int)
    return df

def main():
    ap = argparse.ArgumentParser(description="Gera dataset consolidado a partir de data/history.")
    ap.add_argument("--histdir", default="data/history", help="pasta com CSVs por par")
    ap.add_argument("--out", default="data/dataset.csv", help="arquivo de saída")
    ap.add_argument("--horizon", type=int, default=3, help="horizonte futuro (candles)")
    ap.add_argument("--label-th", type=float, default=0.01, help="limiar de alta para label=1")
    args = ap.parse_args()

    Path("data").mkdir(exist_ok=True)
    rows = []
    for csv in sorted(Path(args.histdir).glob("*.csv")):
        sym = csv.stem.upper()
        df = pd.read_csv(csv, parse_dates=["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        df = add_features(df)
        df = make_label(df, horizon=args.horizon, th=args.label_th)
        df["symbol"] = sym
        rows.append(df)

    if not rows:
        print("Nenhum CSV em data/history/.")
        return

    full = pd.concat(rows, ignore_index=True)
    # limpar NaNs iniciais
    full = full.dropna().reset_index(drop=True)

    feats = ["ret_1","ret_3","ret_5","vol_10","vol_20","sma_5_over_20","body_over_range","volume"]
    cols = ["timestamp","symbol","open","high","low","close"] + feats + ["label"]
    full = full[cols]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    full.to_csv(args.out, index=False)
    print(f"[OK] Dataset salvo: {args.out} | linhas={len(full)} | features={len(feats)}")

if __name__ == "__main__":
    main()
