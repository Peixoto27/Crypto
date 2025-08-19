# -*- coding: utf-8 -*-
"""
data_fetcher_coingecko.py
Pequeno cliente CoinGecko para:
 - resolve_cg_id(symbol)  -> "bitcoin", "ethereum", etc.
 - fetch_ohlc(symbol, days) -> [[ts_ms, o, h, l, c], ...]

Usa:
- ENV COINGECKO_API_URL (default https://api.coingecko.com/api/v3)
- (opcional) arquivo local data/cg_ids.json no formato {"BTCUSDT":"bitcoin",...}
"""

import os
import json
import time
import requests
from typing import List, Dict, Optional

API_URL = os.getenv("COINGECKO_API_URL", "https://api.coingecko.com/api/v3").rstrip("/")

_STABLES = ("USDT","BUSD","USDC","TUSD","FDUSD","USDD")

def _split_base_quote(sym: str):
    for q in _STABLES:
        if sym.endswith(q):
            return sym[:-len(q)], q
    return sym[:-3], sym[-3:]

def _load_local_map() -> Dict[str, str]:
    # tenta em data/cg_ids.json
    try_paths = [
        os.path.join("data", "cg_ids.json"),
        "cg_ids.json",
    ]
    for p in try_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict):
                    # normaliza chaves para maiúsculas (BTCUSDT etc)
                    return {k.upper(): v for k, v in obj.items()}
            except Exception:
                pass
    return {}

_LOCAL_MAP = _load_local_map()

def resolve_cg_id(symbol: str) -> Optional[str]:
    """
    1) Tenta map local (data/cg_ids.json)
    2) Tenta /coins/list e procura por symbol == base.lower()
    """
    sym = symbol.upper()
    if sym in _LOCAL_MAP:
        return _LOCAL_MAP[sym]

    base, _ = _split_base_quote(sym)
    base_l = base.lower()

    # consulta /coins/list (sem paginação no v3)
    url = f"{API_URL}/coins/list"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        coins = r.json()  # [{id, symbol, name}, ...]
        # primeiro: match direto de symbol
        for c in coins:
            if str(c.get("symbol", "")).lower() == base_l:
                return c.get("id")
        # fallback: match por name contém base
        for c in coins:
            if base_l in str(c.get("name", "")).lower():
                return c.get("id")
    except Exception:
        return None
    return None

def _request_with_backoff(url: str, params: dict, tries: int = 6) -> requests.Response:
    backoffs = [30.0, 75.0, 187.5, 300.0, 420.0, 600.0]
    last = None
    for i in range(tries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = backoffs[i] if i < len(backoffs) else backoffs[-1]
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except Exception as e:
            last = e
            # timeouts também respeitam backoff
            wait = backoffs[i] if i < len(backoffs) else backoffs[-1]
            time.sleep(wait)
    raise last if last else RuntimeError("Falha de rede")

def fetch_ohlc(symbol: str, days: int) -> List[List[float]]:
    """
    Retorna [[ts_ms, open, high, low, close], ...]
    Endpoint: /coins/{id}/ohlc?vs_currency=usd&days={days}
    """
    cg_id = resolve_cg_id(symbol)
    if not cg_id:
        raise RuntimeError(f"Não foi possível mapear {symbol} em CoinGecko (cg_id).")

    url = f"{API_URL}/coins/{cg_id}/ohlc"
    params = {"vs_currency": "usd", "days": int(days)}
    resp = _request_with_backoff(url, params)
    data = resp.json()  # [[ts, o, h, l, c], ...] ts em ms
    # normaliza para float
    out = []
    for row in data:
        if isinstance(row, list) and len(row) >= 5:
            ts, o, h, l, c = row[:5]
            out.append([float(ts), float(o), float(h), float(l), float(c)])
    return out
