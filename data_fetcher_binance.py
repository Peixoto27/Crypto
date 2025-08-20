# -*- coding: utf-8 -*-
"""
data_fetcher_binance.py — OHLC via Binance REST (sem SDK).

Retorna lista de [ts,o,h,l,c] em minuto/hora diário (usamos diário).
Em caso de HTTP 451 (bloqueio regional), a exceção propaga para o caller
que fará fallback pro CoinGecko.

Env:
  BINANCE_BASE = https://api.binance.com (padrão)
  BINANCE_INTERVAL = 1d
"""

import os
import json
import time
import urllib.request
import urllib.error
from typing import List

BINANCE_BASE = os.getenv("BINANCE_BASE", "https://api.binance.com")
INTERVAL = os.getenv("BINANCE_INTERVAL", "1d")

def _sleep_backoff(i: int):
    # 5s, 11s, 24s, 53s, 117s, 257s…
    waits = [5, 11, 24, 53, 117, 257]
    time.sleep(waits[min(i, len(waits)-1)])

def fetch_ohlc_binance(symbol: str, days: int) -> List[List[float]]:
    """
    Pega klines diárias suficientes (~days*6 para margem)
    Retorna lista de [ts, o, h, l, c]
    """
    limit = max(100, min(1500, days * 6))
    url = f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval={INTERVAL}&limit={limit}"

    last_err = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status != 200:
                    raise urllib.error.HTTPError(url, resp.status, "HTTP error", resp.headers, None)
                raw = json.loads(resp.read().decode("utf-8"))
            out = []
            for k in raw:
                # kline: [ openTime, open, high, low, close, volume, closeTime, ... ]
                t = float(k[0]) / 1000.0
                o = float(k[1]); h = float(k[2]); l = float(k[3]); c = float(k[4])
                out.append([t, o, h, l, c])
            return out
        except urllib.error.HTTPError as he:
            if he.code == 451:
                raise RuntimeError("HTTP 451")
            last_err = he
            print(f"⚠️ Binance {BINANCE_BASE}: HTTP {he.code} — aguardando {_backoff_secs(attempt)}s (tentativa {attempt+1}/6)")
            _sleep_backoff(attempt)
        except Exception as e:
            last_err = e
            print(f"⚠️ Binance erro {symbol}: {e} — aguardando {_backoff_secs(attempt)}s (tentativa {attempt+1}/6)")
            _sleep_backoff(attempt)

    if last_err:
        raise last_err
    return []

def _backoff_secs(i: int) -> int:
    waits = [5, 11, 24, 53, 117, 257]
    return waits[min(i, len(waits)-1)]
