# -*- coding: utf-8 -*-
"""
sentiment_analyzer.py — NewsData + (opcional) Twitter, com cache e orçamento.

ENV esperadas (além das que você já tem de News):
  # Orçamento
  NEWS_MONTHLY_BUDGET=100
  NEWS_CALLS_PER_CYCLE_MAX=1

  # Janela/cache
  NEWS_LOOKBACK_HOURS=12
  NEWS_CACHE_TTL_MIN=720
  NEWS_CACHE_FILE=news_cache.json

  # Gates para chamar news
  NEWS_SCORE_ONLY_IF_TECH_ABOVE=0.55
  NEWS_MIN_PRICE_MOVE_PCT=1.0

  # NewsData
  NEWS_API_URL=https://newsdata.io/api/1/news
  NEWS_API_KEY=pub_xxx
  NEWS_MAX_PER_SOURCE=5
  NEWS_TIMEOUT=8.0
  NEWS_LANGS=en,pt
  NEWS_CATEGORY=business,technology

  # Twitter (opcional)
  TWITTER_USE=true
  TWITTER_BEARER_TOKEN=AAAAAAAA...
  TWITTER_LOOKBACK_MIN=120
  TWITTER_MAX_TWEETS=80
  TWITTER_TIMEOUT=20
  TWITTER_HOURLY_LIMIT=60
  TWITTER_LANGS=en,pt
  TWITTER_CACHE_TTL=900

  # Pesos de mistura do sentimento
  WEIGHT_SENT_NEWS=1.0
  WEIGHT_SENT_TWITTER=0.5
"""

import os, time, json, math
import urllib.parse
import urllib.request

from datetime import datetime, timedelta

from news_budget import NewsBudget

# ----------------- helpers -----------------

def _now_utc():
    return datetime.utcnow()

def _ts():
    return _now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")

def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

# --------------- cache local ---------------

class TimedCache:
    def __init__(self, path, ttl_sec):
        self.path = path
        self.ttl = ttl_sec
        self.data = _load_json(path, {})

    def get(self, key):
        obj = self.data.get(key)
        if not obj: return None
        exp = obj.get("_exp", 0)
        if time.time() > exp:
            self.data.pop(key, None)
            return None
        return obj.get("value")

    def set(self, key, value):
        self.data[key] = {"_exp": time.time() + self.ttl, "value": value}
        try:
            _save_json(self.path, self.data)
        except Exception:
            pass

# --------------- Sentiment runtime ----------

