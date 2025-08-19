# -*- coding: utf-8 -*-
"""
sentiment_analyzer.py — mistura NEWS + Twitter com fallback neutro

- get_sentiment_for_symbol(sym) -> (score[0..1], n_news, n_tw)
- Nunca lança exceção; se nada encontrado => 0.5
"""

import os
from typing import List, Dict, Tuple

NEWS_WEIGHT  = float(os.getenv("WEIGHT_NEWS", "1.0"))
TWIT_WEIGHT  = float(os.getenv("WEIGHT_TWITTER", "1.0"))

def _is_true(name, default=False) -> bool:
    v = str(os.getenv(name, str(default))).strip().lower()
    return v in ("1", "true", "yes", "on")

def _safe_list(x):
    return x if isinstance(x, list) else ([] if x is None else list(x))

# ===== fetchers do seu projeto (com tolerância) =====
def _fetch_news(sym: str) -> List[Dict]:
    if not (_is_true("NEWS_USE", False) and os.getenv("NEWS_API_KEY", "").strip()):
        return []
    try:
        from news_fetcher import fetch_news_for_symbol
        return _safe_list(fetch_news_for_symbol(sym))
    except Exception:
        return []

def _fetch_tweets(sym: str) -> List[Dict]:
    if not (_is_true("TWITTER_USE", False) and os.getenv("TWITTER_BEARER_TOKEN", "").strip()):
        return []
    try:
        # se tiver um módulo twitter_fetcher.py use aqui
        from twitter_fetcher import fetch_tweets_for_symbol  # opcional no seu repo
        return _safe_list(fetch_tweets_for_symbol(sym))
    except Exception:
        return []

# ===== scorers =====
def score_from_news(items: List[Dict]) -> float:
    """
    Coloque aqui sua lógica real; por enquanto média simples de 'polarity' se existir.
    """
    if not items: return 0.5
    vals = []
    for it in items:
        v = it.get("polarity") or it.get("score") or 0.0
        try:
            v = float(v)
        except Exception:
            v = 0.0
        # normaliza -1..1 -> 0..1
        if v < -1: v = -1
        if v >  1: v =  1
        vals.append(0.5*(v+1.0))
    return sum(vals)/len(vals) if vals else 0.5

def score_from_twitter(items: List[Dict]) -> float:
    if not items: return 0.5
    vals = []
    for it in items:
        v = it.get("polarity") or it.get("score") or 0.0
        try:
            v = float(v)
        except Exception:
            v = 0.0
        if v < -1: v = -1
        if v >  1: v =  1
        vals.append(0.5*(v+1.0))
    return sum(vals)/len(vals) if vals else 0.5

# ===== API pública =====
def get_sentiment_for_symbol(symbol: str) -> Tuple[float, int, int]:
    """
    Retorna (score_em_[0..1], n_news, n_tw) SEM exceções.
    """
    try:
        news = _fetch_news(symbol)
    except Exception:
        news = []
    try:
        tw = _fetch_tweets(symbol)
    except Exception:
        tw = []

    n_news = len(news)
    n_tw   = len(tw)

    try:
        s_news = score_from_news(news) if n_news else 0.5
    except Exception:
        s_news = 0.5

    try:
        s_tw = score_from_twitter(tw) if n_tw else 0.5
    except Exception:
        s_tw = 0.5

    num = s_news*NEWS_WEIGHT + s_tw*TWIT_WEIGHT
    den = (NEWS_WEIGHT if n_news or NEWS_WEIGHT>0 else 0.0) + (TWIT_WEIGHT if n_tw or TWIT_WEIGHT>0 else 0.0)
    mixed = (num/den) if den > 0 else 0.5

    if mixed < 0.0: mixed = 0.0
    if mixed > 1.0: mixed = 1.0
    return mixed, n_news, n_tw
