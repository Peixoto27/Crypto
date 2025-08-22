# trainer_offline.py
import argparse, json
from pathlib import Path
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
import joblib

def main():
    ap = argparse.ArgumentParser(description="Treino offline com LightGBM.")
    ap.add_argument("--dataset", default="data/dataset.csv")
    ap.add_argument("--model", default="model/model.pkl")
    ap.add_argument("--test-size", type=float, default=0.25)
    ap.add_argument("--random-state", type=int, default=13)
    args = ap.parse_args()

    Path("model").mkdir(exist_ok=True)
    df = pd.read_csv(args.dataset, parse_dates=["timestamp"])
    feats = ["ret_1","ret_3","ret_5","vol_10","vol_20","sma_5_over_20","body_over_range","volume"]
    X = df[feats]
    y = df["label"].astype(int)

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=args.test_size, shuffle=False)  # respeita tempo

    model = lgb.LGBMClassifier(
        n_estimators=400,
        learning_rate=0.05,
        max_depth=-1,
        num_leaves=31,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=args.random_state
    )
    model.fit(Xtr, ytr)
    proba = model.predict_proba(Xte)[:,1]
    auc = roc_auc_score(yte, proba)
    meta = {"features": feats, "auc": float(auc)}
    joblib.dump({"model": model, "meta": meta}, args.model)
    print(f"[OK] Modelo salvo em {args.model} | AUC={auc:.3f} | features={feats}")

if __name__ == "__main__":
    main()
