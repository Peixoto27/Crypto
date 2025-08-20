# -*- coding: utf-8 -*-
"""
sentiment_analyzer.py — coleta sentimento de News + Twitter com fallbacks e logs claros.

ENV principais:
  NEWS_USE=true|false
  NEWS_LOOKBACK_MIN=180
  NEWS_MAX_ITEMS=40
  NEWS_LANGS=pt,en
  CRYPTOPANIC_TOKEN=6af8578b33d24fed33f1e3c4be99df7d5bfc9c60            # opcional (fallback de notícias)

  TWITTER_USE=true|false
  TWITTER_BEARER_TOKEN=AAAAAAAAAAAAAAAAAAAAAJyg3gEAAAAAIttc5n2QQTH2nZPp%2FQy0tT6kRAI%3Db8J9ALE3cVLQC4h3gusWWq7XLEXJ7OVJgk2ep7SkGjf7Wr8aWk   # necessário para Twitter
  TWITTER_LOOKBACK_MIN=120
  TWITTER_MAX_TWEETS=60
  TWITTER_LANGS=pt,en

Pesos (no mix dentro do main):
  WEIGHT_TECH, WEIGHT_SENT (definidos no main)

Retorno: {"score": 0..1, "news_n": int, "tw_n": int}
"""
from __future__ import annotations
import os, json, math, time, re
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# -------------------- utils/ENV --------------------
def _get(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    return v if v is not None else default

def _to_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1","true","yes","on")

def _now_utc() -> datetime:
    return datetime.utcnow()

def _ts() -> str:
    return _now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")

def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]

# -------------------- dicionários simples p/ polaridade --------------------
POS = {"surge","pump","bull","breakout","partnership","upgrade","launch","funding",
       "approval","win","record","all-time high","ath","rise","rally","acquisition",
       "integration","support","positive","otimista","alta","subindo","parceria","suporte"}
NEG = {"dump","bear","hack","exploit","lawsuit","ban","delist","halt","probe","scam",
       "down","fall","drop","crash","delay","bug","vulnerab","negative","queda","bloqueio",
       "investigation","fraud","penalty","fine","stop","suspens","interrup"}

_word = re.compile(r"[a-zA-Zçáéíóúàãõâêô]+", re.IGNORECASE)

def _score_text(text: str) -> float:
    if not text:
        return 0.0
    t = text.lower()
    pos = sum(1 for m in _word.findall(t) if any(p in m for p in POS))
    neg = sum(1 for m in _word.findall(t) if any(n in m for n in NEG))
    if pos == 0 and neg == 0:
        return 0.0
    # mapear para [-1,1]
    raw = (pos - neg) / (pos + neg)
    return max(-1.0, min(1.0, raw))