class SentimentRuntime:
    def __init__(self):
        # News env
        self.api_url   = os.getenv("NEWS_API_URL", "https://newsdata.io/api/1/news")
        self.api_key   = os.getenv("NEWS_API_KEY", "")
        self.langs     = os.getenv("NEWS_LANGS", "en,pt")
        self.category  = os.getenv("NEWS_CATEGORY", "business,technology")
        self.max_per   = int(os.getenv("NEWS_MAX_PER_SOURCE", "5"))
        self.timeout   = float(os.getenv("NEWS_TIMEOUT", "8.0"))
        self.lookback_h= int(os.getenv("NEWS_LOOKBACK_HOURS", "12"))
        self.cache_ttl = int(os.getenv("NEWS_CACHE_TTL_MIN", "720")) * 60
        self.cache     = TimedCache(os.getenv("NEWS_CACHE_FILE", "news_cache.json"), self.cache_ttl)

        # Gates
        self.gate_min_tech   = float(os.getenv("NEWS_SCORE_ONLY_IF_TECH_ABOVE", "0.55"))
        self.gate_min_move   = float(os.getenv("NEWS_MIN_PRICE_MOVE_PCT", "1.0"))

        # Pesos
        self.w_news   = float(os.getenv("WEIGHT_SENT_NEWS", "1.0"))
        self.w_tw     = float(os.getenv("WEIGHT_SENT_TWITTER", "0.5"))

        # Budget
        self.budget = NewsBudget()

        # Twitter
        self.tw_use   = os.getenv("TWITTER_USE", "false").lower() in ("1","true","yes")
        self.tw_token = os.getenv("TWITTER_BEARER_TOKEN", "")
        self.tw_look  = int(os.getenv("TWITTER_LOOKBACK_MIN", "120"))
        self.tw_max   = int(os.getenv("TWITTER_MAX_TWEETS", "80"))
        self.tw_limit = int(os.getenv("TWITTER_HOURLY_LIMIT", "60"))
        self.tw_timeout = int(os.getenv("TWITTER_TIMEOUT", "20"))
        self.tw_langs = os.getenv("TWITTER_LANGS", "en,pt")
        self.tw_cache = TimedCache("twitter_cache.json", int(os.getenv("TWITTER_CACHE_TTL", "900")))

    # ---------- ciclo ----------
    def new_cycle(self):
        self.budget.new_cycle()

    # ---------- gates ----------
    def _should_fetch_news(self, tech_score, last_close, curr_close):
        if tech_score is not None and tech_score < self.gate_min_tech:
            return False
        try:
            if last_close and curr_close and last_close > 0:
                move = abs(curr_close - last_close) / last_close * 100.0
                return move >= self.gate_min_move
        except Exception:
            pass
        return True  # se não sei, deixo passar

    # ---------- NewsData ----------
    def _q(self, params: dict) -> str:
        return self.api_url + "?" + urllib.parse.urlencode(params)

    def _fetch_news_raw(self, symbol):
        since = (_now_utc() - timedelta(hours=self.lookback_h)).isoformat(timespec="seconds") + "Z"
        q = {
            "apikey": self.api_key,
            "q": symbol.replace("USDT",""),
            "language": self.langs,
            "category": self.category,
            "from_date": since,
            "page": 1
        }
        url = self._q(q)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _score_from_news(self, payload) -> float:
        """
        Heurística simples: conta termos positivos/negativos nos títulos/descrições.
        Retorna [0..1].
        """
        if not payload or payload.get("status") != "success":
            return 0.5  # neutro
        items = payload.get("results", [])[: self.max_per]
        if not items:
            return 0.5

        pos = neg = 0
        POS = ("surge","rally","bull","gain","partnership","approve","record","growth","up")
        NEG = ("fall","drop","bear","hack","exploit","ban","lawsuit","downgrade","down")
        for it in items:
            txt = (" ".join([it.get("title",""), it.get("description","")])).lower()
            pos += sum(1 for w in POS if w in txt)
            neg += sum(1 for w in NEG if w in txt)
        total = pos + neg
        if total == 0: return 0.5
        return max(0.0, min(1.0, pos/total))

    def get_news_sentiment(self, symbol, tech_score=None, last_close=None, curr_close=None):
        """Respeita gates + orçamento + cache. Retorna (score, src_str)."""
        cache_key = f"news:{symbol}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return float(cached), "news-cache"

        if not self._should_fetch_news(tech_score, last_close, curr_close):
            return 0.5, "news-skipped-gate"

        if not self.budget.allow_call():
            return 0.5, "news-skipped-budget"

        try:
            payload = self._fetch_news_raw(symbol)
            score = self._score_from_news(payload)
            self.cache.set(cache_key, score)
            self.budget.consume()
            rem = self.budget.remaining_month()
            print(f"📰 News OK {symbol} | score={round(score*100,1)}% | restante mês={rem}")
            return score, "news-api"
        except Exception as e:
            print(f"📰⚠️ News falhou {symbol}: {e}")
            return 0.5, "news-error"

    # ---------- Twitter ----------
    def _twitter_headers(self):
        return {"Authorization": f"Bearer {self.tw_token}", "User-Agent": "Mozilla/5.0"}

    def _fetch_tweets(self, symbol):
        # Busca simples por '$BTC' e 'bitcoin' etc.
        base = "https://api.twitter.com/2/tweets/search/recent"
        query = f"({symbol.replace('USDT','')} OR ${symbol.replace('USDT','')}) lang:en -is:retweet"
        start = (_now_utc() - timedelta(minutes=self.tw_look)).isoformat(timespec="seconds") + "Z"
        params = {
            "query": query,
            "max_results": min(100, max(10, self.tw_max)),
            "start_time": start,
            "tweet.fields": "lang,created_at"
        }
        url = base + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._twitter_headers())
        with urllib.request.urlopen(req, timeout=self.tw_timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _score_from_tweets(self, payload):
        if not payload or "data" not in payload:
            return 0.5
        tweets = payload["data"]
        if not tweets:
            return 0.5
        POS = ("moon","pump","bull","breakout","win","up","green","buy")
        NEG = ("dump","bear","crash","rekt","down","red","sell")
        pos = neg = 0
        for t in tweets:
            txt = t.get("text","").lower()
            pos += sum(1 for w in POS if w in txt)
            neg += sum(1 for w in NEG if w in txt)
        total = pos + neg
        if total == 0: return 0.5
        return max(0.0, min(1.0, pos/total))

    def get_twitter_sentiment(self, symbol):
        if not self.tw_use or not self.tw_token:
            return 0.5, "tw-disabled"

        ck = f"tw:{symbol}"
        cv = self.tw_cache.get(ck)
        if cv is not None:
            return float(cv), "tw-cache"
        try:
            payload = self._fetch_tweets(symbol)
            score = self._score_from_tweets(payload)
            self.tw_cache.set(ck, score)
            print(f"🐦 X OK {symbol} | score={round(score*100,1)}%")
            return score, "tw-api"
        except Exception as e:
            print(f"🐦⚠️ X falhou {symbol}: {e}")
            return 0.5, "tw-error"

    # ---------- API pública p/ main ----------
    def get_sentiment_score(self, symbol, tech_score=None, last_close=None, curr_close=None):
        """
        Retorna float [0..1] de sentimento combinado (News + opcional Twitter)
        e um dict info p/ log.
        """
        news_s, news_src = self.get_news_sentiment(symbol, tech_score, last_close, curr_close)
        tw_s, tw_src = self.get_twitter_sentiment(symbol)

        # mistura
        num = self.w_news*news_s + self.w_tw*tw_s
        den = (self.w_news if news_s is not None else 0.0) + (self.w_tw if tw_s is not None else 0.0)
        sent = num/den if den > 0 else 0.5

        info = {
            "news_src": news_src, "tw_src": tw_src,
            "news": round(news_s*100, 1), "twitter": round(tw_s*100, 1),
            "mix": round(sent*100, 1)
        }
        return sent, info

# Singleton simples
_RUNTIME = None

def init_sentiment_runtime():
    global _Runtime, _RUNTIME
    _RUNTIME = SentimentRuntime()
    return _RUNTIME

def runtime():
    return _RUNTIME or init_sentiment_runtime()
