# -*- coding: utf-8 -*-
"""
news_fetcher.py - Busca manchetes de cripto (NewsData.io) com cache simples.
Compatível com variáveis: NEWS_API_KEY ou THENEWS_API_KEY.
"""

import os, time, json
from typing import List, Dict, Any
import requests

# cache em memória (sobrevive ao processo, não ao restart)
_CACHE: Dict[str, Any] = {}
_TTL = int(os.getenv("NEWS_CACHE_TTL", "900"))  # 900s = 15 min

def _get_key() -> str:
    return (os.getenv("NEWS_API_KEY") 
            or os.getenv("THENEWS_API_KEY") 
            or "").strip()

def _now() -> float:
    return time.time()

def _cache_key(q: str, hours: int, max_items: int) -> str:
    return f"{q}|{hours}|{max_items}"

def _fetch_from_api(query: str, hours: int, max_items: int) -> List[Dict[str, Any]]:
    api_key = _get_key()
    if not api_key:
        print("⚠️ NEWS_API_KEY não definido. Devolvendo lista vazia.")
        return []

    # janela de tempo aprox (NewsData usa params de data também; aqui uso q e tamanho)
    url = "https://newsdata.io/api/1/news"
    params = {
        "apikey": api_key,
        "q": query,
        "language": "en,pt",
        "category": "business,technology",
        "page": 1,
        "size": max(10, max_items),
    }
    try:
        r = requests.get(url, params=params, timeout=float(os.getenv("NEWS_TIMEOUT", "8")))
        if r.status_code != 200:
            print(f"⚠️ NewsData HTTP {r.status_code}: {r.text[:180]}")
            return []
        data = r.json()
        articles = data.get("results") or data.get("articles") or []
        rows = []
        for a in articles[:max_items]:
            rows.append({
                "title": a.get("title"),
                "desc": a.get("description"),
                "link": a.get("link") or a.get("url"),
                "pubDate": a.get("pubDate") or a.get("pubDate_tz") or a.get("pubDateUTC"),
                "source": (a.get("source_id") or a.get("source") or ""),
            })
        return rows
    except Exception as e:
        print(f"⚠️ Erro NewsData: {e}")
        return []

def get_news(query: str, lookback_hours: int = 6, max_items: int = 20) -> List[Dict[str, Any]]:
    """Retorna manchetes (com cache por 15min)."""
    ck = _cache_key(query, lookback_hours, max_items)
    hit = _CACHE.get(ck)
    if hit and (_now() - hit["t"]) < _TTL:
        return hit["data"]

    rows = _fetch_from_api(query, lookback_hours, max_items)
    _CACHE[ck] = {"t": _now(), "data": rows}
    return rows
