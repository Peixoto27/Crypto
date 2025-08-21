# -*- coding: utf-8 -*-
"""
history_manager.py — utilitários de histórico/caching
- Cache de OHLC por símbolo (load/save)
- Persistência de amostras de treino (opcional)
"""

import os
import json
from datetime import datetime
from typing import List, Dict, Any

HISTORY_DIR = os.getenv("HISTORY_DIR", "data/history")
SAMPLES_DIR = os.path.join(HISTORY_DIR, "samples")
OHLC_DIR    = os.path.join(HISTORY_DIR, "ohlc")

def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

# -----------------------------
# Normalização de OHLC
# -----------------------------
def _norm_ohlc_rows(rows: List[Any]) -> List[List[float]]:
    """
    Normaliza para lista de listas: [[ts,o,h,l,c], ...]
    Aceita:
      - [[ts,o,h,l,c], ...]
      - [{"t":..,"o":..,"h":..,"l":..,"c":..}, ...]
      - [{"time":..,"open":..,"high":..,"low":..,"close":..}, ...]
    """
    out: List[List[float]] = []
    if not rows:
        return out

    if isinstance(rows[0], list) and len(rows[0]) >= 5:
        for r in rows:
            try:
                out.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])])
            except Exception:
                continue
        return out

    if isinstance(rows[0], dict):
        for r in rows:
            t = r.get("t", r.get("time", r.get("timestamp", 0)))
            o = r.get("o", r.get("open", 0))
            h = r.get("h", r.get("high", 0))
            l = r.get("l", r.get("low", 0))
            c = r.get("c", r.get("close", 0))
            try:
                out.append([float(t), float(o), float(h), float(l), float(c)])
            except Exception:
                continue
        return out

    return out

# -----------------------------
# Cache OHLC por símbolo
# -----------------------------
def save_ohlc_cache(history_dir: str, symbol: str, bars: List[Any]) -> bool:
    """
    Salva em {history_dir}/ohlc/{SYMBOL}.json no formato:
      {"symbol":"BTCUSDT","bars":[ [ts,o,h,l,c], ... ], "saved_at": "...UTC" }
    """
    try:
        _ensure_dir(os.path.join(history_dir, "ohlc"))
        norm = _norm_ohlc_rows(bars)
        path = os.path.join(history_dir, "ohlc", f"{symbol}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"symbol": symbol, "bars": norm, "saved_at": _ts()}, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def load_ohlc_cache(history_dir: str, symbol: str) -> List[List[float]]:
    """
    Lê {history_dir}/ohlc/{SYMBOL}.json e retorna [[ts,o,h,l,c], ...] ou [].
    """
    path = os.path.join(history_dir, "ohlc", f"{symbol}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        bars = obj.get("bars", [])
        return _norm_ohlc_rows(bars)
    except Exception:
        return []

# -----------------------------
# Amostras de treino (opcional)
# -----------------------------
def append_sample(features: Dict[str, float], label: int) -> bool:
    """
    Acrescenta uma amostra em JSONL: {HISTORY_DIR}/samples/YYYYMMDD.jsonl
    Cada linha: {"ts":"...UTC","features":{...},"label":0/1}
    """
    try:
        _ensure_dir(SAMPLES_DIR)
        fname = datetime.utcnow().strftime("%Y%m%d") + ".jsonl"
        path = os.path.join(SAMPLES_DIR, fname)
        rec = {"ts": _ts(), "features": features, "label": int(label)}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False
