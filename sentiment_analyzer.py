# sentiment_analyzer.py
# Sentimento combinado: CryptoPanic (news) + Twitter (v2 search recent).
# Saída SEMPRE: {"score": float(0..100), "news_n": int, "tw_n": int}

import os, time, json, math, urllib.parse, urllib.request, ssl
from typing import Dict

# ---- Config via ENV ----
NEWS_USE = os.getenv("NEWS_USE", "true").lower() == "true"
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "").strip()
NEWS_LOOKBACK_MIN = int(os.getenv("NEWS_LOOKBACK_MIN", "180"))
NEWS_MAX_ITEMS = int(os.getenv("NEWS_MAX_ITEMS", "30"))
WEIGHT_NEWS = float(os.getenv("WEIGHT_NEWS", "1.0"))

TWITTER_USE = os.getenv("TWITTER_USE", "true").lower() == "true"
TWITTER_BEARER = os.getenv("TWITTER_BEARER_TOKEN", "").strip()
TWITTER_LOOKBACK_MIN = int(os.getenv("TWITTER_LOOKBACK_MIN", "120"))
TWITTER_MAX_TWEETS = int(os.getenv("TWITTER_MAX_TWEETS", "80"))
TWITTER_LANGS = os.getenv("TWITTER_LANGS", "en,pt").split(",")
WEIGHT_TW = float(os.getenv("WEIGHT_TW", "1.0"))

COMBINE_T_OVER_S = float(os.getenv("MIX_TECH_OVER_SENT", "1.5"))  # usado só pelo main, deixo aqui por padrão

# SSL
_CTX = ssl.create_default_context()

def _now_ts() -> int:
    return int(time.time())

def _since_minutes_to_iso(minutes: int) -> str:
    # Twitter/CryptoPanic aceitam ISO8601
    t = _now_ts() - minutes * 60
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

# ---------- CryptoPanic ----------
def _fetch_news_score(symbol: str, last_price: float | None) -> tuple[float, int]:
    if not NEWS_USE or not CRYPTOPANIC_API_KEY:
        return 50.0, 0
    # Consulta básica por termo: símbolo puro e nome do par
    # Ex.: BTC, BTCUSDT
    q = f"{symbol} OR {symbol}USDT"
    params = {
        "auth_token": CRYPTOPANIC_API_KEY,
        "filter": "hot",
        "currencies": symbol[:-4] if symbol.endswith("USDT") else symbol,
        "public": "true"
    }
    url = "https://cryptopanic.com/api/developer/v1/posts/?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, context=_CTX, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return 50.0, 0

    posts = data.get("results", [])[:NEWS_MAX_ITEMS]
    if not posts:
        return 50.0, 0

    # Heurística simples: sentiment field (bullish/bearish/neutral) se existir
    pos = neg = 0
    for p in posts:
        s = (p.get("sentiment") or "").lower()
        if "bull" in s or s == "positive":
            pos += 1
        elif "bear" in s or s == "negative":
            neg += 1
    total = pos + neg
    if total == 0:
        return 50.0, len(posts)

    raw = 50.0 + 50.0 * (pos - neg) / max(1, total)
    return max(0.0, min(100.0, raw)), len(posts)

# ---------- Twitter v2 recent search ----------
def _fetch_twitter_score(symbol: str, last_price: float | None) -> tuple[float, int]:
    if not TWITTER_USE or not TWITTER_BEARER:
        return 50.0, 0

    if symbol.endswith("USDT"):
        base = symbol[:-4]
    else:
        base = symbol

    # Query simples evitando spam de 100% casadas:
    query = f"({base} OR {base}USDT OR #{base}) lang:en OR lang:pt -is:retweet"
    params = {
        "query": query,
        "max_results": min(100, max(10, TWITTER_MAX_TWEETS)),
        "start_time": _since_minutes_to_iso(TWITTER_LOOKBACK_MIN),
        "tweet.fields": "lang,public_metrics,created_at"
    }
    url = "https://api.twitter.com/2/tweets/search/recent?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {TWITTER_BEARER}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, context=_CTX, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return 50.0, 0

    tweets = data.get("data", [])
    if not tweets:
        return 50.0, 0

    # Sinal muito leve com base em engajamento: (likes + 2*retweets) balanceado
    score = 50.0
    tot = 0
    for t in tweets:
        pm = t.get("public_metrics", {})
        like = pm.get("like_count", 0)
        rt = pm.get("retweet_count", 0)
        rep = pm.get("reply_count", 0)
        val = like + 2*rt + 0.5*rep
        score += 0.02 * val   # escala baixa para não explodir
        tot += 1

    score = max(0.0, min(100.0, score))
    return score, tot

# ---------- API PÚBLICA ----------
def get_sentiment_for_symbol(symbol: str, last_price: float | None = None) -> Dict:
    news_score, news_n = _fetch_news_score(symbol, last_price)
    tw_score, tw_n = _fetch_twitter_score(symbol, last_price)

    # Combinação simples média ponderada (ambos default 1.0)
    parts = []
    wsum = 0.0
    if news_n > 0 or NEWS_USE:
        parts.append((news_score, WEIGHT_NEWS))
        wsum += WEIGHT_NEWS
    if tw_n > 0 or TWITTER_USE:
        parts.append((tw_score, WEIGHT_TW))
        wsum += WEIGHT_TW

    if not parts or wsum == 0:
        combined = 50.0
    else:
        combined = sum(s*w for s, w in parts) / wsum

    return {"score": float(max(0.0, min(100.0, combined))),
            "news_n": int(news_n),
            "tw_n": int(tw_n)}
