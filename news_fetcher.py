# -*- coding: utf-8 -*-
"""
news_fetcher.py
- APITube como fonte primária (200 req/dia no free)
- Fallback automático para TheNewsAPI e RSS
- Interface estável: get_recent_news(symbol) -> List[str]
"""

import os
import time
import json
import math
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests

# --------------------------
# ENV / Config
# --------------------------
USE_APITUBE        = os.getenv("USE_APITUBE", "true").lower() == "true"
APITUBE_API_KEY    = os.getenv("APITUBE_API_KEY", "").strip()
APITUBE_TIMEOUT    = float(os.getenv("APITUBE_TIMEOUT", "12"))
APITUBE_LOOKBACK_HOURS = int(os.getenv("APITUBE_LOOKBACK_HOURS", "12"))
APITUBE_LANG       = os.getenv("APITUBE_LANG", "pt,en")

USE_THENEWSAPI     = os.getenv("USE_THENEWSAPI", "true").lower() == "true"
THENEWS_API_KEY    = os.getenv("THENEWS_API_KEY", "").strip()

NEWS_TIMEOUT       = float(os.getenv("NEWS_TIMEOUT", "10"))  # usado nos fallbacks
NEWS_LOOKBACK_HOURS= int(os.getenv("NEWS_LOOKBACK_HOURS", "12"))
NEWS_MAX_PER_SOURCE= int(os.getenv("NEWS_MAX_PER_SOURCE", "5"))

# Se você já tem um parser RSS, mantenha esta flag para tentar por último
USE_RSS_NEW        = os.getenv("USE_RSS_NEW", "false").lower() == "true"

# --------------------------
# Mapeamento símbolo -> termos de busca
# (mais forte que só o ticker; melhora recall da API)
# --------------------------
SYMBOL_TERMS: Dict[str, List[str]] = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth"],
    "BNBUSDT": ["bnb", "binance coin", "binance chain"],
    "SOLUSDT": ["solana", "sol"],
    "XRPUSDT": ["xrp", "ripple"],
    "ADAUSDT": ["cardano", "ada"],
    "AVAXUSDT": ["avalanche", "avax"],
    "DOTUSDT": ["polkadot", "dot"],
    "LINKUSDT": ["chainlink", "link"],
    "LTCUSDT": ["litecoin", "ltc"],
    "MATICUSDT": ["polygon", "matic"],
    "DOGEUSDT": ["dogecoin", "doge"],
    "TRXUSDT": ["tron", "trx"],
    "FILUSDT": ["filecoin", "fil"],
    "NEARUSDT": ["near protocol", "near"],
    "APTUSDT": ["aptos", "apt"],
    "INJUSDT": ["injective", "inj"],
    "ARBUSDT": ["arbitrum", "arb"],
    "OPUSDT": ["optimism", "op"],
    "XLMUSDT": ["stellar", "xlm"],
    # adicione conforme necessário
}

def _terms_for(symbol: str) -> List[str]:
    return SYMBOL_TERMS.get(symbol.upper(), [symbol.upper()])

# --------------------------
# Helpers
# --------------------------
def _now_utc() -> datetime:
    return datetime.utcnow()

def _iso8601(dt: datetime) -> str:
    # APITube aceita 2024-08-14T10:00:00Z
    return dt.replace(microsecond=0).isoformat() + "Z"

