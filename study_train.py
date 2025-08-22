# study_train.py
import os, glob, pickle
import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score
from lightgbm import LGBMClassifier
from features import add_basic_features, make_target, FEATURE_COLS

DATA_DIR = os.path.join("data","history")
MODEL_PATH = os.path.join("model","model.pkl")
os.makedirs("model", exist_ok=True)

def load_concat():
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*_1h.csv")))
    frames = []
    for fp in files:
        try:
            df = pd.read_csv(fp)
            df = add_basic_features(df)
            y = make_target(df, horizon=1, threshold=0.002)
            df["y"] = y
            df["symbol"] = os.path.basename(fp).split("_")[0]  # BTCUSDT
            frames.append(df)
        except Exception as e:
            print("Falhou ler:", fp, e)
    if not frames:
        return pd.DataFrame()
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.dropna().reset_index(drop=True)
    return all_df

def main():
    data = load_concat()
    if data.empty:
        print("Sem dados. Rode hist_collect.py antes.")
        return

    X = data[FEATURE_COLS].values
    y = data["y"].values

    print(f"Dataset: {X.shape[0]} amostras | {X.shape[1]} features | Positivos={y.sum()} Negativos={(y==0).sum()}")

    # Validação rápida
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    aucs = []
    for i,(tr,va) in enumerate(skf.split(X,y),1):
        model = LGBMClassifier(
            n_estimators=400,
            learning_rate=0.05,
            max_depth=-1,
            num_leaves=31,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1
        )
        model.fit(X[tr], y[tr])
        proba = model.predict_proba(X[va])[:,1]
        auc = roc_auc_score(y[va], proba)
        aucs.append(auc)
        print(f"Fold {i} AUC={auc:.3f}")

    print(f"AUC médio={np.mean(aucs):.3f} ± {np.std(aucs):.3f}")

    # Treina final em tudo e salva
    final_model = LGBMClassifier(
        n_estimators=400,
        learning_rate=0.05,
        max_depth=-1,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=-1
    ).fit(X,y)

    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": final_model, "features": FEATURE_COLS}, f)
    print("Modelo salvo em:", MODEL_PATH)

if __name__ == "__main__":
    main()
