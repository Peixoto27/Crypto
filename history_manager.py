# -*- coding: utf-8 -*-
"""
history_manager.py
- Guarda cada sinal emitido com features e rótulo (hit_tp / hit_sl / open).
- Tenta rotular automaticamente sinais "antigos" consultando OHLC posterior.
- Fornece dataset para o trainer.
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional

# compat: nomes via ENV
HISTORY_FILE = os.getenv("HISTORY_FILE", "history.json")
AUTO_LABEL_LOOKAHEAD_HOURS = float(os.getenv("AUTO_LABEL_LOOKAHEAD_HOURS", "48"))

# vamos usar o fetch_ohlc existente
try:
    from data_fetcher_coingecko import fetch_ohlc
except Exception:
    # fallback bobo: retorna lista vazia
    def fetch_ohlc(symbol: str, days: int):
        return []

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _ensure_file(path: str, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)

_ensure_file(HISTORY_FILE, [])

def load_history() -> List[Dict[str, Any]]:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_history(rows: List[Dict[str, Any]]) -> None:
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

def record_signal(sig: Dict[str, Any]) -> None:
    """Guarda o sinal com features para treinamento futuro."""
    rows = load_history()
    # campos mínimos
    row = {
        "id": sig.get("id"),
        "symbol": sig.get("symbol"),
        "created_at": sig.get("created_at", _ts()),
        "entry": sig.get("entry"),
        "tp": sig.get("tp"),
        "sl": sig.get("sl"),
        "rr": sig.get("rr"),
        "strategy": sig.get("strategy"),
        "confidence": sig.get("confidence"),
        "tech_score": sig.get("tech_score"),      # score técnico do momento
        "sent_score": sig.get("sent_score"),      # score sentimento 0..1
        "mix_score": sig.get("mix_score"),        # score misto usado p/ decisão
        "features": sig.get("features", {}),      # indicadores brutos
        "label": "open",                          # open | hit_tp | hit_sl | expired
        "closed_at": None
    }
    rows.append(row)
    save_history(rows)

def _parse_utc(s: str) -> datetime:
    # "YYYY-MM-DD HH:MM:SS UTC"
    return datetime.strptime(s.replace(" UTC",""), "%Y-%m-%d %H:%M:%S")

def _decide_outcome(entry: float, tp: float, sl: float, ohlc: List[List[float]]) -> str:
    """
    Dado OHLC após o sinal:
    - Se low <= sl antes de high >= tp -> 'hit_sl'
    - Se high >= tp antes de low <= sl -> 'hit_tp'
    - Caso nenhum em toda janela -> 'open'
    Espera ohlc como [[ts, o, h, l, c], ...] em ordem crescente.
    """
    for _, _, h, l, _ in ohlc:
        if l is not None and sl is not None and l <= sl:
            return "hit_sl"
        if h is not None and tp is not None and h >= tp:
            return "hit_tp"
    return "open"

def evaluate_pending_outcomes(verbose: bool = True) -> Tuple[int, int]:
    """
    Rotula automaticamente sinais antigos (>= lookahead horas).
    Retorna (avaliados, fechados).
    """
    rows = load_history()
    if not rows:
        return (0, 0)

    now = datetime.utcnow()
    horizon = timedelta(hours=AUTO_LABEL_LOOKAHEAD_HOURS)
    eval_count, close_count = 0, 0

    for r in rows:
        if r.get("label") != "open":
            continue
        created = _parse_utc(r["created_at"])
        if now - created < horizon:
            continue  # ainda cedo

        eval_count += 1
        # quantos dias pegar? horizon horas -> ceil(dias)
        days = max(1, int((AUTO_LABEL_LOOKAHEAD_HOURS / 24.0) + 1))
        try:
            after = fetch_ohlc(r["symbol"], days)
        except Exception:
            after = []

        # manter apenas candles *depois* do sinal
        ts0 = int(created.timestamp()) * 1000
        after = [k for k in after if (isinstance(k, list) and len(k) >= 5 and k[0] >= ts0)]

        if not after:
            r["label"] = "expired"
            r["closed_at"] = _ts()
            close_count += 1
            continue

        outcome = _decide_outcome(r.get("entry"), r.get("tp"), r.get("sl"), after)
        r["label"] = outcome
        r["closed_at"] = _ts()
        close_count += 1

    if close_count:
        save_history(rows)

    if verbose and eval_count:
        print(f"📘 history: avaliados={eval_count}, fechados={close_count}")
    return (eval_count, close_count)

def get_training_dataset(min_samples: int = 100) -> Tuple[List[List[float]], List[int], List[str]]:
    """
    Retorna (X, y, feature_names)
      - X: lista de vetores de features
      - y: 1 se hit_tp, 0 se hit_sl (descarta 'open' e 'expired')
      - feature_names: nomes na mesma ordem de X
    """
    rows = load_history()
    X, y = [], []

    # defina a ordem das features (alinhada ao que o main salva)
    feat_order = [
        "rsi", "macd", "macd_sig", "macd_hist",
        "ema20", "ema50",
        "bb_mid", "bb_hi", "bb_lo",
        "stochK", "stochD",
        "adx", "pdi", "mdi",
        "atr_rel",
        "cci"
    ]

    for r in rows:
        if r.get("label") not in ("hit_tp", "hit_sl"):
            continue
        f: Dict[str, Any] = r.get("features", {})
        vec = []
        ok = True
        for k in feat_order:
            v = f.get(k, None)
            if v is None:
                ok = False
                break
            vec.append(float(v))
        if not ok:
            continue
        X.append(vec)
        y.append(1 if r["label"] == "hit_tp" else 0)

    if len(X) < min_samples:
        return [], [], feat_order
    return X, y, feat_order
