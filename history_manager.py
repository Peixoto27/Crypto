# history_manager.py
# -*- coding: utf-8 -*-

"""
Gerenciamento de histórico (OHLCV) com cache local.
Inclui:
- save_ohlc_cache(symbol, bars, path)   -> salva candles
- load_ohlc_cache(symbol, path)         -> lê candles
- clear_ohlc_cache(symbol, path)        -> limpa cache
"""

import os
import io
import json
import time
import threading
from typing import List, Dict, Any, Optional

_LOCK = threading.Lock()

def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def _file_for_symbol(symbol: str, base_path: str) -> str:
    """Gera caminho do cache para cada símbolo."""
    sym = symbol.replace("/", "_").upper()
    return os.path.join(base_path, f"{sym}_ohlc.json")

def save_ohlc_cache(symbol: str,
                    bars: List[Dict[str, Any]],
                    path: str = "cache") -> None:
    """
    Salva lista de candles (OHLCV) em JSON.
    Cada bar deve ser dict com chaves: time, open, high, low, close, volume.
    """
    _ensure_dir(path)
    fpath = _file_for_symbol(symbol, path)
    data = {
        "symbol": symbol,
        "saved_at": int(time.time()),
        "bars": bars or []
    }
    tmp = fpath + ".tmp"
    content = json.dumps(data, ensure_ascii=False, indent=2)
    with _LOCK:
        with io.open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, fpath)

def load_ohlc_cache(symbol: str,
                    path: str = "cache") -> Optional[List[Dict[str, Any]]]:
    """Lê candles salvos no cache. Retorna lista ou None se não existir."""
    fpath = _file_for_symbol(symbol, path)
    if not os.path.exists(fpath):
        return None
    try:
        with io.open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("bars")
    except Exception:
        return None

def clear_ohlc_cache(symbol: str,
                     path: str = "cache") -> bool:
    """Remove cache de um símbolo específico."""
    fpath = _file_for_symbol(symbol, path)
    if os.path.exists(fpath):
        os.remove(fpath)
        return True
    return False
