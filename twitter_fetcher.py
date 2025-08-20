# -*- coding: utf-8 -*-
import os, requests

BEARER = os.getenv("TWITTER_BEARER_TOKEN", "")
LOOKBACK_MIN = int(os.getenv("TWITTER_LOOKBACK_MIN", "120") or "120")
MAX_TWEETS   = int(os.getenv("TWITTER_MAX_TWEETS", "80") or "80")
TW_LANGS     = (os.getenv("TWITTER_LANGS", "en,pt") or "en,pt").split(",")

SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

def _simple_sentiment(text: str) -> float:
    if not text: return 0.5
    t = text.lower()
    pos = sum(w in t for w in ["moon", "pump", "bull", "ath", "buy", "up", "breakout"])
    neg = sum(w in t for w in ["dump", "bear", "down", "rug", "scam", "sell"])
    score = 0.5 + 0.1*(pos - neg)
    return min(1.0, max(0.0, score))

def get_sentiment_for_symbol(symbol: str):
    """
    Retorna {"score":0..1, "count":N}
    """
    if not BEARER:
        return {"score": 0.5, "count": 0}

    query = f'({symbol} OR #{symbol.replace("USDT","")}) -is:retweet lang:en'
    params = {
        "query": query,
        "max_results": str(min(100, max(10, MAX_TWEETS))),
        "tweet.fields": "lang,created_at",
    }
    headers = {"Authorization": f"Bearer {BEARER}"}
    try:
        r = requests.get(SEARCH_URL, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        tweets = data.get("data", [])
        scores = []
        for tw in tweets:
            if tw.get("lang") not in TW_LANGS and "en" not in TW_LANGS:
                # Se filtragem por idioma estiver muito restrita, caia para 'en'
                pass
            s = _simple_sentiment(tw.get("text", ""))
            scores.append(s)
            if len(scores) >= MAX_TWEETS:
                break
        if not scores:
            return {"score": 0.5, "count": 0}
        return {"score": sum(scores)/len(scores), "count": len(scores)}
    except Exception:
        return {"score": 0.5, "count": 0}
