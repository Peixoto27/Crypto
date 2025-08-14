# -*- coding: utf-8 -*-
"""
news_fetcher.py
Cliente mínimo para TheNewsAPI.
Expõe: get_recent_news(symbol: str, lookback_hours: int = 24) -> list[str]
"""

import os
import time
import math
import json
from datetime import datetime, timedelta
from typing import List
import requests

API_KEY = os.getenv("THENEWS_API_KEY", "").strip()

# Limites defensivos (podem ser ajustados por env)
HTTP_TIMEOUT = float(os.getenv("NEWS_HTTP_TIMEOUT", "12"))
NEWS_LIMIT   = int(os.getenv("NEWS_LIMIT", "20"))         # máx 50 no plano free
NEWS_LANG    = os.getenv("NEWS_LANG", "en")               # "en", "pt", etc.
MAX_RETRIES  = int(os.getenv("NEWS_MAX_RETRIES", "4"))
BACKOFF_BASE = float(os.getenv("NEWS_BACKOFF_BASE", "2.0"))

BASE_URL = "https://api.thenewsapi.com/v1/news/all"

# Palavras-chave por símbolo (ajuste livre)
SYMBOL_QUERY = {
    "BTCUSDT": "bitcoin OR BTC",
    "ETHUSDT": "ethereum OR ETH",
    "BNBUSDT": "binance coin OR BNB",
    "SOLUSDT": "solana OR SOL",
    "XRPUSDT": "ripple OR XRP",
    "ADAUSDT": "cardano OR ADA",
    "MATICUSDT": "polygon OR MATIC",
    "DOGEUSDT": "dogecoin OR DOGE",
    "DOTUSDT": "polkadot OR DOT",
    "LINKUSDT": "chainlink OR LINK",
    "LTCUSDT": "litecoin OR LTC",
    "TRXUSDT": "tron OR TRX",
    "AVAXUSDT": "avalanche OR AVAX",
    "ATOMUSDT": "cosmos OR ATOM",
    "FILUSDT": "filecoin OR FIL",
    "INJUSDT": "injective OR INJ",
    "APTUSDT": "aptos OR APT",
    "ARBUSDT": "arbitrum OR ARB",
    # fallback: símbolo puro
}

def _now_utc() -> datetime:
    return datetime.utcnow()

def _iso(dt: datetime) -> str:
    # TheNewsAPI aceita RFC3339 / ISO 8601
    return dt.replace(microsecond=0).isoformat() + "Z"

def _build_params(symbol: str, lookback_hours: int) -> dict:
    q = SYMBOL_QUERY.get(symbol, symbol.replace("USDT", ""))
    published_after = _iso(_now_utc() - timedelta(hours=lookback_hours))
    return {
        "api_token": API_KEY,
        "search": q,
        "language": NEWS_LANG,
        "limit": min(NEWS_LIMIT, 50),
        "published_after": published_after,
        "sort": "published_at",   # mais recentes primeiro
    }

def _fetch(params: dict) -> dict:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(BASE_URL, params=params, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                # rate limit — backoff com “retry_after” se vier
                try:
                    body = r.json()
                    ra = body.get("meta", {}).get("rate_limit", {}).get("reset", 0)
                    wait = float(body.get("meta", {}).get("rate_limit", {}).get("remaining", 0) == 0) or 0
                except Exception:
                    wait = 0
                wait = max(wait, BACKOFF_BASE * attempt)
                print(f"⚠️ 429 TheNewsAPI: aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            # outros HTTP
            print(f"⚠️ News HTTP {r.status_code}: {r.text[:200]}")
            last_err = RuntimeError(f"http {r.status_code}")
        except requests.RequestException as e:
            last_err = e
            wait = BACKOFF_BASE * attempt
            print(f"⚠️ Erro rede TheNewsAPI: {e} (tentativa {attempt}/{MAX_RETRIES}). Aguardando {wait:.1f}s")
            time.sleep(wait)
    raise last_err or RuntimeError("Falha ao buscar notícias")

def get_recent_news(symbol: str, lookback_hours: int = 24) -> List[str]:
    """
    Retorna lista de *títulos* de notícias recentes para o símbolo.
    Se a variável THENEWS_API_KEY não estiver definida, devolve [].
    """
    if not API_KEY:
        print("ℹ️ THENEWS_API_KEY ausente — news desativado.")
        return []
    params = _build_params(symbol, lookback_hours)
    data = _fetch(params)
    # Estrutura típica: {"data": [ { "title": "...", ... }, ...], "meta": {...}}
    titles = []
    try:
        for item in (data.get("data") or []):
            t = (item.get("title") or "").strip()
            if t:
                titles.append(t)
    except Exception:
        pass
    return titles
