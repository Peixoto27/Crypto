# -*- coding: utf-8 -*-
"""
sentiment_analyzer.py
- Busca notícias no NewsData.io (gratuito) usando NEWS_API_KEY
- Faz cache leve em disco (news_cache.json) para não estourar cota
- Calcula sentimento (-1..1) por símbolo a partir de títulos/descrições
- Respeita janela de tempo e limite por fonte
"""

from __future__ import annotations
import os, json, time, math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
import requests

# =======================
# Config via ENV
# =======================
NEWS_API_KEY        = os.getenv("NEWS_API_KEY", "").strip()
NEWS_LOOKBACK_HOURS = int(os.getenv("NEWS_LOOKBACK_HOURS", "12"))   # janela de busca
NEWS_MAX_PER_SOURCE = int(os.getenv("NEWS_MAX_PER_SOURCE", "5"))    # por fonte
NEWS_TIMEOUT        = float(os.getenv("NEWS_TIMEOUT", "8.0"))       # seg por request
NEWS_LANGS          = os.getenv("NEWS_LANGS", "en,pt").strip()      # idiomas aceitos
NEWS_CATEGORY       = os.getenv("NEWS_CATEGORY", "business,technology").strip()

CACHE_FILE          = os.getenv("NEWS_CACHE_FILE", "news_cache.json")
CACHE_TTL_MIN       = int(os.getenv("NEWS_CACHE_TTL_MIN", "30"))    # reusa por 30min

BASE_URL = "https://newsdata.io/api/1/news"

# =======================
# Mapeamento de símbolos -> termos
# (ajuda a achar a notícia certa)
# =======================
SYMBOL_KEYWORDS: Dict[str, List[str]] = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth"],
    "BNBUSDT": ["binance coin", "bnb", "binancecoin"],
    "XRPUSDT": ["xrp", "ripple"],
    "ADAUSDT": ["cardano", "ada"],
    "SOLUSDT": ["solana", "sol"],
    "DOGEUSDT": ["dogecoin", "doge"],
    "MATICUSDT": ["polygon", "matic"],
    "DOTUSDT": ["polkadot", "dot"],
    "LTCUSDT": ["litecoin", "ltc"],
    "LINKUSDT": ["chainlink", "link"],
    # … adicione se quiser granularidade por alt novas
}

# =======================
# Cache leve em disco
# =======================
def _load_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"created_at": time.time(), "items": {}}

def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass

_cache = _load_cache()

def _cache_get(key: str) -> Any:
    item = _cache["items"].get(key)
    if not item:
        return None
    ts = item.get("_ts", 0)
    if (time.time() - ts) > CACHE_TTL_MIN * 60:
        return None
    return item.get("value")

def _cache_set(key: str, value: Any) -> None:
    _cache["items"][key] = {"_ts": time.time(), "value": value}
    _save_cache(_cache)

# =======================
# Helpers
# =======================
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _since_ts(hr: int) -> str:
    dt = _utc_now() - timedelta(hours=hr)
    # NewsData espera ISO8601 (ele usa published_date como string ISO).
    # Vamos usar como filtro pós-busca (client-side), pois a API não tem "from" padrão.
    return dt.isoformat()

def _clean(text: str) -> str:
    return (text or "").strip().lower()

