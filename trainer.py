# -*- coding: utf-8 -*-
"""
trainer.py — Treinador LightGBM para o Crypton Signals

Lê amostras do histórico e treina um classificador binário (probabilidade de "bom sinal").
Salva o modelo em disco e registra métricas básicas.

ONDE BUSCA DADOS (na ordem):
1) {HISTORY_DIR}/samples/*.jsonl      # um JSON por linha: {"features": {...}, "label": 0/1, ...}
2) {HISTORY_DIR}/samples/*.json       # array JSON com itens {"features": {...}, "label": 0/1, ...}
3) {HISTORY_DIR}/training_data.csv    # CSV com colunas: ... features numéricas ..., label

ENV ÚTEIS:
- HISTORY_DIR         (default: data/history)
- MODEL_FILE          (default: model.pkl)
- TRAIN_MIN_SAMPLES   (default: 200)
- RANDOM_STATE        (default: 42)
"""

import os
import json
import glob
import time
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score, precision_score, recall_score
)
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from joblib import dump

# LightGBM
try:
    from lightgbm import LGBMClassifier
except Exception as e:
    raise RuntimeError(
        "LightGBM não está instalado. Garanta 'lightgbm' no requirements.txt."
    ) from e


# =============== Config via ENV ===============
def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)

HISTORY_DIR       = _get_env("HISTORY_DIR", "data/history")
MODEL_FILE        = _get_env("MODEL_FILE", "model.pkl")
TRAIN_MIN_SAMPLES = int(_get_env("TRAIN_MIN_SAMPLES", "200"))
RANDOM_STATE      = int(_get_env("RANDOM_STATE", "42"))

os.makedirs(HISTORY_DIR, exist_ok=True)


# =============== Utils de IO ===============
def _log(msg: str):
    print(msg, flush=True)

def _now_ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


# =============== Carregamento de Dados ===============
def _load_jsonl_samples(pattern: str):
    """Carrega JSONL (um JSON por linha)."""
    rows = []
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    rows.append(obj)
        except Exception as e:
            _log(f"⚠️  Falha ao ler {path}: {e}")
    return rows

def _load_json_array_samples(pattern: str):
    """Carrega JSON (array com vários objetos)."""
    rows = []
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                arr = json.load(f)
            if isinstance(arr, list):
                rows.extend(arr)
        except Exception as e:
            _log(f"⚠️  Falha ao ler {path}: {e}")
    return rows

def _load_csv_samples(path: str):
    if not os.path.exists(path):
        return None
    try:
        return pd.read_csv(path)
    except Exception as e:
        _log(f"⚠️  Falha ao ler CSV {path}: {e}")
        return None


def _extract_Xy_from_rows(rows):
    """
    Espera itens com:
      - "features": {feat_name: valor, ...}
      - "label": 0/1
    Ignora itens sem label/features.
    """
    feats_list = []
    y_list = []

    for r in rows:
        if not isinstance(r, dict):
            continue

        # estrutura principal: features + label
        features = r.get("features")
        label = r.get("label")

        # fallback: se vier tudo 'flat' (sem 'features'), tenta usar campos numéricos
        if features is None:
            # copia só numéricos (exceto label)
            feats = {
                k: v for k, v in r.items()
                if k != "label" and isinstance(v, (int, float))
            }
        else:
            feats = features

        if label is None or not isinstance(feats, dict) or len(feats) == 0:
            continue

        # assegura float
        clean = {}
        for k, v in feats.items():
            try:
                clean[k] = float(v)
            except Exception:
                continue

        if len(clean) == 0:
            continue

        try:
            y_val = int(label)
        except Exception:
            # tenta interpretacao booleana
            y_val = 1 if str(label).lower() in ("true", "1", "yes", "win", "good") else 0

        feats_list.append(clean)
        y_list.append(y_val)

    if not feats_list:
        return pd.DataFrame(), np.array([])

    # alinhar colunas pelo union das chaves
    X = pd.DataFrame(feats_list).fillna(0.0)
    y = np.array(y_list, dtype=int)
    return X, y


