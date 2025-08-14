# -*- coding: utf-8 -*-
import os, json
from typing import Dict, Any, List

HISTORY_FILE = os.getenv("HISTORY_FILE", "history.json")

def _ensure_file(path: str, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)

_ensure_file(HISTORY_FILE, [])  # lista de fechamentos

def load_history() -> List[Dict[str, Any]]:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_history(rows: List[Dict[str, Any]]) -> None:
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

def append_history(event: Dict[str, Any]) -> None:
    """
    event = {
      "symbol": "BTCUSDT", "result": "hit_tp|hit_sl|expirado",
      "entry": float, "tp": float, "sl": float,
      "exit_price": float, "created_at": "...", "closed_at": "...", "id": "..."
    }
    """
    data = load_history()
    data.append(event)
    save_history(data)