# =======================
# Busca NewsData.io (com paginação segura)
# =======================
def _fetch_news_newsdata(query: str) -> List[Dict[str, Any]]:
    """
    Busca por 'query' e retorna lista de artigos (cada artigo é dict).
    Respeita paginação sem estourar com 'nextPage' inválido.
    """
    if not NEWS_API_KEY:
        return []

    # cache por query + janela
    cache_key = f"newsdata::{query}::{NEWS_LOOKBACK_HOURS}::{NEWS_LANGS}::{NEWS_CATEGORY}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    results: List[Dict[str, Any]] = []
    page_token = None
    tries = 0
    MAX_PAGES = 3  # segurança: 3 páginas no máx por consulta

    while tries < MAX_PAGES:
        tries += 1
        params = {
            "apikey": NEWS_API_KEY,
            "q": query,
            "language": NEWS_LANGS,           # múltiplos separados por vírgula
            "category": NEWS_CATEGORY,        # múltiplos separados por vírgula
            "page": page_token or "",         # NewsData usa 'page' como token; se None, envia vazio
        }
        # remove param 'page' se não há token pra evitar 422
        if not page_token:
            params.pop("page", None)

        r = requests.get(BASE_URL, params=params, timeout=NEWS_TIMEOUT)
        # Em erros HTTP, pare e use o que tem
        if r.status_code != 200:
            # print(f"NewsData HTTP {r.status_code}: {r.text[:200]}")
            break

        data = r.json()
        batch = data.get("results") or []
        results.extend(batch)

        page_token = data.get("nextPage")
        if not page_token:
            break

    # Guardar no cache
    _cache_set(cache_key, results)
    return results

# =======================
# Sentimento: léxico leve
# =======================
_POS = set("""
surge rally breakout bullish upgrade approval partnership adoption record profit
soar jump rise pump optimistic growth positive win wins winning secure expand
""".split())

_NEG = set("""
hack exploit vulnerability lawsuit ban halt downgrade outage delay bearish dump
fall drop plunge loss losses negative fear fraud scam shutdown crash risk warning
""".split())

def _score_text(text: str) -> float:
    """
    Nota -1..1 baseada em contagem de palavras simples.
    """
    if not text:
        return 0.0
    t = _clean(text)
    if not t:
        return 0.0
    words = [w.strip(".,!?()[]{}:;\"'") for w in t.split()]
    pos = sum(1 for w in words if w in _POS)
    neg = sum(1 for w in words if w in _NEG)
    if pos == 0 and neg == 0:
        return 0.0
    raw = (pos - neg) / float(pos + neg)
    return float(max(-1.0, min(1.0, raw)))

def _combine_scores(scores: List[float]) -> float:
    if not scores:
        return 0.0
    # média com saturação leve
    avg = sum(scores) / len(scores)
    return float(max(-1.0, min(1.0, avg)))

# =======================
# Público: get_sentiment_score
# =======================
def get_sentiment_score(symbol: str) -> float:
    """
    Retorna sentimento da última janela para um símbolo.
    -1..1 (negativo..positivo). 0.0 se não encontrou nada.
    """
    if not NEWS_API_KEY:
        # sem chave → neutro
        return 0.0

    # termos de busca
    terms = SYMBOL_KEYWORDS.get(symbol.upper(), [])
    if not terms:
        # fallback: remove USDT e usa o "nome cru"
        base = symbol.upper().replace("USDT", "").strip()
        if base:
            terms = [base]
    # faz uma única query combinada com OR (ex: "bitcoin OR btc")
    q = " OR ".join(terms) if terms else symbol

    articles = _fetch_news_newsdata(q)

    # filtrar por tempo e limitar por fonte
    since_iso = _since_ts(NEWS_LOOKBACK_HOURS)
    since_dt = datetime.fromisoformat(since_iso)

    by_source_count: Dict[str, int] = {}
    scores: List[float] = []

    for art in articles:
        # published_date pode vir como ISO; tratar ausências
        p = art.get("pubDate") or art.get("published_date") or art.get("pub_date")
        try:
            adt = datetime.fromisoformat(p.replace("Z","+00:00")) if p else None
        except Exception:
            adt = None
        if adt and adt.tzinfo is None:
            adt = adt.replace(tzinfo=timezone.utc)

        if adt and adt < since_dt:
            continue

        src = (art.get("source_id") or art.get("source") or "unknown").lower()
        by_source_count[src] = by_source_count.get(src, 0) + 1
        if by_source_count[src] > NEWS_MAX_PER_SOURCE:
            continue

        title = art.get("title") or ""
        desc  = art.get("description") or art.get("summary") or ""
        s = _score_text(title) * 0.7 + _score_text(desc) * 0.3
        scores.append(s)

    return _combine_scores(scores)