def load_training_data():
    """
    Tenta várias fontes:
      1) JSONL em {HISTORY_DIR}/samples/*.jsonl
      2) JSON array em {HISTORY_DIR}/samples/*.json
      3) CSV  em {HISTORY_DIR}/training_data.csv
    Retorna X (DataFrame), y (np.array)
    """
    samples_dir = os.path.join(HISTORY_DIR, "samples")
    os.makedirs(samples_dir, exist_ok=True)

    # 1) JSONL
    rows = _load_jsonl_samples(os.path.join(samples_dir, "*.jsonl"))

    # 2) JSON array
    if len(rows) == 0:
        rows = _load_json_array_samples(os.path.join(samples_dir, "*.json"))

    if len(rows) > 0:
        X, y = _extract_Xy_from_rows(rows)
        if len(y) > 0:
            return X, y

    # 3) CSV (fallback)
    csv_path = os.path.join(HISTORY_DIR, "training_data.csv")
    df = _load_csv_samples(csv_path)
    if df is not None and "label" in df.columns:
        y = df["label"].astype(int).values
        X = df.drop(columns=["label"])
        # mantém apenas numéricos
        X = X.select_dtypes(include=[np.number]).fillna(0.0)
        return X, y

    return pd.DataFrame(), np.array([])


# =============== Treino ===============
def build_model():
    """
    Pipeline simples: StandardScaler (opcional) + LGBMClassifier
    Obs: LightGBM não exige padronização, mas manter escalador ajuda quando
    misturamos features com escalas muito diferentes.
    """
    clf = LGBMClassifier(
        n_estimators=400,
        learning_rate=0.05,
        max_depth=-1,
        subsample=0.85,
        colsample_bytree=0.85,
        random_state=RANDOM_STATE,
        n_jobs=-1,
        class_weight="balanced"
    )
    pipe = Pipeline(steps=[
        ("scaler", StandardScaler(with_mean=False)),  # robusto a esparsidade / colunas constantes
        ("lgbm", clf)
    ])
    return pipe


def evaluate_and_log(y_true, y_proba):
    """
    Calcula métricas padrão e imprime.
    """
    y_pred = (y_proba >= 0.5).astype(int)

    acc  = accuracy_score(y_true, y_pred)
    f1   = f1_score(y_true, y_pred, zero_division=0)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec  = recall_score(y_true, y_pred, zero_division=0)
    try:
        auc  = roc_auc_score(y_true, y_proba)
    except Exception:
        auc = float("nan")

    _log(f"🎯 Métricas — ACC={acc:.3f} | F1={f1:.3f} | PREC={prec:.3f} | REC={rec:.3f} | AUC={auc:.3f}")
    return {"accuracy": acc, "f1": f1, "precision": prec, "recall": rec, "auc": auc}


def train_and_save():
    X, y = load_training_data()

    if len(y) == 0:
        _log("🟡 Nenhuma amostra encontrada no histórico. Abortando treino.")
        return False

    _log(f"📦 Amostras carregadas: {len(y)} | Features: {X.shape[1]}")

    if len(y) < TRAIN_MIN_SAMPLES:
        _log(f"⏳ Aguardando volume mínimo para treino: {len(y)}/{TRAIN_MIN_SAMPLES}")
        return False

    # Partição estratificada
    try:
        X_train, X_val, y_train, y_val = train_test_split(
            X, y, test_size=0.2, random_state=RANDOM_STATE, stratify=y
        )
    except ValueError:
        # Se houver apenas uma classe, usa hold-out simples
        X_train, X_val, y_train, y_val = train_test_split(
            X, y, test_size=0.2, random_state=RANDOM_STATE
        )

    model = build_model()
    _log("⚙️  Treinando LightGBM…")
    model.fit(X_train, y_train)

    # Avaliação
    y_val_proba = model.predict_proba(X_val)[:, 1]
    metrics = evaluate_and_log(y_val, y_val_proba)

    # Salva modelo
    dump(model, MODEL_FILE)
    _log(f"💾 Modelo salvo em: {MODEL_FILE}")

    # Metadados do modelo
    meta = {
        "saved_at": _now_ts(),
        "n_samples": int(len(y)),
        "n_features": int(X.shape[1]),
        "features": list(X.columns),
        "metrics": metrics,
        "random_state": RANDOM_STATE,
        "train_min_samples": TRAIN_MIN_SAMPLES,
        "lib": "lightgbm",
        "version_hint": "4.5.0"
    }
    with open(os.path.splitext(MODEL_FILE)[0] + "_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    _log("🧾 Metadados salvos ( *_meta.json ).")

    return True


if __name__ == "__main__":
    ok = train_and_save()
    if ok:
        _log("✅ Treino concluído com sucesso.")
    else:
        _log("ℹ️ Treino não executado (ver acima).")
