# -*- coding: utf-8 -*-
"""
data_fetcher_cmc.py
Busca universo de moedas pelo CoinMarketCap.
Requer: CMC_API_KEY
"""

import os
import time
import json
import urllib.parse
import urllib.request

CMC_API = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

def _http_get(url: str, params: dict, headers: dict, retries: int = 3, backoff: float = 2.0):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
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

def get_universe(limit: int = 100, min_rank: int = 1) -> list:
    """
    Retorna uma lista de símbolos no formato XXXUSDT (ex.: BTCUSDT).
    Usa market-cap ranking do CMC.
    """
    api_key = os.getenv("CMC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("CMC_API_KEY ausente")

    headers = {
        "X-CMC_PRO_API_KEY": api_key,
        "Accept": "application/json",
        "User-Agent": "crypto-runner/1.0"
    }
    params = {
        "start": min_rank,
        "limit": limit,
        "convert": "USD"
    }
    data = _http_get(CMC_API, params, headers)
    coins = []
    for item in data.get("data", []):
        sym = (item.get("symbol") or "").upper()
        # Ignora stablecoins comuns para evitar ruído
        if sym in {"USDT","USDC","FDUSD","TUSD","DAI","WBTC","WETH"}:
            continue
        coins.append(sym + "USDT")
    return coins
