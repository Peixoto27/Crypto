# -*- coding: utf-8 -*-
"""
signal_generator.py
- Camada simples para salvar/ler sinais em signals.json
- Sanitiza valores (evita float(tuple) e outros formatos)
"""
import os, json
from typing import Dict, Any, List
from signals_model import normalize_signal  # mantém padrão único

SIGNALS_FILE = os.getenv("SIGNALS_FILE", "signals.json")

def _ensure_file(path: str, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)

_ensure_file(SIGNALS_FILE, [])  # lista de sinais abertos/atuais

def _num(x, default=None):
    """
    Converte com segurança para float:
      - tupla/lista -> primeiro elemento numérico
      - string -> float se possível
      - None -> default
    """
    if x is None:
        return default
    if isinstance(x, (list, tuple)) and x:
        return _num(x[0], default)
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.strip())
        except Exception:
            return default
    return default

def load_signals() -> List[Dict[str, Any]]:
    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_signals(rows: List[Dict[str, Any]]) -> None:
    with open(SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

def append_signal(sig: Dict[str, Any]) -> None:
    """
    Sanitiza campos numéricos antes de normalizar/salvar
    (previne 'float() ... not tuple').
    """
    # saneamento local
    sig = dict(sig or {})
    sig["entry"]      = _num(sig.get("entry"))
    sig["tp"]         = _num(sig.get("tp"))
    sig["sl"]         = _num(sig.get("sl"))
    sig["rr"]         = _num(sig.get("rr"), 2.0)
    sig["confidence"] = _num(sig.get("confidence"), 0.0)

    # aplica formatação padrão do projeto
    norm = normalize_signal(sig)

    data = load_signals()
    data.append(norm)
    save_signals(data)
