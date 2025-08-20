# -*- coding: utf-8 -*-
"""
data_fetcher_binance.py
Coleta OHLC da Binance. Tenta vários endpoints para contornar 451/429.

Formata SEMPRE como: [[ts_ms, open, high, low, close], ...]

ENVs:
  BINANCE_API_URLS=https://api.binance.com,https://api1.binance.com,https://api-gcp.binance.com
  BINANCE_INTERVAL=4h
  SLEEP_BETWEEN_CALLS=5
  FETCH_TIMEOUT=20
"""
from __future__ import annotations

import os, time, math, json
from typing import Any, List, Sequence
import urllib.request, urllib.error

def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None else v

BINANCE_API_URLS: Sequence[str] = [u.strip() for u in _env(
    "BINANCE_API_URLS",
    "https://api.binance.com,https://api1.binance.com,https://api-gcp.binance.com"
).split(",") if u.strip()]

BINANCE_INTERVAL = _env("BINANCE_INTERVAL", "4h")
SLEEP_BETWEEN_CALLS = float(_env("SLEEP_BETWEEN_CALLS", "5"))
FETCH_TIMEOUT = int(_env("FETCH_TIMEOUT", "20"))

def _http_get_json(url: str, timeout: int) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-runner/1.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return json.loads(raw)

def _ceil_limit_for_days(days: int, interval: str) -> int:
    mult = {"1m":1440,"5m":288,"15m":96,"1h":24,"4h":6,"1d":1}.get(interval, 6)
    return min(1000, int(math.ceil(days * mult)))

def _fetch_one(base_url: str, symbol: str, days: int, interval: str) -> List[List[float]]:
    limit = _ceil_limit_for_days(days, interval)
    base = base_url.rstrip("/")
    url = f"{base}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = _http_get_json(url, timeout=FETCH_TIMEOUT)
    out: List[List[float]] = []
    if isinstance(data, list):
        for r in data:
            if len(r) >= 5:
                out.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])])
    time.sleep(SLEEP_BETWEEN_CALLS)
    return out

def fetch_ohlc(symbol: str, days: int = 30, interval: str | None = None) -> List[List[float]]:
    iv = interval or BINANCE_INTERVAL
    wait = 5.0
    tries = 0
    last_err: Exception | None = None
    for base in BINANCE_API_URLS:
        tries = 0
        wait = 5.0
        while tries < 6:
            tries += 1
            try:
                return _fetch_one(base, symbol, days, iv)
            except urllib.error.HTTPError as he:
                if he.code in (418, 429, 451):
                    print(f"⚠️ Binance {base.split('//')[-1]}: HTTP {he.code} — aguardando {wait:.1f}s (tentativa {tries}/6)")
                    time.sleep(wait); wait *= 2.2
                    last_err = he
                    continue
                last_err = he
                break
            except Exception as e:
                last_err = e
                if tries < 3:
                    time.sleep(wait); wait *= 1.6
                    continue
                break
    if last_err: raise last_err
    return []
