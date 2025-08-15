# -*- coding: utf-8 -*-
"""
sentiment_analyzer.py - Score simples baseado nas manchetes via news_fetcher.
"""

import math
from typing import List, Dict
from news_fetcher import get_news

# lista minúscula de palavras chave (toy)
POS = {"rally", "surge", "breakout", "bull", "partnership", "etf", "approval", "record", "upgrade", "positive", "up"}
NEG = {"dump", "hack", "exploit", "lawsuit", "ban", "down", "bear", "risk", "halt", "negative", "fraud"}

def _token_score(text: str) -> float:
    if not text:
        return 0.0
    t = text.lower()
    sc = 0
    for w in POS:
        if w in t: sc += 1
    for w in NEG:
        if w in t: sc -= 1
    return sc

def get_sentiment_score(symbol: str, lookback_hours: int = None, max_items: int = None) -> float:
    # usa env padrão do projeto
    import os
    lookback_hours = int(os.getenv("NEWS_LOOKBACK_HOURS", "6")) if lookback_hours is None else lookback_hours
    max_items      = int(os.getenv("NEWS_MAX_PER_SOURCE", "10")) if max_items is None else max_items

    q = symbol.replace("USDT", "")  # ex: BTCUSDT -> BTC
    rows = get_news(q, lookback_hours, max_items)
    if not rows:
        return 0.0

    raw = 0.0
    n = 0
    for r in rows:
        title = r.get("title") or ""
        desc  = r.get("desc") or ""
        raw += _token_score(title) + 0.5 * _token_score(desc)
        n += 1

    # normaliza em ~[-1,1]
    if n == 0:
        return 0.0
    avg = raw / max(1, n)
    return max(-1.0, min(1.0, avg / 5.0))
