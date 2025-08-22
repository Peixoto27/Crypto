# offline_backtest.py
import os, glob, pickle
import numpy as np
import pandas as pd
from features import add_basic_features, FEATURE_COLS

MODEL_PATH = os.path.join("model","model.pkl")
DATA_DIR = os.path.join("data","history")
THRESH = 0.60     # probabilidade mínima para entrar comprado
STOP = -0.05      # stop -5%
TAKE = 0.08       # take +8%

def load_model():
    with open(MODEL_PATH, "rb") as f:
        obj = pickle.load(f)
    return obj["model"], obj["features"]

def backtest_one(csv_path, model, feat_cols):
    df = pd.read_csv(csv_path)
    df = add_basic_features(df).dropna().reset_index(drop=True)
    proba = model.predict_proba(df[feat_cols].values)[:,1]
    df["proba"] = proba

    equity = 1.0
    in_pos = False
    entry = 0.0
    ecurve = []

    for i in range(len(df)):
        price = df.loc[i,"close"]

        if not in_pos:
            if df.loc[i,"proba"] >= THRESH:
                in_pos = True
                entry = price
        else:
            ret = (price/entry) - 1.0
            if ret <= STOP or ret >= TAKE:
                equity *= (1.0 + ret)
                in_pos = False
                entry = 0.0
        ecurve.append(equity if not np.isnan(equity) else 0.0)

    return pd.DataFrame({"date": df["date"], "equity": ecurve})

def main():
    model, feat_cols = load_model()
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*_1h.csv")))
    curves = []
    for fp in files:
        print("BT:", os.path.basename(fp))
        ec = backtest_one(fp, model, feat_cols)
        curves.append(ec["equity"].values)
    if not curves:
        print("Sem dados. Rode hist_collect.py e study_train.py antes.")
        return
    # média das curvas por ativo
    arr = np.vstack(curves)
    eq = arr.mean(axis=0)
    total_return = eq[-1] - 1.0
    dd = np.max(np.maximum.accumulate(eq) - eq)
    print(f"Retorno médio (hold=1.0): {total_return*100:.1f}% | MaxDD médio: {dd*100:.1f}%")

if __name__ == "__main__":
    main()
