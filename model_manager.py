# -*- coding: utf-8 -*-
"""
model_manager.py — IA "tech-only" (sem depender de notícias)
- Extrai features simples e robustas de OHLC
- Registra snapshots para treino (label preenchido depois)
- Rotula automático (quando horizonte futuro já passou)
- Treina e salva modelo (LightGBM se disponível, senão LogisticRegression)
- Prediz probabilidade "bom trade" para misturar com score técnico

Arquivos:
- DATA_DIR/features.csv       -> dataset incremental
- AI_MODEL_PATH (env)         -> modelo salvo (joblib/pkl)
- DATA_DIR/feature_list.json  -> ordem das features usada no treino

Depêndencias: lightgbm (opcional, recomendado), scikit-learn, joblib, numpy, pandas
"""

import os, json, time, math, csv
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

import numpy as np

try:
    import joblib
except Exception:
    joblib = None

# Modelo: tenta LightGBM, senão cai para LogisticRegression
_LGB_OK = True
try:
    import lightgbm as lgb
except Exception:
    _LGB_OK = False
from sklearn.linear_model import LogisticRegression

# =========================
# ENV & Caminhos
# =========================
DATA_DIR            = os.getenv("AI_DATA_DIR", "data/ai")
AI_MODEL_PATH       = os.getenv("AI_MODEL_PATH", "models/tech_only_clf.pkl")
AI_TRAIN_PERIOD_D   = int(os.getenv("AI_TRAIN_PERIOD_DAYS", "120"))
AI_PRED_HORIZON_H   = int(os.getenv("AI_PRED_HORIZON_H", "4"))
AI_LABEL_RET_PCT    = float(os.getenv("AI_LABEL_RET_PCT", "1.0"))   # +1.0% alvo
AI_AUTOTRAIN        = os.getenv("AI_AUTOTRAIN", "false").lower() in ("1","true","yes")
HISTORY_DIR         = os.getenv("HISTORY_DIR", "data/history")

FEATURES_CSV        = os.path.join(DATA_DIR, "features.csv")
FEATURE_LIST_JSON   = os.path.join(DATA_DIR, "feature_list.json")

os.makedirs(os.path.dirname(AI_MODEL_PATH), exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# =========================
# Utils
# =========================
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(AI_MODEL_PATH), exist_ok=True)

def _norm_rows(raw) -> List[Dict[str, float]]:
    """Normaliza para lista de dicts {t,o,h,l,c}."""
    out = []
    if not raw:
        return out
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        for r in raw:
            if len(r) >= 5:
                out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]),
                            "l": float(r[3]), "c": float(r[4])})
    elif isinstance(raw, list) and isinstance(raw[0], dict):
        for r in raw:
            t = float(r.get("t", r.get("time", 0.0)))
            o = float(r.get("o", r.get("open", 0.0)))
            h = float(r.get("h", r.get("high", 0.0)))
            l = float(r.get("l", r.get("low", 0.0)))
            c = float(r.get("c", r.get("close", 0.0)))
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    return out

def _sma(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) < n: return np.full_like(arr, np.nan, dtype=float)
    csum = np.cumsum(arr, dtype=float)
    csum[n:] = csum[n:] - csum[:-n]
    out = csum[n-1:] / n
    pad = np.full(n-1, np.nan)
    return np.concatenate([pad, out])

def _ema(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) == 0: return np.array([])
    alpha = 2.0 / (n + 1.0)
    out = np.zeros_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1-alpha) * out[i-1]
    return out

def _rsi(arr: np.ndarray, n: int = 14) -> np.ndarray:
    if len(arr) < n+1:
        return np.full_like(arr, np.nan, dtype=float)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.zeros_like(arr)
    avg_loss = np.zeros_like(arr)
    avg_gain[n] = gains[:n].mean()
    avg_loss[n] = losses[:n].mean()
    for i in range(n+1, len(arr)):
        avg_gain[i] = (avg_gain[i-1]*(n-1) + gains[i-1]) / n
        avg_loss[i] = (avg_loss[i-1]*(n-1) + losses[i-1]) / n
    rs = np.divide(avg_gain, avg_loss, out=np.zeros_like(avg_gain), where=avg_loss!=0)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi[:n] = np.nan
    return rsi

