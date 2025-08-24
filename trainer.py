import os
import json
import math
import warnings
from typing import List, Dict, Any, Tuple

import numpy as np
import pandas as pd

# Fallbacks em cadeia para garantir treino mesmo sem libs “pesadas”
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.dummy import DummyClassifier
import joblib

warnings.filterwarnings("ignore")

# =========================
# Config via ambiente
# =========================
MODEL_FILE = os.getenv("MODEL_FILE", "model.pkl")
TRAIN_MIN_SAMPLES = int(os.getenv("TRAIN_MIN_SAMPLES", "200"))
RANDOM_STATE = int(os.getenv("RANDOM_STATE", "42"))

# =========================
# Utilidades de log
# =========================
def log(msg: str):
    print(msg, flush=True)

# =========================
# Leitura de dados
# =========================
def load_signals(path: str = "signals.json") -> pd.DataFrame:
    """
    signals.json (se existir) deve ter uma lista de dicts com, por ex:
    { "symbol": "BTCUSDT", "ts": 1690000000, "close": 67000, ... , "label": 0/1 }
    Não é obrigatório. Se não existir, retorna DF vazio.
    """
    if not os.path.exists(path):
        return pd.DataFrame()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return pd.DataFrame()

    if not isinstance(data, list) or len(data) == 0:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    # normaliza colunas esperadas
    if "symbol" not in df.columns: df["symbol"] = "UNK"
    if "ts" not in df.columns and "time" in df.columns: df["ts"] = df["time"]
    return df


