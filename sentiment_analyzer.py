# -*- coding: utf-8 -*-
"""
sentiment_analyzer.py
Calcula sentimento médio [-1..1] por símbolo usando títulos do news_fetcher.
- Cache com validade
- Respeita limite por hora (simples)
"""

import os
import time
from collections import deque
from typing import List, Optional

import math
from textblob import TextBlob
from news_fetcher import get_recent_news

# --------------------------
# Config via ENV
# --------------------------
HOURLY_LIMIT        = int(os.getenv("SENTI_HOURLY_LIMIT", "30"))   # chamadas/hora máx
CACHE_SECONDS       = int(os.getenv("SENTI_CACHE_SECONDS", str(2*60*60))) # 2h
STALE_GRACE_SECONDS = int(os.getenv("SENTI_STALE_GRACE", str(24*60*60)))  # usa cache velho até +24h
MIN_NEWS_FOR_SCORE  = int(os.getenv("SENTI_MIN_NEWS", "2"))        # mínimo de títulos para computar

# estado
_call_times: deque[float] = deque()
_cache = {}  # { symbol: {"score": float, "ts": float} }

def _now() -> float:
    return time.time()

def _can_call() -> bool:
    now = _now()
    # limpa eventos antigos (>1h)
    one_hour_ago = now - 3600
    while _call_times and _call_times[0] < one_hour_ago:
        _call_times.popleft()
    return len(_call_times) < HOURLY_LIMIT

def _cache_get(symbol: str, now: float) -> Optional[float]:
    item = _cache.get(symbol)
    if not item:
        return None
    age = now - item["ts"]
    if age < CACHE_SECONDS:
        print(f"🧠 Sentimento (cache) {symbol}: {item['score']:.2f}")
        return item["score"]
    return None

def _cache_get_stale(symbol: str, now: float) -> Optional[float]:
    item = _cache.get(symbol)
    if not item:
        return None
    age = now - item["ts"]
    if age < CACHE_SECONDS + STALE_GRACE_SECONDS:
        print(f"🧠 Sentimento (cache *stale*) {symbol}: {item['score']:.2f}")
        return item["score"]
    return None

def _dedupe(texts: List[str]) -> List[str]:
    seen = set()
    out = []
    for t in texts or []:
        t = (t or "").strip()
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out

def _polarity_avg(texts: List[str]) -> float:
    if not texts:
        return 0.0
    tot, n = 0.0, 0
    for t in texts:
        try:
            tot += TextBlob(t).sentiment.polarity  # [-1, 1]
            n += 1
        except Exception:
            continue
    if n == 0:
        return 0.0
    score = tot / n
    # zona morta para ruído
    if abs(score) < 0.05:
        score = 0.0
    return round(max(-1.0, min(1.0, score)), 2)

def get_sentiment_score(symbol: str) -> float:
    """
    Retorna o sentimento médio [-1..1] para o símbolo.
    Regras:
      1) usa cache válido (2h)
      2) respeita limite/hora
      3) se falhar/limite, tenta cache stale (24h)
      4) senão, retorna 0.0
    """
    now = _now()

    cached = _cache_get(symbol, now)
    if cached is not None:
        return cached

    if not _can_call():
        stale = _cache_get_stale(symbol, now)
        return stale if stale is not None else 0.0

    _call_times.append(now)
    try:
        titles = get_recent_news(symbol)
        titles = _dedupe(titles)

        if len(titles) < MIN_NEWS_FOR_SCORE:
            score = 0.0
        else:
            score = _polarity_avg(titles)

        _cache[symbol] = {"score": score, "ts": now}
        print(f"🧠 Sentimento calculado {symbol}: {score:.2f} (n={len(titles)})")
        return score
    except Exception as e:
        print(f"⚠️ Sentimento falhou {symbol}: {e}")
        stale = _cache_get_stale(symbol, now)
        return stale if stale is not None else 0.0