def _std(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) < n: return np.full_like(arr, np.nan, dtype=float)
    out = np.zeros_like(arr, dtype=float)
    for i in range(len(arr)):
        if i < n-1:
            out[i] = np.nan
        else:
            out[i] = float(np.std(arr[i-n+1:i+1]))
    return out

def _pct(a: float, b: float) -> float:
    try:
        if b == 0: return 0.0
        return (a / b - 1.0) * 100.0
    except Exception:
        return 0.0

# =========================
# Feature extraction
# =========================
_FEATURE_ORDER = [
    "ret_1", "ret_4", "ret_12", "ret_24",
    "sma20_rel", "sma50_rel",
    "ema20_rel", "ema50_rel",
    "bb_width",
    "vol_20",
    "mom_10",
    "range_14"
]

def extract_features_from_ohlc(ohlc_rows: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    """
    Extrai features simples (robustas e rápidas) a partir de {t,o,h,l,c}.
    Retorna dict com as chaves em _FEATURE_ORDER.
    """
    rows = _norm_rows(ohlc_rows)
    if not rows or len(rows) < 60:
        return None

    closes = np.array([r["c"] for r in rows], dtype=float)
    highs  = np.array([r["h"] for r in rows], dtype=float)
    lows   = np.array([r["l"] for r in rows], dtype=float)

    # returns discretos (~1h)
    def _ret(k: int) -> float:
        if len(closes) <= k: return 0.0
        return _pct(closes[-1], closes[-1-k])

    ret_1   = _ret(1)
    ret_4   = _ret(4)
    ret_12  = _ret(12)
    ret_24  = _ret(24)

    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)

    sma20_rel = _pct(closes[-1], sma20[-1]) if not math.isnan(sma20[-1]) else 0.0
    sma50_rel = _pct(closes[-1], sma50[-1]) if not math.isnan(sma50[-1]) else 0.0
    ema20_rel = _pct(closes[-1], ema20[-1]) if len(ema20) else 0.0
    ema50_rel = _pct(closes[-1], ema50[-1]) if len(ema50) else 0.0

    # BB width ~ 2*std20 / sma20
    std20 = _std(closes, 20)
    if not math.isnan(std20[-1]) and not math.isnan(sma20[-1]) and sma20[-1] != 0:
        bb_width = (2.0 * std20[-1]) / sma20[-1]
    else:
        bb_width = 0.0

    # volatilidade simples de retornos (20)
    rets = np.diff(closes) / np.maximum(1e-9, closes[:-1])
    if len(rets) >= 20:
        vol_20 = float(np.std(rets[-20:]))
    else:
        vol_20 = float(np.std(rets)) if len(rets) > 0 else 0.0

    # momentum e amplitude recente
    mom_10 = _pct(closes[-1], closes[-10]) if len(closes) > 10 else 0.0
    if len(highs) >= 14 and len(lows) >= 14:
        rng = (np.max(highs[-14:]) - np.min(lows[-14:])) / max(1e-9, closes[-1])
    else:
        rng = 0.0

    feats = {
        "ret_1": ret_1, "ret_4": ret_4, "ret_12": ret_12, "ret_24": ret_24,
        "sma20_rel": sma20_rel, "sma50_rel": sma50_rel,
        "ema20_rel": ema20_rel, "ema50_rel": ema50_rel,
        "bb_width": float(bb_width),
        "vol_20": vol_20,
        "mom_10": mom_10,
        "range_14": float(rng),
    }
    return feats

