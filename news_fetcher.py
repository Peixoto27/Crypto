# -*- coding: utf-8 -*-
"""
news_fetcher.py — TheNewsAPI + RSS (fallback) com sanitização de URLs.
Expondo: get_recent_news(symbol: str, lookback_hours: int = 24) -> list[str]
"""

import os, time, re
from datetime import datetime, timedelta, timezone
from typing import List
import requests
import feedparser

# ---- ENV ----
USE_THENEWSAPI  = os.getenv("USE_THENEWSAPI", "true").lower() == "true"
USE_RSS_NEWS    = os.getenv("USE_RSS_NEWS", "true").lower() == "true"
THENEWS_API_KEY = os.getenv("THENEWS_API_KEY", "").strip()

NEWS_LIMIT      = int(os.getenv("NEWS_LIMIT", "20"))
NEWS_LANG       = os.getenv("NEWS_LANG", "en")
NEWS_TIMEOUT    = int(os.getenv("NEWS_HTTP_TIMEOUT", "12"))
NEWS_MAX_RETRY  = int(os.getenv("NEWS_MAX_RETRIES", "3"))
RSS_FEEDS_RAW   = os.getenv("RSS_FEEDS", "")
NEWS_LOOKBACK_H = int(os.getenv("NEWS_LOOKBACK_HOURS", "12"))

USER_AGENT_HDRS = {"User-Agent": "CryptoSignalsBot/1.0 (+https://example.com)"}

# Símbolo -> termos de busca
SYMBOL_QUERY = {
    "BTCUSDT": "bitcoin OR BTC",
    "ETHUSDT": "ethereum OR ETH",
    "BNBUSDT": "binance coin OR BNB",
    "SOLUSDT": "solana OR SOL",
    "XRPUSDT": "ripple OR XRP",
    "ADAUSDT": "cardano OR ADA",
    "MATICUSDT": "polygon OR MATIC",
    "DOGEUSDT": "dogecoin OR DOGE",
    "DOTUSDT": "polkadot OR DOT",
    "LINKUSDT": "chainlink OR LINK",
    "LTCUSDT": "litecoin OR LTC",
}

def _utc_now():
    return datetime.now(timezone.utc)

def _since_dt():
    return _utc_now() - timedelta(hours=NEWS_LOOKBACK_H)

def _dedupe(titles: List[str]) -> List[str]:
    seen, out = set(), []
    for t in titles or []:
        t = (t or "").strip()
        if not t: continue
        k = re.sub(r"\s+", " ", t.lower())
        if k in seen: continue
        seen.add(k); out.append(t)
    return out

# ---------- TheNewsAPI ----------
def _fetch_newsapi(symbol: str) -> List[str]:
    if not (USE_THENEWSAPI and THENEWS_API_KEY):
        return []
    q = SYMBOL_QUERY.get(symbol, symbol.replace("USDT", ""))
    params = {
        "api_token": THENEWS_API_KEY,
        "search": q,
        "language": NEWS_LANG,
        "limit": min(NEWS_LIMIT, 50),
        "published_after": _since_dt().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sort": "published_at",
    }
    url = "https://api.thenewsapi.com/v1/news/all"
    last_err = None
    for i in range(1, NEWS_MAX_RETRY+1):
        try:
            r = requests.get(url, params=params, headers=USER_AGENT_HDRS, timeout=NEWS_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                return [ (a.get("title") or "").strip() for a in (data.get("data") or []) if a.get("title") ]
            if r.status_code == 429:
                wait = 2.5 * i
                print(f"⚠️ 429 TheNewsAPI — aguardando {wait:.1f}s…")
                time.sleep(wait)
                continue
            print(f"⚠️ TheNewsAPI HTTP {r.status_code}: {r.text[:160]}")
            last_err = RuntimeError(r.status_code)
        except requests.RequestException as e:
            last_err = e
            wait = 2.5 * i
            print(f"⚠️ TheNewsAPI erro rede: {e} — retry em {wait:.1f}s")
            time.sleep(wait)
    if last_err: print(f"⚠️ TheNewsAPI falhou: {last_err}")
    return []

# ---------- RSS ----------
def _clean_urls(raw: str) -> List[str]:
    urls = [u.strip() for u in (raw or "").split(";") if u.strip()]
    # remove pontuação final acidental (.,;)
    urls = [re.sub(r"[.;]+$", "", u) for u in urls]
    return urls

def _fetch_rss(symbol: str) -> List[str]:
    if not USE_RSS_NEWS or not RSS_FEEDS_RAW:
        return []
    terms = [t.lower() for t in SYMBOL_QUERY.get(symbol, symbol.replace("USDT","")).split(" OR ")]
    titles: List[str] = []
    cutoff = _since_dt()
    for url in _clean_urls(RSS_FEEDS_RAW):
        try:
            # pedir conteúdo com UA e repassar ao feedparser (alguns sites bloqueiam UA padrão)
            resp = requests.get(url, headers=USER_AGENT_HDRS, timeout=NEWS_TIMEOUT)
            resp.raise_for_status()
            fp = feedparser.parse(resp.content)
            for e in fp.entries[:NEWS_LIMIT]:
                title = (getattr(e, "title", "") or "").strip()
                if not title: continue
                # filtra por data se disponível
                ts = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
                if ts:
                    dt = datetime(ts.tm_year, ts.tm_mon, ts.tm_mday, ts.tm_hour, ts.tm_min, ts.tm_sec, tzinfo=timezone.utc)
                    if dt < cutoff: 
                        continue
                lt = title.lower()
                if any(term in lt for term in terms):
                    titles.append(title)
        except Exception as ex:
            print(f"⚠️ RSS falhou {url}: {ex}")
    return titles

# ---------- API pública ----------
def get_recent_news(symbol: str, lookback_hours: int = None) -> List[str]:
    """Retorna títulos recentes para o símbolo (TheNewsAPI + RSS, com dedupe)."""
    if lookback_hours is not None:
        # permite override por chamada
        global NEWS_LOOKBACK_H
        NEWS_LOOKBACK_H = int(lookback_hours)

    out = []
    out += _fetch_newsapi(symbol)
    out += _fetch_rss(symbol)
    ded = _dedupe(out)[:NEWS_LIMIT]
    print(f"🗞️ news_fetcher: {symbol} → {len(ded)} títulos (antes {len(out)})")
    return ded
