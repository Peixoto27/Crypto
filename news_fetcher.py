# -*- coding: utf-8 -*-
import os, time, json, requests
from datetime import datetime, timezone
from requests.utils import quote

THENEWSAPI_KEY   = os.getenv("THENEWSAPI_KEY", "")
NEWS_CACHE_FILE  = os.getenv("NEWS_CACHE_FILE", "news_cache.json")
NEWS_CACHE_TTL_SEC = int(float(os.getenv("NEWS_CACHE_TTL_SEC", "3600")))  # 1h

KW = {
    "BTCUSDT": ["bitcoin","btc"],
    "ETHUSDT": ["ethereum","eth"],
    "BNBUSDT": ["bnb","binance coin","binance"],
    "XRPUSDT": ["xrp","ripple"],
    "ADAUSDT": ["cardano","ada"],
    "DOGEUSDT": ["dogecoin","doge"],
    "SOLUSDT": ["solana","sol"],
    "MATICUSDT": ["polygon","matic"],
    "DOTUSDT": ["polkadot","dot"],
    "LTCUSDT": ["litecoin","ltc"],
    "LINKUSDT": ["chainlink","link"],
}

def _hours_ago(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z","+00:00")).astimezone(timezone.utc)
        return (datetime.now(timezone.utc)-dt).total_seconds()/3600.0
    except Exception:
        return 999.0

def _load_cache():
    if not os.path.exists(NEWS_CACHE_FILE): return {}
    try:
        with open(NEWS_CACHE_FILE,"r",encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(data):
    with open(NEWS_CACHE_FILE,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)

def _fetch_thenewsapi(query, limit=15):
    if not THENEWSAPI_KEY: return []
    url = (
        "https://api.thenewsapi.com/v1/news/all"
        f"?api_token={THENEWSAPI_KEY}"
        f"&search={quote(query)}"
        "&language=en"
        "&sort=published_at"
        f"&limit={limit}"
    )
    r = requests.get(url, timeout=12)
    if r.status_code != 200:
        return []
    data = r.json().get("data", [])
    titles = []
    for it in data:
        iso = it.get("published_at") or ""
        if _hours_ago(iso) <= 24.0:
            t = (it.get("title") or "").strip()
            if t: titles.append(t)
    return titles

def get_recent_news(symbol: str):
    """
    Retorna lista de TITLES recentes (24h) p/ o símbolo — assinatura usada por sentiment_analyzer.py
    Com cache de 1h para não gastar cota.
    """
    query = " OR ".join(KW.get(symbol, [symbol.replace("USDT","").lower()]))
    key = f"{symbol}::{query}"
    cache = _load_cache()
    node = cache.get(key)
    now = time.time()
    if node and (now - node.get("ts",0)) < NEWS_CACHE_TTL_SEC:
        return node.get("titles", [])
    titles = _fetch_thenewsapi(query, limit=15)
    cache[key] = {"ts": now, "titles": titles}
    _save_cache(cache)
    return titles
