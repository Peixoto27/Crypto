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
            continue
        seen.add(k)
        out.append(t.strip())
    return out

# ---------------------------------------------------------------------
# TheNewsAPI
# Doc geral: https://www.thenewsapi.com/
# Endpoint típico: GET https://api.thenewsapi.com/v1/news/all
#   params:
#     api_token=... (OBRIGATÓRIO)
#     search=bitcoin
#     published_after=YYYY-MM-DDTHH:MM:SSZ
#     languages=en,pt
#     limit=20
# ---------------------------------------------------------------------
def _fetch_from_thenewsapi(symbol: str) -> List[str]:
    if not THENEWS_API_KEY:
        return []

    terms = _SYMBOL_TERMS.get(symbol, [symbol.replace("USDT", "")])
    query = " OR ".join(terms)

    published_after = (datetime.utcnow() - timedelta(hours=NEWS_LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    url = "https://api.thenewsapi.com/v1/news/all"
    params = {
        "api_token": THENEWS_API_KEY,
        "search": query,
        "published_after": published_after,
        "languages": "en,pt",
        "limit": max(NEWS_MAX_PER_SOURCE * 2, 10),  # pega um pouco mais e filtramos
    }

    titles: List[str] = []
    try:
        r = requests.get(url, params=params, timeout=NEWS_TIMEOUT)
        if r.status_code != 200:
            print(f"⚠️ TheNewsAPI {symbol}: HTTP {r.status_code} - {r.text[:200]}")
            return []
        data = r.json()
        # some responses hold articles in 'data' or 'news' depending on plan; we try both
        articles = data.get("data") or data.get("news") or []
        for a in articles:
            t = a.get("title") or ""
            if t:
                titles.append(t)
            if len(titles) >= NEWS_MAX_PER_SOURCE:
                break
    except Exception as e:
        print(f"⚠️ TheNewsAPI erro {symbol}: {e}")
        return []

    return titles

# ---------------------------------------------------------------------
# RSS (sem feedparser; usa xml nativo)
# ---------------------------------------------------------------------
def _fetch_from_rss(symbol: str) -> List[str]:
    # preferimos o primeiro termo para reduzir falsos positivos
    primary = _SYMBOL_TERMS.get(symbol, [symbol.replace("USDT", "")])[0].lower()
    titles: List[str] = []
    since_dt = datetime.utcnow() - timedelta(hours=NEWS_LOOKBACK_HOURS)

    for url in [u.strip() for u in (RSS_SOURCES or "").split(";") if u.strip()]:
        if len(titles) >= NEWS_MAX_PER_SOURCE:
            break
        try:
            r = requests.get(url, timeout=NEWS_TIMEOUT)
            if r.status_code != 200:
                print(f"⚠️ RSS {url} -> HTTP {r.status_code}")
                continue
            root = ET.fromstring(r.content)

            # RSS padrão: channel/item/title, pubDate
            for item in root.findall(".//item"):
                if len(titles) >= NEWS_MAX_PER_SOURCE:
                    break
                title = (item.findtext("title") or "").strip()
                if not title:
                    continue
                # filtro simples: título contém o termo?
                if primary not in title.lower():
                    continue

                # filtra por data quando disponível
                pub = item.findtext("pubDate") or ""
                if pub:
                    try:
                        # tenta parse RFC2822 (Thu, 01 Jan 1970 00:00:00 GMT)
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(pub)
                        if dt.tzinfo:
                            dt = dt.astimezone(tz=None).replace(tzinfo=None)
                        if dt < since_dt:
                            continue
                    except Exception:
                        pass

                titles.append(title)
        except Exception as e:
            print(f"⚠️ RSS erro em {url}: {e}")

    return titles

# ---------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------
def get_recent_news(symbol: str) -> List[str]:
    """
    Retorna títulos de notícias recentes para o símbolo.
    - Usa cache de 30 min.
    - Consulta TheNewsAPI (se ativado) e RSS (se ativado).
    """
    now = _now()
    cached = _cache_titles.get(symbol)
    if cached and (now - cached.get("ts", 0) < _CACHE_TTL):
        return list(cached.get("titles", []))

    titles: List[str] = []

    if USE_THENEWSAPI:
        titles += _fetch_from_thenewsapi(symbol)

    if USE_RSS_NEW:
        titles += _fetch_from_rss(symbol)

    titles = _dedupe_keep_order(titles)

    _cache_titles[symbol] = {"ts": now, "titles": titles}
    return titles
