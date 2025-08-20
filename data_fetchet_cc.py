# -*- coding: utf-8 -*-
"""
data_fetcher_cc.py
OHLC via CryptoCompare (sem Binance).
Requer: CRYPTOCOMPARE_API_KEY
"""

import os
import time
import json
import urllib.parse
import urllib.request
from typing import List, Tuple

CC_BASE = "https://min-api.cryptocompare.com"
# formata: [ [ts, o, h, l, c, v], ... ]

def _http_get(path: str, params: dict, retries: int = 3, backoff: float = 2.0):
    api_key = os.getenv("CRYPTOCOMPARE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("CRYPTOCOMPARE_API_KEY ausente")
    headers = {
        "Accept": "application/json",
        "User-Agent": "crypto-runner/1.0",
        "Authorization": f"Apikey {api_key}",
    }
    url = f"{CC_BASE}{path}?{urllib.parse.urlencode(params)}"
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(backoff * (i+1))
    raise last_err

def _to_ohlc_rows(d: dict) -> List[Tuple[int,float,float,float,float,float]]:
    res = []
    for it in d.get("Data", {}).get("Data", []):
        res.append([
            int(it.get("time", 0)),
            float(it.get("open", 0) or 0),
            float(it.get("high", 0) or 0),
            float(it.get("low", 0) or 0),
            float(it.get("close", 0) or 0),
            float(it.get("volumeto", 0) or 0),
        ])
    return res

def fetch_ohlc_cc(symbol_usdt: str, days: int = 30, interval: str = "4h") -> List[list]:
    """
    Retorna OHLC no formato [[ts, open, high, low, close, volume], ...]
    interval: "4h" (histohour aggregate=4) ou "1d" (histoday).
    """
    if not symbol_usdt.endswith("USDT"):
        raise ValueError("Esperado símbolo no formato XXXUSDT")
    fsym = symbol_usdt[:-4]  # BTCUSDT -> BTC
    tsym = "USDT"

    if interval == "4h":
        limit = max(60, int(days*6))  # 6 candles 4h por dia
        data = _http_get(
            "/data/v2/histohour",
            {"fsym": fsym, "tsym": tsym, "aggregate": 4, "limit": limit}
        )
    else:
        limit = max(30, days)
        data = _http_get(
            "/data/v2/histoday",
            {"fsym": fsym, "tsym": tsym, "aggregate": 1, "limit": limit}
        )

    rows = _to_ohlc_rows(data)
    # garante ordem crescente (CryptoCompare já retorna cronológico, mas não custa)
    rows.sort(key=lambda r: r[0])
    return rows