# =========================
# Dataset (CSV) & Labeling
# =========================
def _write_csv_header_if_needed(path: str, fieldnames: List[str]):
    exists = os.path.exists(path)
    f = open(path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    if not exists:
        writer.writeheader()
    return f, writer

def append_snapshot_for_training(symbol: str, ohlc_rows: List[Dict[str, float]],
                                 horizon_h: int = None, ret_target_pct: float = None) -> None:
    """
    Registra um snapshot com label em branco para ser preenchido depois.
    """
    try:
        horizon_h = horizon_h if horizon_h is not None else AI_PRED_HORIZON_H
        ret_target_pct = ret_target_pct if ret_target_pct is not None else AI_LABEL_RET_PCT
        feats = extract_features_from_ohlc(ohlc_rows)
        if feats is None:
            return
        rows = _norm_rows(ohlc_rows)
        snap_ts = float(rows[-1]["t"])
        future_ts = snap_ts + horizon_h * 3600.0

        row = {
            "timestamp": snap_ts,
            "future_ts": future_ts,
            "symbol": symbol,
            "label": "",  # será preenchido depois
            "ret_target_pct": ret_target_pct
        }
        for k in _FEATURE_ORDER:
            row[k] = feats.get(k, 0.0)

        # salva
        fields = ["timestamp", "future_ts", "symbol", "label", "ret_target_pct"] + _FEATURE_ORDER
        f, writer = _write_csv_header_if_needed(FEATURES_CSV, fields)
        with f:
            writer.writerow(row)
    except Exception as e:
        print(f"[AI] append_snapshot erro {symbol}: {e}")

def _load_hist_ohlc(symbol: str) -> List[Dict[str, float]]:
    """
    Lê HISTORY_DIR/ohlc/{symbol}.json (estruturas: lista ou {'bars':[...]}).
    """
    try:
        p = os.path.join(HISTORY_DIR, "ohlc", f"{symbol}.json")
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict) and "bars" in obj:
            return _norm_rows(obj["bars"])
        if isinstance(obj, list):
            return _norm_rows(obj)
        return []
    except Exception:
        return []

def _close_at_or_after(bars: List[Dict[str, float]], target_ts: float) -> Optional[float]:
    for r in bars:
        if float(r["t"]) >= target_ts:
            return float(r["c"])
    return None

def update_labels(horizon_h: int = None, ret_target_pct: float = None, max_rows: int = 100000) -> Tuple[int,int]:
    """
    Preenche labels vazios quando o 'future_ts' já passou e existe OHLC suficiente.
    Label = 1 se (close_future / close_snap - 1) >= ret_target_pct/100, senão 0.
    """
    horizon_h = horizon_h if horizon_h is not None else AI_PRED_HORIZON_H
    ret_target_pct = ret_target_pct if ret_target_pct is not None else AI_LABEL_RET_PCT

    if not os.path.exists(FEATURES_CSV):
        return (0,0)

    # ler tudo (se grande, pode otimizar por chunks)
    with open(FEATURES_CSV, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    updated, total_blank = 0, 0
    now = time.time()

    for r in rows:
        try:
            if str(r.get("label","")).strip() != "":
                continue
            total_blank += 1
            sym = r["symbol"]
            snap_ts = float(r["timestamp"])
            fts = float(r["future_ts"])
            if now < fts:
                continue  # ainda não chegou o futuro

            bars = _load_hist_ohlc(sym)
            if not bars:
                continue
            # close no snapshot (aproxima pelo bar mais próximo <= snap_ts)
            c0 = None
            prev = None
            for b in bars:
                if float(b["t"]) <= snap_ts:
                    prev = b
                else:
                    break
            c0 = float(prev["c"]) if prev else None
            if c0 is None or c0 == 0:
                continue

            cfut = _close_at_or_after(bars, fts)
            if cfut is None:
                continue

            ret_pct = (cfut / c0 - 1.0) * 100.0
            lab = 1 if ret_pct >= ret_target_pct else 0
            r["label"] = str(int(lab))
        except Exception:
            continue

    # regrava arquivo
    fields = rows[0].keys() if rows else ["timestamp","future_ts","symbol","label","ret_target_pct"] + _FEATURE_ORDER
    with open(FEATURES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields))
        writer.writeheader()
        for r in rows[:max_rows]:
            writer.writerow(r)

    updated = sum(1 for r in rows if str(r.get("label","")).strip() in ("0","1"))
    return (updated, total_blank)

