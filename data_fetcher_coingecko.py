# -*- coding: utf-8 -*-
"""
data_fetcher_coingecko.py
Usa cg_ids.json para mapear pares -> id do CoinGecko.
Se faltar um par, resolve pela coinlist e atualiza cg_ids.json.
Tem backoff/espera para reduzir 429.
"""

import os, json, time, requests
from typing import List, Dict, Any

API_DELAY_OHLC  = float(os.getenv("API_DELAY_OHLC", "12.0"))
MAX_RETRIES     = int(os.getenv("MAX_RETRIES", "6"))
BACKOFF_BASE    = float(os.getenv("BACKOFF_BASE", "2.5"))
OHLC_DAYS       = int(os.getenv("OHLC_DAYS", "14"))

CG_IDS_FILE         = os.getenv("CG_IDS_FILE", "cg_ids.json")
COINLIST_CACHE_FILE = os.getenv("CG_COINLIST_CACHE", "cg_coinlist_cache.json")
AUTOREFRESH_IDS     = os.getenv("CG_IDS_AUTOREFRESH", "1") == "1"

API_BASE = "https://api.coingecko.com/api/v3"
TIMEOUT = 30
_last_call_ts = 0.0

def _respect_rate_limit():
    global _last_call_ts
    now = time.time()
    diff = now - _last_call_ts
    if diff < API_DELAY_OHLC:
        time.sleep(API_DELAY_OHLC - diff)
    _last_call_ts = time.time()

def _get_json(url: str, params: Dict[str, Any] = None) -> Any:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _respect_rate_limit()
            r = requests.get(url, params=params or {}, timeout=TIMEOUT)
            if r.status_code == 429:
                wait = max(API_DELAY_OHLC, round(API_DELAY_OHLC * (BACKOFF_BASE ** (attempt - 1))))
                print(f"⚠️ 429 CG: aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            wait = max(3.0, round(API_DELAY_OHLC * (BACKOFF_BASE ** (attempt - 1))))
            print(f"⚠️ Erro CG: {e}. Aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
    return None

def _load_cg_ids() -> Dict[str, str]:
    if os.path.exists(CG_IDS_FILE):
        try:
            with open(CG_IDS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                return {k.upper(): v for k, v in raw.items()}
        except Exception:
            pass
    return {}

def _save_cg_ids(mapper: Dict[str, str]) -> None:
    try:
        with open(CG_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(mapper, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _load_coinlist() -> List[dict]:
    if os.path.exists(COINLIST_CACHE_FILE):
        try:
            with open(COINLIST_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    data = _get_json(f"{API_BASE}/coins/list") or []
    try:
        with open(COINLIST_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return data

def _resolve_id(symbol: str, coinlist: List[dict]) -> str:
    base = symbol.upper().replace("USDT", "").replace("USD", "").lower()
    # 1) symbol exato
    for c in coinlist:
        if (c.get("symbol") or "").lower() == base:
            return c.get("id")
    # 2) id contém base
    for c in coinlist:
        cid = (c.get("id") or "").lower()
        if base and base in cid:
            return c.get("id")
    return ""

def _get_cg_id(symbol: str) -> str:
    mapper = _load_cg_ids()
    key = symbol.upper()
    if key in mapper and mapper[key]:
        return mapper[key]

    if not AUTOREFRESH_IDS:
        print(f"⚠️ Sem mapeamento CoinGecko para {symbol}. Adicione em {CG_IDS_FILE}.")
        return ""

    # tenta resolver e persistir
    coinlist = _load_coinlist()
    cid = _resolve_id(symbol, coinlist)
    if cid:
        mapper[key] = cid
        _save_cg_ids(mapper)
        print(f"🔄 CG_IDS atualizado: {symbol} -> {cid}")
        return cid

    print(f"⚠️ Não foi possível mapear {symbol}.")
    return ""

# --------- Públicos usados no main ---------

def fetch_top_symbols(limit: int = 100) -> List[str]:
    data = _get_json(f"{API_BASE}/coins/markets", {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": min(250, max(1, limit)),
        "page": 1,
        "sparkline": "false"
    }) or []
    syms = []
    for c in data[:limit]:
        s = (c.get("symbol") or "").upper()
        if s and s.isalnum():
            syms.append(f"{s}USDT")
    # remove duplicados mantendo ordem
    seen = set(); out = []
    for s in syms:
        if s not in seen:
            seen.add(s); out.append(s)
    return out[:limit]

def fetch_ohlc(symbol: str, days: int = None) -> List[Dict[str, Any]]:
    days = int(days or OHLC_DAYS)
    cg_id = _get_cg_id(symbol)
    if not cg_id:
        return []
    data = _get_json(f"{API_BASE}/coins/{cg_id}/market_chart", {
        "vs_currency": "usd",
        "days": days,
        "interval": "daily"
    }) or {}
    prices = data.get("prices") or []
    if len(prices) < 2:
        return []
    # reconstrói OHLC diário por dia
    buckets: Dict[str, List[float]] = {}
    for ts_ms, price in prices:
        day = time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000))
        buckets.setdefault(day, []).append(float(price))
    ohlc: List[Dict[str, Any]] = []
    for day, arr in sorted(buckets.items()):
        if not arr: continue
        o, h, l, c = arr[0], max(arr), min(arr), arr[-1]
        ohlc.append({"time": f"{day} 23:59:59 UTC", "open": o, "high": h, "low": l, "close": c})
    return ohlc[-max(1, days):]
