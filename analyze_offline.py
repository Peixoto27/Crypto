# analyze_offline.py
import argparse, json
from pathlib import Path
import pandas as pd
import numpy as np
import joblib

FEATURES = ["ret_1","ret_3","ret_5","vol_10","vol_20","sma_5_over_20","body_over_range","volume"]

def build_last_row(csv_path):
    df = pd.read_csv(csv_path, parse_dates=["timestamp"]).sort_values("timestamp")
    # reproduzir as mesmas features do prepare_dataset
    df["ret_1"] = df["close"].pct_change(1)
    df["ret_3"] = df["close"].pct_change(3)
    df["ret_5"] = df["close"].pct_change(5)
    df["vol_10"] = df["volume"].rolling(10).mean()
    df["vol_20"] = df["volume"].rolling(20).mean()
    df["sma_5"] = df["close"].rolling(5).mean()
    df["sma_20"] = df["close"].rolling(20).mean()
    df["sma_5_over_20"] = df["sma_5"]/df["sma_20"]
    body = (df["close"] - df["open"]).abs()
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["body_over_range"] = body/rng
    row = df.dropna().tail(1)
    if row.empty:
        return None
    return row[FEATURES].iloc[0].values

def main():
    ap = argparse.ArgumentParser(description="Predições offline nos últimos candles por par.")
    ap.add_argument("--model", default="model/model.pkl")
    ap.add_argument("--histdir", default="data/history")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    bundle = joblib.load(args.model)
    model, meta = bundle["model"], bundle["meta"]
    feats = meta.get("features", FEATURES)

    preds = []
    for csv in sorted(Path(args.histdir).glob("*.csv")):
        sym = csv.stem.upper()
        x = build_last_row(csv)
        if x is None or len(x)!=len(feats):
            continue
        proba = float(model.predict_proba([x])[:,1][0])
        preds.append({"symbol": sym, "proba": proba})

    if not preds:
        print("Sem amostras válidas.")
        return

    preds.sort(key=lambda d: d["proba"], reverse=True)
    top = preds[:args.top]
    print("\nTop sinais (probabilidade de alta):")
    for r in top:
        print(f"  {r['symbol']}: {r['proba']:.2%}")

    # salva JSON opcional
    out = Path("data/signals_offline.json")
    out.write_text(json.dumps(top, indent=2))
    print(f"\n[OK] Sinais salvos em {out}")

if __name__ == "__main__":
    main()
