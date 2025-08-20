# -*- coding: utf-8 -*-
"""
data_fetcher_coingecko_fallback.py
Fallback leve para OHLC do CoinGecko.

Formata SEMPRE como: [[ts_ms, open, high, low, close], ...]

ENVs:
  CG_API_URL=https://api.coingecko.com/api/v3
  CG_IDS_FILE=cg_ids.json        # caminho do mapeamento { "BTCUSDT": "bitcoin", ... }
  CG_VS=usd
  SLEEP_BETWEEN_CALLS=5
  FETCH_TIMEOUT=20
"""
from __future__ import annotations

import os, json, time
from typing import Dict, List
import urllib.request, urllib.error

def _env(n: str, d: str) -> str:
    v = os.getenv(n);  return d if v is None else v

CG_API_URL = _env("CG_API_URL", "https://api.coingecko.com/api/v3")
CG_IDS_FILE = _env("CG_IDS_FILE", "cg_ids.json")
CG_VS = _env("CG_VS", "usd")
SLEEP_BETWEEN_CALLS = float(_env("SLEEP_BETWEEN_CALLS", "5"))
FETCH_TIMEOUT = int(_env("FETCH_TIMEOUT", "20"))

_IDS: Dict[str,str] | None = None

def _load_ids() -> Dict[str,str]:
    global _IDS
    if _IDS is not None:
        return _IDS
    with open(CG_IDS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # aceita [{"symbol":"BTCUSDT","id":"bitcoin"}, ...] OU {"BTCUSDT":"bitcoin", ...}
    if isinstance(data, list):
        m: Dict[str,str] = {}
        for item in data:
            s = str(item.get("symbol","")).upper()
            i = str(item.get("id","")).strip()
            if s and i:
                m[s] = i
        _IDS = m
    elif isinstance(data, dict):
        _IDS = {str(k).upper(): str(v) for k,v in data.items()}
    else:
        _IDS = {}
    return _IDS

def _http_get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent":"crypto-runner/1.1"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))

def fetch_ohlc(symbol: str, days: int = 30) -> List[List[float]]:
    ids = _load_ids()
    coin = ids.get(symbol.upper())
    if not coin:
        raise RuntimeError(f"Sem mapeamento CoinGecko para {symbol}. Edite {CG_IDS_FILE}.")
    base = CG_API_URL.rstrip("/")
    url = f"{base}/coins/{coin}/ohlc?vs_currency={CG_VS}&days={int(days)}"
    data = _http_get_json(url)
    out: List[List[float]] = []
    if isinstance(data, list):
        for r in data:
            if len(r) >= 5:
                # CG retorna ts em ms; já convertemos para float
                out.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])])
    time.sleep(SLEEP_BETWEEN_CALLS)
    return out
