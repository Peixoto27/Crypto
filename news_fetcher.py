# -*- coding: utf-8 -*-
import os, time, json, math
from datetime import datetime, timedelta
import requests

NEWS_API_URL = os.getenv("NEWS_API_URL", "https://newsdata.io/api/1/news")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
LOOKBACK_H  = int(os.getenv("NEWS_LOOKBACK_HOURS", "12") or "12")
MAX_PER_SRC = int(os.getenv("NEWS_MAX_PER_SOURCE", "5") or "5")
NEWS_LANGS  = os.getenv("NEWS_LANGS", "en,pt") or "en,pt"
NEWS_CAT    = os.getenv("NEWS_CATEGORY", "business,technology,crypto,markets") or "business,technology,crypto,markets"
TIMEOUT_S   = int(float(os.getenv("NEWS_TIMEOUT", "8.0") or "8"))
CACHE_FILE  = os.getenv("NEWS_CACHE_FILE", "news_cache.json") or "news_cache.json"
CACHE_TTL   = int(os.getenv("NEWS_CACHE_TTL_MIN", "30") or "30") * 60

def _now_utc(): return datetime.utcnow()

def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ts": 0, "items": {}}

def _save_cache(obj):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass

def _fetch(symbol: str) -> list:
    # Palavras-chave simples: símbolo e nome popular (se tiver mapeamento)
    q = symbol.replace("USDT", "")
    params = {
        "apikey": NEWS_API_KEY,
        "q": q,
        "language": NEWS_LANGS,
        "category": NEWS_CAT,
    }
    try:
        r = requests.get(NEWS_API_URL, params=params, timeout=TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        # Normaliza lista de resultados:
        articles = data.get("results") or data.get("articles") or []
        return articles[: MAX_PER_SRC] if MAX_PER_SRC > 0 else articles
    except Exception:
        return []

def _cached(symbol: str) -> list:
    cache = _load_cache()
    if time.time() - cache.get("ts", 0) <= CACHE_TTL:
        lst = cache.get("items", {}).get(symbol)
        if isinstance(lst, list):
            return lst
    items = _fetch(symbol)
    cache.setdefault("items", {})[symbol] = items
    cache["ts"] = time.time()
    _save_cache(cache)
    return items

def _simple_sentiment(text: str) -> float:
    """Heurística simples 0..1. (substitua por modelo se quiser)"""
    if not text: return 0.5
    t = text.lower()
    pos = sum(w in t for w in ["surge", "rally", "bull", "up", "record", "buy", "partnership", "growth"])
    neg = sum(w in t for w in ["fall", "dump", "bear", "down", "hack", "lawsuit", "ban", "sell"])
    score = 0.5 + 0.1*(pos - neg)
    return min(1.0, max(0.0, score))

def get_sentiment_for_symbol(symbol: str):
    """
    Retorna {"score":0..1, "count":N}
    """
    if not NEWS_API_KEY:
        return {"score": 0.5, "count": 0}
    items = _cached(symbol)
    if not items:
        return {"score": 0.5, "count": 0}
    scores = []
    for it in items:
        title = it.get("title") or ""
        desc  = it.get("description") or ""
        s = _simple_sentiment(f"{title}. {desc}")
        scores.append(s)
        if len(scores) >= MAX_PER_SRC > 0:
            break
    if not scores:
        return {"score": 0.5, "count": 0}
    return {"score": sum(scores)/len(scores), "count": len(scores)}