def load_data_raw(path: str = "data_raw.json") -> Dict[str, Any]:
    """
    data_raw.json esperado no formato:
    { "BTCUSDT": [{"t": 169..., "o":..., "h":..., "l":..., "c":..., "v":...}, ...], "ETHUSDT": [...], ... }
    Retorna dict simb -> lista de candles
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


# =========================
# Engenharia de features
# =========================
def candles_to_df(symbol: str, candles: List[Dict[str, Any]]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()

    # aceita chaves variadas (t/time, o/open, h/high, l/low, c/close, v/volume)
    def g(x, *keys, default=None):
        for k in keys:
            if k in x:
                return x[k]
        return default

    rows = []
    for c in candles:
        rows.append({
            "symbol": symbol,
            "ts": g(c, "t", "time"),
            "open": float(g(c, "o", "open", default=np.nan)),
            "high": float(g(c, "h", "high", default=np.nan)),
            "low": float(g(c, "l", "low", default=np.nan)),
            "close": float(g(c, "c", "close", default=np.nan)),
            "volume": float(g(c, "v", "volume", default=np.nan)),
        })
    df = pd.DataFrame(rows).dropna()
    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)
    return df


def add_tech_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    # retornos
    df["ret_1"] = df["close"].pct_change(1)
    df["ret_3"] = df["close"].pct_change(3)
    df["ret_5"] = df["close"].pct_change(5)

    # volatilidade
    df["vol_10"] = df["ret_1"].rolling(10).std()
    df["vol_20"] = df["ret_1"].rolling(20).std()

    # médias e razões
    df["sma_5"] = df["close"].rolling(5).mean()
    df["sma_10"] = df["close"].rolling(10).mean()
    df["sma_20"] = df["close"].rolling(20).mean()
    df["sma_5_over_20"] = df["sma_5"] / df["sma_20"]
    df["sma_10_over_20"] = df["sma_10"] / df["sma_20"]

    # candle features
    df["body"] = (df["close"] - df["open"]).abs()
    df["range"] = (df["high"] - df["low"]).replace(0, np.nan)
    df["body_over_range"] = df["body"] / df["range"]

    # alvo: próximo retorno > 0
    df["target"] = (df["close"].shift(-1) / df["close"] - 1.0) > 0
    df["target"] = df["target"].astype(float)  # 1.0 ou 0.0

    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    return df


def build_dataset(data_raw: Dict[str, Any]) -> pd.DataFrame:
    frames = []
    for sym, candles in data_raw.items():
        d = candles_to_df(sym, candles)
        d = add_tech_features(d)
        if not d.empty:
            frames.append(d)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df


# =========================
# Treino
# =========================
def get_feature_columns(df: pd.DataFrame) -> List[str]:
    base_cols = [
        "ret_1", "ret_3", "ret_5",
        "vol_10", "vol_20",
        "sma_5_over_20", "sma_10_over_20",
        "body_over_range",
        "volume"
    ]
    return [c for c in base_cols if c in df.columns]


def train_model(X: pd.DataFrame, y: pd.Series):
    """
    Cadeia de fallbacks:
    1) LightGBM (se instalado)
    2) XGBoost (se instalado)
    3) LogisticRegression
    4) DummyClassifier
    Sempre retorna um estimador com predict_proba.
    """
    # tenta LightGBM
    try:
        import lightgbm as lgb
        clf = lgb.LGBMClassifier(
            n_estimators=400,
            learning_rate=0.05,
            max_depth=-1,
            subsample=0.9,
            colsample_bytree=0.8,
            objective="binary",
            random_state=RANDOM_STATE
        )
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", clf),
        ])
        pipe.fit(X, y)
        return pipe
    except Exception:
        pass

    # tenta XGBoost
    try:
        import xgboost as xgb
        clf = xgb.XGBClassifier(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.9,
            colsample_bytree=0.8,
            objective="binary:logistic",
            eval_metric="logloss",
            random_state=RANDOM_STATE,
            n_jobs=2,
        )
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", clf),
        ])
        pipe.fit(X, y)
        return pipe
    except Exception:
        pass

    # LogisticRegression
    try:
        clf = LogisticRegression(max_iter=200, random_state=RANDOM_STATE)
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", clf),
        ])
        pipe.fit(X, y)
        return pipe
    except Exception:
        pass

    # Dummy de fallback (sempre retorna probabilidade constante)
    dummy = DummyClassifier(strategy="prior", random_state=RANDOM_STATE)
    dummy.fit(X, y)
    pipe = Pipeline([("clf", dummy)])
    return pipe


class AIPredictor:
    """
    Wrapper salvo no model.pkl para garantir compatibilidade
    com main: possui predict_proba(X) e predict(X).
    Também salva o nome das colunas de features.
    """
    def __init__(self, pipeline, feature_cols: List[str]):
        self.pipeline = pipeline
        self.feature_cols = feature_cols

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        # seleciona e ordena colunas esperadas; falta -> preenche com 0
        X = pd.DataFrame(index=df.index)
        for c in self.feature_cols:
            X[c] = df[c] if c in df.columns else 0.0
        return X

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        X = self.prepare(df)
        # alguns estimadores (LR/LGBM) têm predict_proba; Dummy também
        if hasattr(self.pipeline, "predict_proba"):
            return self.pipeline.predict_proba(X)
        # fallback: usa decisão como prob
        preds = self.pipeline.predict(X)
        preds = np.clip(preds.astype(float), 0, 1)
        return np.vstack([1 - preds, preds]).T

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        proba = self.predict_proba(df)
        return (proba[:, 1] >= 0.5).astype(int)


def bootstrap_if_needed(df: pd.DataFrame, min_rows: int) -> pd.DataFrame:
    """
    Se houver poucas amostras, replica com jitter leve para permitir um treino inicial.
    """
    if len(df) >= min_rows:
        return df

    if df.empty:
        return df

    reps = math.ceil(min_rows / max(1, len(df)))
    out = [df]
    for _ in range(reps - 1):
        jitter = df.copy()
        # ruído pequeno em features contínuas
        for col in ["ret_1", "ret_3", "ret_5", "vol_10", "vol_20",
                    "sma_5_over_20", "sma_10_over_20", "body_over_range", "volume"]:
            if col in jitter.columns:
                std = (jitter[col].std() or 1e-6)
                noise = np.random.normal(0, std * 0.05, size=len(jitter))
                jitter[col] = jitter[col] + noise
        out.append(jitter)
    boot = pd.concat(out, ignore_index=True)
    return boot.sample(frac=1.0, random_state=RANDOM_STATE).reset_index(drop=True)

def main():
    log("🔧 Iniciando treino de IA…")

    # 1) Carrega dados
    sig_df = load_signals("signals.json")
    raw = load_data_raw("data_raw.json")
    feat_df = build_dataset(raw)

    # Se signals.json tiver label explícito, prioriza-o
    if not sig_df.empty and "label" in sig_df.columns:
        # quando existir close/open/high/low/volume, criamos features mínimas
        if {"close", "open", "high", "low", "volume"}.issubset(sig_df.columns):
            tmp = sig_df.rename(columns={"time": "ts"})
            tmp = tmp.sort_values("ts").reset_index(drop=True)
            tmp = add_tech_features(tmp)
            # substitui o target pelo label pronto
            common = set(tmp.columns)
            tmp = tmp[list(common)]
            tmp["target"] = sig_df["label"].astype(float).values[:len(tmp)]
            feat_df = pd.concat([feat_df, tmp], ignore_index=True) if not feat_df.empty else tmp

    if feat_df.empty:
        log("⚠️ Sem dados suficientes em data_raw.json/signals.json para treinar. "
            "Salvando modelo dummy (prob=0.5)…")
        # Cria um DF mínimo para encaixar o pipeline
        feat_df = pd.DataFrame({
            "ret_1": [0.0, 0.01, -0.01],
            "ret_3": [0.0, 0.02, -0.02],
            "ret_5": [0.0, 0.03, -0.03],
            "vol_10": [0.01, 0.01, 0.01],
            "vol_20": [0.01, 0.01, 0.01],
            "sma_5_over_20": [1.0, 1.01, 0.99],
            "sma_10_over_20": [1.0, 1.01, 0.99],
            "body_over_range": [0.5, 0.5, 0.5],
            "volume": [1.0, 1.0, 1.0],
            "target": [0.0, 1.0, 0.0],
        })

    # 2) Seleciona features e alvo
    feat_cols = get_feature_columns(feat_df)
    if not feat_cols:
        log("⚠️ Não foi possível montar features. Usando colunas padrão zeradas.")
        feat_cols = ["ret_1","ret_3","ret_5","vol_10","vol_20","sma_5_over_20","sma_10_over_20","body_over_range","volume"]
        for c in feat_cols:
            if c not in feat_df.columns:
                feat_df[c] = 0.0
        if "target" not in feat_df.columns:
            feat_df["target"] = 0.0

    feat_df = feat_df.dropna(subset=feat_cols + ["target"]).reset_index(drop=True)
    feat_df = bootstrap_if_needed(feat_df, max(50, min(TRAIN_MIN_SAMPLES, 500)))

    X = feat_df[feat_cols].copy()
    y = feat_df["target"].astype(int).copy()

    # 3) Split rápido p/ métrica e treino
    try:
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=RANDOM_STATE, stratify=y)
    except Exception:
        Xtr, Xte, ytr, yte = X, X, y, y

    model = train_model(Xtr, ytr)

    # 4) Métrica opcional
    try:
        proba = getattr(model, "predict_proba")(Xte)[:, 1]
        auc = roc_auc_score(yte, proba)
        log(f"✅ Treino concluído | AUC={auc:.3f} | amostras={len(X)} | features={len(feat_cols)}")
    except Exception:
        log(f"✅ Treino concluído (sem AUC) | amostras={len(X)} | features={len(feat_cols)}")

    # 5) Salvar wrapper
    wrapper = AIPredictor(model, feat_cols)
    joblib.dump(wrapper, MODEL_FILE)
    log(f"💾 Modelo salvo em {MODEL_FILE} (columns={feat_cols})")

    # salva metadados (lista de features) ao lado do modelo para uso em produção
    try:
        meta_path = os.path.splitext(MODEL_FILE)[0] + "_meta.json"
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump({"features": feat_cols}, mf, ensure_ascii=False, indent=2)
        log(f"💾 Metadados salvos em {meta_path}")
    except Exception as e:
        log(f"⚠️ Falha ao salvar metadados: {e}")


if __name__ == "__main__":
    main()