# =========================
# Train / Load / Predict
# =========================
def _load_dataset(period_days: int) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    if not os.path.exists(FEATURES_CSV):
        return np.zeros((0,len(_FEATURE_ORDER))), np.zeros((0,)), _FEATURE_ORDER[:]

    import pandas as pd
    df = pd.read_csv(FEATURES_CSV)

    # filtros
    df = df[df["label"].isin([0,1,"0","1"])]
    if df.empty:
        return np.zeros((0,len(_FEATURE_ORDER))), np.zeros((0,)), _FEATURE_ORDER[:]

    # período
    try:
        df["timestamp_dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    except Exception:
        # timestamp pode já estar em segundos float
        df["timestamp_dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True, errors="coerce")
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=period_days)
    df = df[df["timestamp_dt"] >= cutoff]
    if df.empty:
        return np.zeros((0,len(_FEATURE_ORDER))), np.zeros((0,)), _FEATURE_ORDER[:]

    X = df[_FEATURE_ORDER].astype(float).values
    y = df["label"].astype(int).values
    return X, y, _FEATURE_ORDER[:]

def train_and_save(model_path: str = None, period_days: int = None, min_rows: int = 1000) -> Optional[str]:
    model_path = model_path or AI_MODEL_PATH
    period_days = period_days or AI_TRAIN_PERIOD_D
    X, y, feats = _load_dataset(period_days)
    n = X.shape[0]
    if n < max(200, min_rows):
        print(f"[AI] Dados insuficientes para treino: {n} < {max(200,min_rows)}")
        return None

    # treina
    if _LGB_OK:
        dtrain = lgb.Dataset(X, label=y)
        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "verbosity": -1,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "feature_fraction": 0.9,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "min_data_in_leaf": 20
        }
        clf = lgb.train(params, dtrain, num_boost_round=400)
    else:
        clf = LogisticRegression(max_iter=500)
        clf.fit(X, y)

    # salva
    if joblib:
        joblib.dump({"model": clf, "features": feats}, model_path)
    else:
        import pickle
        with open(model_path, "wb") as f:
            pickle.dump({"model": clf, "features": feats}, f)

    with open(FEATURE_LIST_JSON, "w", encoding="utf-8") as f:
        json.dump({"features": feats}, f)

    print(f"[AI] Modelo salvo em {model_path} (n={n}, alg={'LGBM' if _LGB_OK else 'LogReg'})")
    return model_path

def load_or_none(model_path: str = None):
    model_path = model_path or AI_MODEL_PATH
    if not os.path.exists(model_path):
        return None
    try:
        if joblib:
            pack = joblib.load(model_path)
        else:
            import pickle
            with open(model_path, "rb") as f:
                pack = pickle.load(f)
        return pack  # {"model": clf, "features": [...]}
    except Exception as e:
        print(f"[AI] Falha ao carregar modelo: {e}")
        return None

def predict_proba_single(model_pack: Dict[str, Any], feats: Dict[str, float]) -> Optional[float]:
    try:
        if not model_pack:
            return None
        model = model_pack["model"]
        feat_order = model_pack.get("features", _FEATURE_ORDER)
        x = np.array([[float(feats.get(k, 0.0)) for k in feat_order]], dtype=float)
        if hasattr(model, "predict_proba"):
            proba = float(model.predict_proba(x)[0][1])
        else:
            # lgb.Booster retorna margens -> usa sigmoid
            margin = float(model.predict(x)[0])
            proba = 1.0 / (1.0 + math.exp(-margin))
        return max(0.0, min(1.0, proba))
    except Exception as e:
        print(f"[AI] predict erro: {e}")
        return None
