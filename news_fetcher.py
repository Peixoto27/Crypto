# -*- coding: utf-8 -*-
# news_fetcher.py — multi-fonte grátis (TheNewsAPI + RSS + Reddit JSON) com fallback
import os, time, json, re
from datetime import datetime, timedelta, timezone
from typing import List, Dict
import requests
import feedparser

# ------------------ ENV ------------------
USE_THENEWSAPI   = os.getenv("USE_THENEWSAPI", "true").lower() == "true"
USE_RSS_NEWS     = os.getenv("USE_RSS_NEWS", "true").lower() == "true"
USE_REDDIT_NEWS  = os.getenv("USE_REDDIT_NEWS", "false").lower() == "true"

THENEWS_API_KEY  = os.getenv("THENEWS_API_KEY", "").strip()
NEWS_MAX_PER_SRC = int(os.getenv("NEWS_MAX_PER_SOURCE", "12"))
NEWS_LOOKBACK_H  = int(os.getenv("NEWS_LOOKBACK_HOURS", "12"))
NEWS_TIMEOUT     = int(os.getenv("NEWS_TIMEOUT", "8"))

RSS_FEEDS_RAW    = os.getenv("RSS_FEEDS", "")
REDDIT_QUERY     = os.getenv("REDDIT_QUERY", "crypto OR bitcoin OR ethereum")
REDDIT_LIMIT     = int(os.getenv("REDDIT_LIMIT", "10"))

# Símbolos -> termos
SYMBOL_TERMS = {
    "BTCUSDT": ["bitcoin", "btc"], "ETHUSDT": ["ethereum", "eth"],
    "BNBUSDT": ["bnb", "binance"], "SOLUSDT": ["solana", "sol"],
    "XRPUSDT": ["xrp", "ripple"], "ADAUSDT": ["cardano", "ada"],
    "DOTUSDT": ["polkadot", "dot"], "MATICUSDT": ["polygon", "matic"],
    "LTCUSDT": ["litecoin", "ltc"], "LINKUSDT": ["chainlink", "link"],
    "DOGEUSDT": ["dogecoin", "doge"],
}

USER_AGENT = {"User-Agent": "CryptonSignalsBot/1.0 (https://github.com/)"}

def _utc_now():
    return datetime.now(timezone.utc)

def _since_dt():
    return _utc_now() - timedelta(hours=NEWS_LOOKBACK_H)

def _dedupe(strings: List[str]) -> List[str]:
    seen, out = set(), []
    for s in strings:
        s = (s or "").strip()
        if not s: continue
        k = re.sub(r"\s+", " ", s.lower())
        if k not in seen:
            seen.add(k); out.append(s)
    return out

# -------------- TheNewsAPI --------------
def _newsapi_for_terms(terms: List[str]) -> List[str]:
    if not USE_THENEWSAPI or not THENEWS_API_KEY:
        return []
    q = " OR ".join(terms)
    url = "https://api.thenewsapi.com/v1/news/all"
    params = {
        "api_token": THENEWS_API_KEY,
        "search": q,
        "language": "en",
        "limit": min(NEWS_MAX_PER_SRC, 50),
        "published_after": _since_dt().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        r = requests.get(url, params=params, headers=USER_AGENT, timeout=NEWS_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        titles = [a.get("title") for a in data.get("data", [])]
        return [t for t in titles if t]
    except Exception as e:
        print(f"⚠️ TheNewsAPI falhou: {e}")
        return []

# -------------- RSS --------------
def _rss_for_terms(terms: List[str]) -> List[str]:
    if not USE_RSS_NEWS or not RSS_FEEDS_RAW:
        return []
    titles: List[str] = []
    feeds = [u.strip() for u in RSS_FEEDS_RAW.split(";") if u.strip()]
    cutoff = _since_dt()
    for feed in feeds:
        try:
            fp = feedparser.parse(feed)
            for entry in fp.entries[:NEWS_MAX_PER_SRC]:
                title = (entry.get("title") or "").strip()
                if not title: continue
                # data de publicação se disponível
                ts = entry.get("published_parsed") or entry.get("updated_parsed")
                if ts:
                    dt = datetime(*ts[:6], tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                lt = title.lower()
                if any(term.lower() in lt for term in terms):
                    titles.append(title)
        except Exception as e:
            print(f"⚠️ RSS falhou {feed}: {e}")
    return titles

# -------------- Reddit (opcional) --------------
def _reddit_for_terms(terms: List[str]) -> List[str]:
    if not USE_REDDIT_NEWS:
        return []
    try:
        q = " OR ".join(terms)
        url = "https://www.reddit.com/search.json"
        params = {"q": q, "sort": "new", "limit": str(REDDIT_LIMIT), "t": "day"}
        r = requests.get(url, params=params, headers=USER_AGENT, timeout=NEWS_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        out = []
        for ch in data.get("data", {}).get("children", []):
            title = ch.get("data", {}).get("title")
            if title:
                out.append(title)
        return out
    except Exception as e:
        print(f"⚠️ Reddit falhou: {e}")
        return []

# -------------- API pública --------------
def get_recent_news(symbol: str) -> List[str]:
    """Retorna lista de títulos recentes relacionados ao símbolo (com fallback e dedupe)."""
    terms = SYMBOL_TERMS.get(symbol, [symbol])
    titles: List[str] = []
    titles += _newsapi_for_terms(terms)
    titles += _rss_for_terms(terms)
    titles += _reddit_for_terms(terms)
    deduped = _dedupe(titles)[:NEWS_MAX_PER_SRC]
    print(f"🗞️ news_fetcher: {symbol} -> {len(deduped)} títulos (antes {len(titles)})")
    return deduped
