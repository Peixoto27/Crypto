# -*- coding: utf-8 -*-
"""
history_manager.py — utilitários de histórico/caches locais

Fornece:
- ensure_dir(path)
- save_ohlc_symbol(symbol, bars, history_dir="data/history")
    -> salva em HISTORY_DIR/ohlc/{SYMBOL}.json
    -> formato: {"symbol": "...", "bars": [[ts,o,h,l,c], ...]}
- load_ohlc_symbol(symbol, history_dir="data/history") -> lista de dicts padronizados
"""

import os
import json
from typing import List, Dict

DEFAULT_HISTORY_DIR = os.getenv("HISTORY_DIR", "data/history")

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _normalize_ohlc(rows: List) -> List[Dict[str, float]]:
    out = []
    if not rows:
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                try:
                    out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]),
                                "l": float(r[3]), "c": float(r[4])})
                except Exception:
                    pass
    elif isinstance(rows, list) and isinstance(rows[0], dict):
        for r in rows:
            try:
                o = float(r.get("open", r.get("o", 0.0)))
                h = float(r.get("high", r.get("h", 0.0)))
                l = float(r.get("low",  r.get("l", 0.0)))
                c = float(r.get("close",r.get("c", 0.0)))
                t = float(r.get("t", r.get("time", 0.0)))
                out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
            except Exception:
                pass
    return out

def save_ohlc_symbol(symbol: str, bars: List[Dict[str, float]], history_dir: str = DEFAULT_HISTORY_DIR) -> str:
    """
    Salva OHLC em HISTORY_DIR/ohlc/{SYMBOL}.json
    bars pode ser lista de dicts {t,o,h,l,c} ou lista de listas [t,o,h,l,c].
    """
    ensure_dir(os.path.join(history_dir, "ohlc"))
    # normaliza para lista de listas
    norm = _normalize_ohlc(bars)
    out_rows = [[b["t"], b["o"], b["h"], b["l"], b["c"]] for b in norm]
    path = os.path.join(history_dir, "ohlc", f"{symbol}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "bars": out_rows}, f, ensure_ascii=False)
    return path

def load_ohlc_symbol(symbol: str, history_dir: str = DEFAULT_HISTORY_DIR) -> List[Dict[str, float]]:
    """
    Lê HISTORY_DIR/ohlc/{SYMBOL}.json e devolve lista padronizada [{t,o,h,l,c},...]
    """
    path = os.path.join(history_dir, "ohlc", f"{symbol}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("bars", data)
        return _normalize_ohlc(rows)
    except Exception:
        return []
