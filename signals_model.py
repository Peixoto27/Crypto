# -*- coding: utf-8 -*-
from typing import Dict, Any

def normalize_signal(sig: Dict[str, Any]) -> Dict[str, Any]:
    """
    Garante um formato padrão do sinal em TODO o sistema.
    Campos esperados:
      symbol, entry, tp, sl, rr, confidence, strategy, created_at, id
    """
    return {
        "symbol":      sig.get("symbol"),
        "entry":       float(sig.get("entry")) if sig.get("entry") is not None else None,
        "tp":          float(sig.get("tp"))    if sig.get("tp")    is not None else None,
        "sl":          float(sig.get("sl"))    if sig.get("sl")    is not None else None,
        "rr":          float(sig.get("rr", 2.0)),
        "confidence":  float(sig.get("confidence", 0.0)),
        "strategy":    sig.get("strategy", "RSI+MACD+EMA+BB"),
        "created_at":  sig.get("created_at"),
        "id":          sig.get("id"),
    }
