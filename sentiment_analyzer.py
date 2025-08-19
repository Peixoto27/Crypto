# -*- coding: utf-8 -*-
import os
import time
from typing import Tuple

from news_fetcher import get_recent_news_titles   # você já tem
from textblob import TextBlob

# Twitter opcional
try:
    from sentiment_twitter import get_twitter_sentiment
except Exception:
    def get_twitter_sentiment(symbol: str):
        return (0.0, 0)

# ========================
# ENV / Pesos
# ========================
WEIGHT_NEWS    = float(os.getenv("WEIGHT_NEWS", "1.0"))
WEIGHT_TWITTER = float(os.getenv("WEIGHT_TWITTER", "1.0"))

NEWS_MIN_ART   = int(os.getenv("SENTI_MIN_NEWS", "2"))     # mínimo headlines
CACHE_SECONDS  = int(os.getenv("SENTI_CACHE_SECONDS", str(60*30)))  # 30min

_cache = {}  # symbol -> (score, n, ts)

def _now() -> float:
    return time.time()

def _polarity_texts(texts) -> float:
    if not texts:
        return 0.0
    s = 0.0; n = 0
    for t in texts:
        try:
            s += TextBlob(t).sentiment.polarity
            n += 1
        except Exception:
            pass
    if n == 0:
        return 0.0
    val = s / n
    if abs(val) < 0.05:
        val = 0.0
    return max(-1.0, min(1.0, round(val, 3)))

def _from_cache(symbol: str):
    it = _cache.get(symbol)
    if it and _now() - it[2] <= CACHE_SECONDS:
        return it[0], it[1]
    return None

def _save_cache(symbol: str, score: float, n: int):
    _cache[symbol] = (score, n, _now())

def get_sentiment_score(symbol: str) -> Tuple[float, int]:
    """
    Retorna (score, n_total) em que score ∈ [-1,1]:
      - News (headlines) -> polaridade [-1,1]
      - Twitter (tweets) -> polaridade [-1,1]
      - Média ponderada por WEIGHT_NEWS e WEIGHT_TWITTER
    """
    c = _from_cache(symbol)
    if c is not None:
        return c

    # 1) News
    try:
        headlines = get_recent_news_titles(symbol)  # sua função existente
    except Exception:
        headlines = []
    news_score = _polarity_texts(headlines) if len(headlines) >= NEWS_MIN_ART else 0.0
    news_n = len(headlines) if len(headlines) >= NEWS_MIN_ART else 0

    # 2) Twitter (opcional)
    tw_score, tw_n = get_twitter_sentiment(symbol)

    # 3) Combina
    w_news = WEIGHT_NEWS
    w_twit = WEIGHT_TWITTER
    total_w = max(1e-9, w_news + w_twit)
    mix = (w_news * news_score + w_twit * tw_score) / total_w

    total_n = news_n + tw_n
    _save_cache(symbol, mix, total_n)
    return mix, total_n
