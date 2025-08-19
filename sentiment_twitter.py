# -*- coding: utf-8 -*-
import os
import time
import math
import html
import re
import requests
from collections import deque
from typing import List, Tuple, Optional
from textblob import TextBlob

# =======================
# ENV
# =======================
TW_BEARER = os.getenv("TWITTER_BEARER", "").strip()
TW_USE    = os.getenv("TWITTER_USE", "true").lower() in ("1","true","yes")

# janela e limites
TW_LOOKBACK_MIN   = int(os.getenv("TWITTER_LOOKBACK_MIN", "120"))   # últimos X minutos
TW_MAX_TWEETS     = int(os.getenv("TWITTER_MAX_TWEETS", "80"))      # teto por consulta
TW_TIMEOUT        = int(os.getenv("TWITTER_TIMEOUT", "20"))         # timeout HTTP
TW_HOURLY_LIMIT   = int(os.getenv("TWITTER_HOURLY_LIMIT", "60"))    # chamadas/hora
TW_LANGS          = [s.strip() for s in os.getenv("TWITTER_LANGS", "en,pt").split(",") if s.strip()]

# cache (TTL) para reduzir custo
CACHE_TTL_S       = int(os.getenv("TWITTER_CACHE_TTL", str(15*60)))  # 15 min
_api_timestamps: deque[float] = deque()
_cache = {}  # symbol -> {"score": float, "n": int, "ts": float}

# mapeamento simples símbolo -> query
_SYMBOL_KEYWORDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binance",
    "XRP": "xrp",
    "SOL": "solana",
    "ADA": "cardano",
    "DOGE":"dogecoin",
    "TRX": "tron",
    "MATIC":"polygon",
    "DOT":"polkadot",
    "LTC":"litecoin",
    "LINK":"chainlink",
    "RNDR":"render",
    "TAO":"bittensor",
    "ATOM":"cosmos",
    "ICP":"internet computer",
    "PEPE":"pepe",
    "CRO":"cronos",
    "MKR":"maker",
}

def _now() -> float:
    return time.time()

def _within_hour() -> bool:
    now = _now()
    while _api_timestamps and _api_timestamps[0] < now - 3600:
        _api_timestamps.popleft()
    return len(_api_timestamps) < TW_HOURLY_LIMIT

def _base_from_symbol(symbol: str) -> str:
    s = symbol.upper().replace("-", "").replace("_", "")
    # corta sufixos comuns
    for suf in ("USDT","USDC","FDUSD","BUSD","TUSD","DAI"):
        if s.endswith(suf):
            return s[:-len(suf)]
    return s

def _build_query(symbol: str) -> str:
    base = _base_from_symbol(symbol)
    word = _SYMBOL_KEYWORDS.get(base, base)
    cashtag = f"${base}"
    # remove palavras muito curtas (evita ruído tipo "TAO" comum em frases)
    if len(word) <= 2:
        word = cashtag
    # query: cashtag OU palavra, evita spam/retweets
    q = f'({cashtag} OR "{word}") -is:retweet -is:reply -is:quote'
    return q

def _lang_filter(tweet: dict) -> bool:
    if not TW_LANGS:
        return True
    lang = tweet.get("lang")
    return (lang in TW_LANGS)

def _clean_text(t: str) -> str:
    t = html.unescape(t or "")
    t = re.sub(r"http\S+", "", t)          # remove links
    t = re.sub(r"[@#]\w+", "", t)          # remove @/#
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _polarity(texts: List[str]) -> float:
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
    score = s / n
    # zona morta para ruído
    if abs(score) < 0.05:
        score = 0.0
    return max(-1.0, min(1.0, round(score, 3)))

def _from_cache(symbol: str) -> Optional[Tuple[float, int]]:
    it = _cache.get(symbol)
    if not it:
        return None
    if _now() - it["ts"] <= CACHE_TTL_S:
        return it["score"], it["n"]
    return None

def _save_cache(symbol: str, score: float, n: int):
    _cache[symbol] = {"score": score, "n": n, "ts": _now()}

def get_twitter_sentiment(symbol: str) -> Tuple[float, int]:
    """
    Retorna (score, n) do Twitter:
      score ∈ [-1,1], n = nº de tweets usados
    Requer TWITTER_BEARER e TWITTER_USE=true.
    Usa cache com TTL e respeita limite horário.
    """
    if not TW_USE or not TW_BEARER:
        return 0.0, 0

    c = _from_cache(symbol)
    if c is not None:
        return c

    if not _within_hour():
        # estourou cota -> devolve cache stale ou 0
        old = _cache.get(symbol)
        if old:
            return old["score"], old["n"]
        return 0.0, 0

    base_url = "https://api.twitter.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {TW_BEARER}"}
    query = _build_query(symbol)

    params = {
        "query": query,
        "max_results": str(max(10, min(100, TW_MAX_TWEETS))),
        "tweet.fields": "lang,created_at,text",
    }

    try:
        _api_timestamps.append(_now())
        r = requests.get(base_url, headers=headers, params=params, timeout=TW_TIMEOUT)
        if r.status_code == 429:
            # rate limit hard do Twitter: não explode o sistema
            return 0.0, 0
        r.raise_for_status()
        data = r.json()
        tweets = data.get("data", []) or []

        texts = []
        seen = set()
        for tw in tweets:
            if not _lang_filter(tw):
                continue
            txt = _clean_text(tw.get("text", ""))
            if not txt:
                continue
            key = txt.lower()
            if key in seen:
                continue
            seen.add(key)
            texts.append(txt)

        score = _polarity(texts)
        _save_cache(symbol, score, len(texts))
        return score, len(texts)
    except Exception:
        # silencioso: volta neutro
        return 0.0, 0
