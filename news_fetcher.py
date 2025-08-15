# -*- coding: utf-8 -*-
"""
news_fetcher.py — integração com NewsData.io
Retorna somente os TÍTULOS para uso no sentiment_analyzer.get_recent_news(symbol).

Env vars aceitas:
- NEWS_API_KEY           (obrigatória)
- NEWS_LOOKBACK_HOURS    (default 24)
- NEWS_MAX_PER_SOURCE    (default 5)   -> limite por fonte
- NEWS_TIMEOUT           (default 10)  -> seconds
- NEWS_LANGS             (default "en,pt") -> línguas separadas por vírgula
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
import requests

API_KEY = os.getenv("NEWS_API_KEY", "").strip()
API_URL = "https://newsdata.io/api/1/news"

LOOKBACK_HOURS = int(os.getenv("NEWS_LOOKBACK_HOURS", "24"))
MAX_PER_SOURCE = int(os.getenv("NEWS_MAX_PER_SOURCE", "5"))
REQ_TIMEOUT    = int(os.getenv("NEWS_TIMEOUT", "10"))
LANGS          = [s.strip() for s in os.getenv("NEWS_LANGS", "en,pt").split(",") if s.strip()]

# Mapeia símbolo -> termos de busca mais eficazes
_SYMBOL_KEYWORDS: Dict[str, List[str]] = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth"],
    "BNBUSDT": ["binance coin", "bnb"],
    "XRPUSDT": ["xrp", "ripple"],
    "ADAUSDT": ["cardano", "ada"],
    "DOGEUSDT": ["dogecoin", "doge"],
    "SOLUSDT": ["solana", "sol"],
    "MATICUSDT": ["polygon", "matic"],
    "DOTUSDT": ["polkadot", "dot"],
    "LTCUSDT": ["litecoin", "ltc"],
    "LINKUSDT": ["chainlink", "link"],
    "USDTUSDT": ["tether", "usdt"],
    "USDCUSDT": ["usd coin", "usdc"],
}

def _keywords_for(symbol: str) -> List[str]:
    sym = (symbol or "").upper().strip()
    if sym in _SYMBOL_KEYWORDS:
        return _SYMBOL_KEYWORDS[sym]
    base = sym.replace("USDT", "").replace("USD", "")
    if base:
        return [base.lower()]
    return [sym.lower()]

def _iso_date(dt: datetime) -> str:
    # NewsData aceita from_date/to_date em YYYY-MM-DD
    return dt.strftime("%Y-%m-%d")

def _within_lookback(pub_iso: str, now: datetime) -> bool:
    # NewsData costuma retornar pubDate ISO, ex: "2025-08-14 21:10:00"
    try:
        pub_iso = pub_iso.replace("Z", "")
        # tenta formatos mais comuns
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(pub_iso[:19], fmt).replace(tzinfo=timezone.utc)
                break
            except Exception:
                continue
        else:
            return True  # se não parsear, não bloqueia pelo tempo
        return (now - dt) <= timedelta(hours=LOOKBACK_HOURS)
    except Exception:
        return True

def _dedupe_keep_limit_by_source(rows: List[dict]) -> List[str]:
    """
    Remove duplicados por título e limita quantidade por fonte.
    Retorna somente títulos.
    """
    by_source: Dict[str, int] = {}
    seen_titles = set()
    titles: List[str] = []
    for r in rows:
        title = (r.get("title") or "").strip()
        if not title:
            continue
        key = title.lower()
        if key in seen_titles:
            continue
        source_id = (r.get("source_id") or r.get("source") or "unknown").lower()
        count = by_source.get(source_id, 0)
        if count >= MAX_PER_SOURCE > 0:
            continue
        by_source[source_id] = count + 1
        seen_titles.add(key)
        titles.append(title)
    return titles

def _fetch_page(q: str, page: Optional[str], from_date: str, to_date: str) -> dict:
    params = {
        "apikey": API_KEY,
        "q": q,
        "language": ",".join(LANGS),
        "from_date": from_date,
        "to_date": to_date,
    }
    if page:
        params["page"] = page
    r = requests.get(API_URL, params=params, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    return r.json()

def get_recent_news(symbol: str) -> List[str]:
    """
    Retorna lista de TÍTULOS recentes para o símbolo.
    Usa OR entre palavras-chave (ex: "bitcoin OR btc").
    Pagina até 3 páginas ou até 60 resultados brutos, filtra janela de LOOKBACK_HOURS
    e limita por fonte.
    """
    if not API_KEY:
        print("⚠️ NEWS_API_KEY não definido. Devolvendo lista vazia.")
        return []

    now = datetime.now(timezone.utc)
    from_date = _iso_date(now - timedelta(hours=LOOKBACK_HOURS))
    to_date   = _iso_date(now)

    # monta consulta: "bitcoin OR btc"
    terms = _keywords_for(symbol)
    query = " OR ".join(terms)

    all_rows: List[dict] = []
    next_page: Optional[str] = None
    pages = 0

    while pages < 3:  # segura a mão no free tier
        try:
            data = _fetch_page(query, next_page, from_date, to_date)
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", "?")
            txt = ""
            try:
                txt = e.response.text
            except Exception:
                pass
            print(f"⚠️ NewsData HTTP {status}: {txt[:200]}")
            break
        except Exception as e:
            print(f"⚠️ NewsData erro de rede: {e}")
            break

        results = data.get("results") or []
        for it in results:
            # filtra por janela de tempo quando possível
            if _within_lookback(str(it.get("pubDate", "")), now):
                all_rows.append(it)

        next_page = data.get("nextPage")
        pages += 1
        # corta se já há bastante material bruto
        if len(all_rows) >= 60 or not next_page:
            break

        # leve pausa para não dar flood
        time.sleep(0.5)

    titles = _dedupe_keep_limit_by_source(all_rows)
    if not titles:
        print(f"ℹ️ NewsData: sem títulos para {symbol} (query='{query}')")
    else:
        print(f"📰 NewsData: {len(titles)} títulos para {symbol} (query='{query}')")
    return titles