def _aggregate_scores(items: list[dict], key_fields=("title","text","description")) -> float:
    if not items:
        return 0.0
    vals = []
    for it in items:
        chunk = " ".join(str(it.get(k,"")) for k in key_fields)
        vals.append(_score_text(chunk))
    # média com atenuação por outliers
    if not vals:
        return 0.0
    vals.sort()
    n = len(vals)
    cut = max(0, n//10)  # descarta 10% extremos de cada lado
    trimmed = vals[cut:n-cut] if n >= 10 else vals
    return sum(trimmed) / len(trimmed)

# -------------------- News: usa módulo local se existir; fallback CryptoPanic --------------------
def _news_collect(symbol: str, lookback_min: int, max_items: int, langs: list[str]) -> list[dict]:
    if not _to_bool(_get("NEWS_USE","true")):
        print(f"[NEWS] desativado por ENV (NEWS_USE=false).")
        return []

    # 1) tenta módulo local (news_fetcher)
    try:
        import importlib
        nf = importlib.import_module("news_fetcher")  # type: ignore
        if hasattr(nf, "get_news_for_symbol"):
            items = nf.get_news_for_symbol(symbol, lookback_min=lookback_min,
                                           max_items=max_items, langs=langs)  # type: ignore
            if isinstance(items, list):
                return items[:max_items]
    except Exception as e:
        print(f"[NEWS] módulo news_fetcher indisponível/erro: {e}. Vou tentar CryptoPanic.")

    # 2) fallback: CryptoPanic (se token existir)
    token = _get("CRYPTOPANIC_TOKEN","").strip()
    if not token:
        print("[NEWS] Sem CRYPTOPANIC_TOKEN; retornando vazio (n=0).")
        return []
    # monta consulta
    until = _now_utc()
    since = until - timedelta(minutes=lookback_min)
    q = f"{symbol.replace('USDT','')}"
    url = (f"https://cryptopanic.com/api/v1/posts/?auth_token={token}"
           f"&currencies={q}&filter=rising&public=true")
    try:
        req = Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8","ignore")
            data = json.loads(raw)
            out = []
            for p in data.get("results", [])[:max_items]:
                out.append({
                    "title": p.get("title",""),
                    "text": p.get("domain",""),
                    "published_at": p.get("published_at",""),
                    "url": p.get("url",""),
                })
            return out
    except HTTPError as e:
        print(f"[NEWS] HTTP {e.code} CryptoPanic; retornando n=0.")
    except URLError as e:
        print(f"[NEWS] ERRO rede CryptoPanic: {e}; n=0.")
    except Exception as e:
        print(f"[NEWS] ERRO CryptoPanic: {e}; n=0.")
    return []

# -------------------- Twitter: API v2 recent search --------------------
def _twitter_collect(symbol: str, lookback_min: int, max_tweets: int, langs: list[str]) -> list[dict]:
    if not _to_bool(_get("TWITTER_USE","true")):
        print(f"[TW] desativado por ENV (TWITTER_USE=false).")
        return []
    bearer = _get("TWITTER_BEARER_TOKEN","").strip()
    if not bearer:
        print("[TW] Sem TWITTER_BEARER_TOKEN; n=0.")
        return []
    query_sym = symbol.replace("USDT","")
    query = f"({query_sym} OR #{query_sym}) lang:{' OR lang:'.join(langs)} -is:retweet"
    end = _now_utc()
    start = end - timedelta(minutes=lookback_min)
    url = ("https://api.twitter.com/2/tweets/search/recent"
           f"?query={re.sub(r'\\s+','%20',query)}"
           f"&max_results={min(100, max_tweets)}"
           f"&tweet.fields=created_at,lang,public_metrics,source,context_annotations,entities")
    try:
        req = Request(url, headers={"Authorization": f"Bearer {bearer}",
                                    "User-Agent":"Mozilla/5.0"})
        with urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8","ignore")
            data = json.loads(raw)
            out = []
            for t in data.get("data", []):
                out.append({
                    "title": "",
                    "text": t.get("text",""),
                    "created_at": t.get("created_at",""),
                    "lang": t.get("lang",""),
                })
            return out[:max_tweets]
    except HTTPError as e:
        print(f"[TW] HTTP {e.code} Twitter; n=0.")
    except URLError as e:
        print(f"[TW] ERRO rede Twitter: {e}; n=0.")
    except Exception as e:
        print(f"[TW] ERRO Twitter: {e}; n=0.")
    return []

# -------------------- API pública --------------------
def get_sentiment_for_symbol(symbol: str) -> dict:
    """
    Retorna dict com:
      score  -> [0..1]
      news_n -> int
      tw_n   -> int
    """
    # parâmetros
    news_lb   = int(_get("NEWS_LOOKBACK_MIN","180"))
    news_max  = int(_get("NEWS_MAX_ITEMS","40"))
    news_lang = _split_csv(_get("NEWS_LANGS","pt,en"))

    tw_lb     = int(_get("TWITTER_LOOKBACK_MIN","120"))
    tw_max    = int(_get("TWITTER_MAX_TWEETS","60"))
    tw_lang   = _split_csv(_get("TWITTER_LANGS","pt,en"))

    # coletar
    news_items = _news_collect(symbol, news_lb, news_max, news_lang)
    tw_items   = _twitter_collect(symbol, tw_lb, tw_max, tw_lang)

    news_n = len(news_items)
    tw_n   = len(tw_items)

    # scorers
    news_s = _aggregate_scores(news_items, key_fields=("title","text"))
    tw_s   = _aggregate_scores(tw_items,   key_fields=("text",))

    # mapeia de [-1,1] → [0,1]
    def _to01(x: float) -> float: return 0.5 + 0.5*max(-1.0, min(1.0, x))

    news_01 = _to01(news_s)
    tw_01   = _to01(tw_s)

    # pesos internos (ajuste leve)
    w_news = 0.6 if news_n > 0 else 0.0
    w_tw   = 0.4 if tw_n   > 0 else 0.0
    if w_news + w_tw == 0:
        score = 0.5
    else:
        score = (news_01*w_news + tw_01*w_tw) / (w_news + w_tw)

    print(f"[SENT] {symbol}: news n={news_n}, tw n={tw_n}, score={round(score*100,1)}%")
    return {"score": float(score), "news_n": int(news_n), "tw_n": int(tw_n)}

if __name__ == "__main__":
    # pequeno teste manual
    sym = os.getenv("TEST_SYMBOL","BTCUSDT")
    print(get_sentiment_for_symbol(sym))
