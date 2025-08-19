# -*- coding: utf-8 -*-
"""
signal_generator.py — wrapper estável para geração de score/sinal técnico
Usa apply_strategies.score_signal, mas “blinda” retorno (float|dict|tuple).
"""

from typing import List, Dict, Any, Tuple
from apply_strategies import score_signal as _score

def _normalize_score(val) -> float:
    try:
        if isinstance(val, dict):
            s = float(val.get("score", val.get("value", 0.0)))
        elif isinstance(val, (tuple, list)):
            s = float(val[0]) if val else 0.0
        else:
            s = float(val)
        if s > 1.0:
            s = s / 100.0
        return max(0.0, min(1.0, s))
    except Exception:
        return 0.0

def score_from_indicators(ohlc: List[Dict[str, float]]) -> float:
    try:
        raw = _score(ohlc)
        return _normalize_score(raw)
    except Exception:
        return 0.0