def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for t in items:
        key = (t or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(t.strip())
    return out

def _safe_get(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

# --------------------------
# APITube client (primário)
# --------------------------
def _fetch_apitube(symbol: str) -> List[str]:
    if not (USE_APITUBE and APITUBE_API_KEY):
        return []

    base = "https://api.apitube.io/v1/news/search"
    terms = _terms_for(symbol)
    q = " OR ".join(f'"{t}"' for t in terms)

    since = _now_utc() - timedelta(hours=APITUBE_LOOKBACK_HOURS)
    params = {
        "q": q,
        "published_after": _iso8601(since),
        "lang": APITUBE_LANG,            # ex: "pt,en"
        "page_size": 50,                 # taxa x entrega (ajuste se quiser)
        # "sort_by": "published_desc",   # padrão já recente
    }
    headers = {
        "Authorization": f"Bearer {APITUBE_API_KEY}",
        "Accept": "application/json",
    }

    titles: List[str] = []
    try:
        resp = requests.get(base, params=params, headers=headers, timeout=APITUBE_TIMEOUT)
        if resp.status_code == 401:
            print("❌ APITube: Unauthorized (cheque APITUBE_API_KEY).")
            return []
        if resp.status_code == 429:
            print("⚠️ APITube: Rate limit atingido (429).")
            return []
        resp.raise_for_status()

        data = resp.json()
        items = _safe_get(data, "data", default=[])
        for it in items or []:
            title = (it.get("title") or "").strip()
            # alguns têm "summary"/"excerpt"
            excerpt = (it.get("summary") or it.get("excerpt") or "").strip()
            if title:
                titles.append(title)
            if excerpt:
                titles.append(excerpt)

        titles = _dedupe_keep_order(titles)
        print(f"📰 APITube {symbol}: {len(titles)} textos.")
        return titles
    except requests.Timeout:
        print("⏰ APITube timeout.")
        return []
    except Exception as e:
        print(f"⚠️ APITube erro: {e}")
        return []

# --------------------------
# TheNewsAPI fallback (secundário)
# (igual à sua lógica anterior; simplificado aqui)
# --------------------------
def _fetch_thenewsapi(symbol: str) -> List[str]:
    if not (USE_THENEWSAPI and THENEWS_API_KEY):
        return []

    base = "https://api.thenewsapi.com/v1/news/all"
    terms = _terms_for(symbol)
    q = " OR ".join(terms)
    since = _now_utc() - timedelta(hours=NEWS_LOOKBACK_HOURS)
    params = {
        "api_token": THENEWS_API_KEY,
        "search": q,
        "published_after": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "language": "en,pt",  # ajuste se quiser
        "limit": 50,
    }
    titles: List[str] = []
    try:
        r = requests.get(base, params=params, timeout=NEWS_TIMEOUT)
        if r.status_code == 429:
            print("⚠️ TheNewsAPI: Rate limit 429.")
            return []
        r.raise_for_status()
        data = r.json()
        for it in data.get("data", []):
            t = (it.get("title") or "").strip()
            d = (it.get("description") or "").strip()
            if t:
                titles.append(t)
            if d:
                titles.append(d)
        titles = _dedupe_keep_order(titles)
        print(f"📰 TheNewsAPI {symbol}: {len(titles)} textos.")
        return titles
    except Exception as e:
        print(f"⚠️ TheNewsAPI erro: {e}")
        return []

# --------------------------
# RSS fallback (terciário)
# Mantém hook; se você já tinha um parser, chame aqui.
# --------------------------
def _fetch_rss(symbol: str) -> List[str]:
    if not USE_RSS_NEW:
        return []
    # Se você possui um módulo rss_fetcher com get_titles(symbol),
    # importe e chame aqui. Deixo stub para não quebrar.
    try:
        from rss_fetcher import get_titles  # opcional
        titles = get_titles(symbol) or []
        titles = _dedupe_keep_order(titles)
        print(f"📰 RSS {symbol}: {len(titles)} textos.")
        return titles
    except Exception:
        return []

# --------------------------
# API pública usada pelo sentiment_analyzer
# --------------------------
def get_recent_news(symbol: str) -> List[str]:
    """
    Retorna uma lista de textos (títulos/trechos) recentes sobre o símbolo.
    Ordem de tentativa:
      1) APITube (se habilitado)
      2) TheNewsAPI (se habilitado)
      3) RSS (se habilitado)
    """
    symbol = (symbol or "").upper().strip()
    all_texts: List[str] = []

    # Primário: APITube
    texts = _fetch_apitube(symbol)
    if texts:
        return texts

    # Fallback 1: TheNewsAPI
    texts = _fetch_thenewsapi(symbol)
    if texts:
        return texts

    # Fallback 2: RSS
    texts = _fetch_rss(symbol)
    if texts:
        return texts

    # Sem nada
    return []
