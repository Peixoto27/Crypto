# -*- coding: utf-8 -*-
"""
news_fetcher.py
Busca títulos de notícias recentes para um símbolo (ex.: BTCUSDT).
- Fonte 1 (opcional): TheNewsAPI (https://www.thenewsapi.com/) - via env THENEWS_API_KEY
- Fonte 2 (opcional): RSS (lista separada por ';' em RSS_SOURCES)

Retorna: list[str] de títulos (sem duplicados).
"""

import os
import time
import json
import math
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Set
from xml.etree import ElementTree as ET

# --------------------------
# Config via ENV
# --------------------------
USE_THENEWSAPI = os.getenv("USE_THENEWSAPI", "true").lower() == "true"
USE_RSS_NEW    = os.getenv("USE_RSS_NEW", "true").lower() == "true"

THENEWS_API_KEY = os.getenv("THENEWS_API_KEY", "").strip()

# janela de busca
NEWS_LOOKBACK_HOURS = int(os.getenv("NEWS_LOOKBACK_HOURS", "12"))
# limite por fonte (evita spam e viés)
NEWS_MAX_PER_SOURCE = int(os.getenv("NEWS_MAX_PER_SOURCE", "5"))
# timeout de cada request
NEWS_TIMEOUT = int(os.getenv("NEWS_TIMEOUT", "10"))

# RSS: string com urls separadas por ';'
RSS_SOURCES = os.getenv("RSS_SOURCES",
    "https://www.coindesk.com/arc/outboundfeeds/rss/;"
    "https://cointelegraph.com/rss;"
    "https://www.binance.com/en/support/announcement/rss;"
    "https://blog.kraken.com/feed/"
)

# Mapeia símbolo → termos de busca
_SYMBOL_TERMS: Dict[str, List[str]] = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth"],
    "BNBUSDT": ["binance coin", "bnb"],
    "XRPUSDT": ["xrp", "ripple"],
    "ADAUSDT": ["cardano", "ada"],
    "DOGEUSDT": ["dogecoin", "doge"],
    "SOLUSDT": ["solana", "sol"],
    "MATICUSDT": ["polygon", "matic"],
    "DOTUSDT": ["polkadot", "dot"],
    "LTCUSDT": ["litecoin", "ltc"],
    "LINKUSDT": ["chainlink", "link"],
}

# cache leve p/ reduzir chamadas
_cache_titles: Dict[str, Dict] = {}  # { symbol: {"ts": epoch, "titles": List[str]} }
_CACHE_TTL = 60 * 30  # 30 min

def _now() -> float:
    return time.time()

def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for t in items:
        k = (t or "").strip().lower()
        if not k or k in seen:
