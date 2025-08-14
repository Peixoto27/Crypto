# -*- coding: utf-8 -*-
"""
sentiment_analyzer.py
Calcula sentimento médio [-1..1] para um símbolo usando títulos da TheNewsAPI.
• Faz cache para reduzir custo/latência
• Respeita um limite/hora simples
• Devolve 0.0 em caso de falta de dados/erro
"""

import os
import time
from collections import deque
from typing import List, Optional

from textblob import TextBlob
from news_fetcher import get_recent_news  # precisa existir no mesmo projeto

# ========================
# Config via env
# ========================
HOURLY_API_CALL_LIMIT = int(os.getenv("SENTI_HOURLY_LIMIT", "10"))           # chamadas/hora
CACHE_DURATION        = int(os.getenv("SENTI_CACHE_SECONDS", str(2*60*60)))  # 2h
STALE_GRACE_SECONDS   = int(os.getenv("SENTI_STALE_GRACE", str(24*60*60)))   # +24h
MIN_NEWS_FOR_SIGNAL   = int(os.getenv("SENTI_MIN_NEWS", "2"))                # mínimo de manchetes

LOOKBACK_HOURS        = int(os.getenv("SENTI_LOOKBACK_H", "24"))             # janela de notícias

# ========================
# Estado
# ========================
_api_times: deque[float] = deque()
_cache = {}  # { symbol: {"score": float, "ts": float, "n": int} }

def _now() -> float:
    return time.time()

def _can_call() -> bool:
    """Janela deslizante de 1h para limite de chamadas."""
    now = _now()
    while _api_times and _api_times[0] < now - 3600:
        _api_times.popleft()
    return len(_api_times) < HOURLY_API_CALL_LIMIT

def _dedupe(items: List[str]) -> List[str]:
    seen, out = set(), []
    for t in items or []:
        t = (t or "").strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out

def _polarity(texts: List[str]) -> float:
    if not texts:
        return 0.0
    s, n = 0.0, 0
    for t in texts:
        try:
            s += TextBlob(t).sentiment.polarity  # [-1..1]
            n += 1
        except Exception:
            continue
    if n == 0:
        return 0.0
    avg = s / n
    # zona morta para ruído muito pequeno
    if abs(avg) < 0.05:
        avg = 0.0
    # clamp + arredonda
    avg = max(-1.0, min(1.0, avg))
    return round(avg, 2)

def _cache_get(symbol: str, now: float) -> Optional[float]:
    it = _cache.get(symbol)
    if not it:
        return None
    if now - it["ts"] < CACHE_DURATION:
        print(f"🧠 Sentiment cache hit {symbol}: {it['score']:.2f} (n={it['n']})")
        return it["score"]
    return None

def _cache_get_stale(symbol: str, now: float) -> Optional[float]:
    it = _cache.get(symbol)
    if not it:
        return None
    if now - it["ts"] < CACHE_DURATION + STALE_GRACE_SECONDS:
        print(f"🧠 Sentiment cache STALE {symbol}: {it['score']:.2f} (n={it['n']})")
        return it["score"]
    return None

def get_sentiment_score(symbol: str) -> float:
    """
    Retorna um escore de sentimento [-1..1].
    Política:
      1) Usa cache fresco se existir
      2) Respeita cota/hora; se estourar, usa cache STALE (até 24h) ou 0.0
      3) Busca títulos via news_fetcher; se < MIN_NEWS_FOR_SIGNAL, devolve 0.0
    """
    now = _now()

    # 1) cache fresco
    c = _cache_get(symbol, now)
    if c is not None:
        return c

    # 2) cota
    if not _can_call():
        s = _cache_get_stale(symbol, now)
        if s is not None:
            return s
        print("🚦 Limite horário de sentimento atingido — devolvendo 0.0")
        return 0.0

    # 3) consulta
    _api_times.append(now)
    try:
        print(f"🌐 Sentiment: buscando notícias de {symbol} (lookback={LOOKBACK_HOURS}h)…")
        titles = _dedupe(get_recent_news(symbol, lookback_hours=LOOKBACK_HOURS))

        if len(titles) < MIN_NEWS_FOR_SIGNAL:
            score = 0.0
        else:
            score = _polarity(titles)

        _cache[symbol] = {"score": score, "ts": now, "n": len(titles)}
        print(f"🧠 Sentiment: {symbol} = {score:.2f} (a partir de {len(titles)} manchetes)")
        return score

    except Exception as e:
        print(f"⚠️ Sentiment falhou para {symbol}: {e}")
        s = _cache_get_stale(symbol, now)
        return s if s is not None else 0.0
