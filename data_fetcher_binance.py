# -*- coding: utf-8 -*-
"""
data_fetcher_binance.py
Coleta OHLC da Binance como fonte primária.

- Retorna SEMPRE no formato: [[ts_ms, open, high, low, close], ...]
- Intervalo padrão: 4h
- Respeita limites simples e faz retry com backoff quando pega 429

Env (opcionais):
  BINANCE_API_URL=https://api.binance.com
  BINANCE_INTERVAL=4h          # 1m 5m 15m 1h 4h 1d...
  SLEEP_BETWEEN_CALLS=5        # segundos entre requests
  FETCH_TIMEOUT=20             # timeout por request (s)
"""
from __future__ import annotations

import os
import time
import math
import json
from typing import List, Any
import urllib.request
import urllib.error


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


BINANCE_API_URL = _env("BINANCE_API_URL", "https://api.binance.com")
BINANCE_INTERVAL = _env("BINANCE_INTERVAL", "4h")
SLEEP_BETWEEN_CALLS = float(_env("SLEEP_BETWEEN_CALLS", "5"))
FETCH_TIMEOUT = int(_env("FETCH_TIMEOUT", "20"))


def _http_get_json(url: str, timeout: int) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-runner/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            return json.loads(data)


def _ceil_limit_for_days(days: int, interval: str) -> int:
    # Conversão simples para estimar quantas velas precisamos
    # Limite máximo do endpoint /klines é 1000
    mult = {
        "1m": 24*60,
        "5m": 24*12,
        "15m": 24*4,
        "1h": 24,
        "4h": 6,
        "1d": 1
    }.get(interval, 6)  # default 4h
    return min(1000, int(math.ceil(days * mult)))


def fetch_ohlc(symbol: str, days: int = 30, interval: str | None = None) -> List[List[float]]:
    """
    Busca OHLC na Binance e retorna lista de listas:
    [[ts_ms, open, high, low, close], ...]
    """
    iv = interval or BINANCE_INTERVAL
    limit = _ceil_limit_for_days(days, iv)
    base = BINANCE_API_URL.rstrip("/")
    url = f"{base}/api/v3/klines?symbol={symbol}&interval={iv}&limit={limit}"

    tries = 0
    wait = 5.0
    while True:
        tries += 1
        try:
            data = _http_get_json(url, timeout=FETCH_TIMEOUT)
            # Formato Binance: [
            #   [Open time, Open, High, Low, Close, Volume, Close time, ...],
            #   ...
            # ]
            out = []
            if isinstance(data, list):
                for r in data:
                    if len(r) >= 5:
                        ts = float(r[0])  # ms
                        o = float(r[1]); h = float(r[2]); l = float(r[3]); c = float(r[4])
                        out.append([ts, o, h, l, c])
            time.sleep(SLEEP_BETWEEN_CALLS)
            return out
        except urllib.error.HTTPError as he:
            code = he.code
            if code == 429:
                # rate limit — backoff exponencial
                print(f"⚠️ 429 Binance: aguardando {wait:.1f}s (tentativa {tries}/6)")
                time.sleep(wait)
                wait *= 2.5
                if tries >= 6:
                    raise
            else:
                raise
        except Exception:
            # qualquer outra falha — tenta mais algumas vezes
            if tries < 3:
                time.sleep(wait)
                wait *= 1.8
                continue
            raise
