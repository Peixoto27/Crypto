# -*- coding: utf-8 -*-
import os, json
from typing import Dict, Any, List
from signals_model import normalize_signal

SIGNALS_FILE = os.getenv("SIGNALS_FILE", "signals.json")

def _ensure_file(path: str, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)

_ensure_file(SIGNALS_FILE, [])  # lista de sinais abertos

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
    data = load_signals()
    data.append(normalize_signal(sig))
    save_signals(data)

def list_open_signals() -> List[Dict[str, Any]]:
    return load_signals()
