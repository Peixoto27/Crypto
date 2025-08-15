# -*- coding: utf-8 -*-
"""
data_fetcher_coingecko.py
Coleta OHLC no CoinGecko com backoff e normalização + resolução robusta de IDs.
Saídas principais:
- fetch_ohlc(symbol, days) -> list[[ts, o, h, l, c], ...]
- fetch_top_symbols(n) -> list[str] (ex.: ["BTCUSDT", "ETHUSDT", ...])
"""

from __future__ import annotations

import os
import time
import json
import math
from typing import Any, Dict, List, Optional

import requests

# ==============================
# Config via ENV (mesmos nomes do .env)
# ==============================
API_DELAY_OHLC = float(os.getenv("API_DELAY_OHLC", "12.0"))
MAX_RETRIES    = int(os.getenv("MAX_RETRIES", "6"))
BACKOFF_BASE   = float(os.getenv("BACKOFF_BASE", "2.5"))

CG_IDS_FILE    = os.getenv("CG_IDS_FILE", "cg_ids.json")  # cache de mapeamento símbolo -> id CG

# ==============================
# Preferências/heurísticas de ID
# ==============================

# Preferências explícitas p/ pares mais comuns
PREFERRED_IDS: Dict[str, str] = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "BNBUSDT": "binancecoin",
    "XRPUSDT": "ripple",
    "SOLUSDT": "solana",
    "ADAUSDT": "cardano",
    "DOGEUSDT": "dogecoin",
    "TRXUSDT": "tron",
    "AVAXUSDT": "avalanche-2",
    "LINKUSDT": "chainlink",
    "MATICUSDT": "polygon",
    "TONUSDT": "the-open-network",
    "DOTUSDT": "polkadot",
    "LTCUSDT": "litecoin",
    "UNIUSDT": "uniswap",
    "BCHUSDT": "bitcoin-cash",
    "ETCUSDT": "ethereum-classic",
    "XLMUSDT": "stellar",
    "ATOMUSDT": "cosmos",
    "ICPUSDT": "internet-computer",
    "FILUSDT": "filecoin",
    "HBARUSDT": "hedera-hashgraph",
}

# Substrings de IDs que geralmente são ruins p/ OHLC (bridged/peg/wrapped etc.)
BAD_ID_SUBSTR = (
    "bridged", "wrapped", "wormhole", "peg", "bep2", "erc20",
    "binance-peg", "allbridge", "fuse-peg", "kcc-peg", "bsc-",
    "huobi-btc", "sollet", "staked", "rebase"
)

# ==============================
# Utilidades de cache de IDs
# ==============================

def _load_cg_ids() -> Dict[str, str]:
    try:
        with open(CG_IDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cg_ids(data: Dict[str, str]) -> None:
    try:
        with open(CG_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ==============================
# HTTP util c/ retries & backoff
# ==============================

def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    delay = API_DELAY_OHLC
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()

            # 429 rate limit → espera com backoff
            if resp.status_code == 429:
                wait = round(max(delay, 1.0), 1)
                print(f"⚠️ 429 OHLC: aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                delay *= BACKOFF_BASE
                continue

            # 404/4xx: não vale repetir muito
            if 400 <= resp.status_code < 500:
                raise RuntimeError(f"{resp.status_code} Client Error: {resp.text[:120]}")

            # 5xx → retry
            wait = round(max(delay, 1.0), 1)
            print(f"⚠️ {resp.status_code} OHLC: aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            delay *= BACKOFF_BASE

        except requests.RequestException as e:
            wait = round(max(delay, 1.0), 1)
            print(f"⚠️ Erro de rede: {e}. Aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            delay *= BACKOFF_BASE

    raise RuntimeError("Falha após máximo de tentativas na chamada CoinGecko.")

# ==============================
# Coin list & mercados (para resolver ID e Top N)
# ==============================

def _get_coin_list() -> List[Dict[str, Any]]:
    """Lista completa de moedas do CoinGecko (id/symbol/name)."""
    url = "https://api.coingecko.com/api/v3/coins/list?include_platform=false"
    data = _get_json(url)
    # Alguns endpoints alternativos trazem market_cap_rank; list não traz.
    # Mais adiante priorizamos por symbol/name; se necessário, complementamos.
    return data or []

def _get_markets_page(page: int, per_page: int = 250) -> List[Dict[str, Any]]:
    """Página de mercados por volume para montar Top N."""
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = dict(
        vs_currency="usd",
        order="volume_desc",
        per_page=per_page,
        page=page,
        price_change_percentage="24h",
        locale="en",
        sparkline="false",
    )
    data = _get_json(url, params=params)
    return data or []

# ==============================
# Resolução de ID robusta
# ==============================

def _pick_best_id(symbol: str, candidates: List[Dict[str, Any]]) -> Optional[str]:
    """Escolhe o melhor CoinGecko id para um par (ex.: ETHUSDT)."""
    # 1) override explícito
    if symbol in PREFERRED_IDS:
        return PREFERRED_IDS[symbol]

    if not candidates:
        return None

    # 2) tenta remover candidatos ruins
    def is_bad(c: Dict[str, Any]) -> bool:
        cid = (c.get("id") or "").lower()
        return any(s in cid for s in BAD_ID_SUBSTR)

    pool = [c for c in candidates if not is_bad(c)] or candidates

    base = symbol.replace("USDT","").replace("USD","").replace("FDUSD","")
    base_up = base.upper()
    base_lo = base.lower()

    # Se tivermos market_cap_rank nos candidates (quando vierem de markets),
    # utilizamos como critério secundário.
    def score(c: Dict[str, Any]):
        s = 0
        sym = (c.get("symbol") or "").upper()
        name = (c.get("name") or "").lower()
        if sym == base_up:
            s += 3
        if base_lo in name:
            s += 1
        mcap = c.get("market_cap_rank")
        mcap_penalty = -int(mcap) if isinstance(mcap, int) else -10**6
        return (s, mcap_penalty)

    pool.sort(key=score, reverse=True)
    return pool[0].get("id")

def resolve_cg_id(symbol: str,
                  coinlist: Optional[List[Dict[str, Any]]] = None,
                  markets_hint: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """
    Retorna o id do CoinGecko para um símbolo tipo BTCUSDT.
    Usa cache (cg_ids.json). Se não existir, tenta resolver e cacheia.
    """
    # 0) cache
    ids = _load_cg_ids()
    if symbol in ids and ids[symbol]:
        return ids[symbol]

    # 1) base symbol (BTC de BTCUSDT, etc.)
    base = symbol.replace("USDT","").replace("USD","").replace("FDUSD","").lower()

    # 2) constrói candidatos
    candidates: List[Dict[str, Any]] = []

    # dica de mercados (tem market_cap_rank)
    if markets_hint:
        for c in markets_hint:
            sym = (c.get("symbol") or "").lower()
            name = (c.get("name") or "").lower()
            if sym == base or base in name:
                candidates.append(c)

    # coinlist (id/symbol/name)
    if coinlist is None:
        coinlist = _get_coin_list()
    for c in coinlist:
        sym = (c.get("symbol") or "").lower()
        name = (c.get("name") or "").lower()
        if sym == base or base in name:
            candidates.append(c)

    chosen = _pick_best_id(symbol, candidates)
    if chosen:
        ids[symbol] = chosen
        _save_cg_ids(ids)
        print(f"🟦 CG_IDS atualizado: {symbol} -> {chosen}")
        return chosen

    print(f"🟨 Sem mapeamento CoinGecko para {symbol}. Adicione em CG_IDS.")
    return None

# ==============================
# Top N dinâmico
# ==============================

def fetch_top_symbols(top_n: int = 50) -> List[str]:
    """
    Retorna os top N pares em USDT com base no volume de mercado (CoinGecko).
    Converte id/symbol do CG em símbolos tipo XXUSDT quando fizer sentido.
    """
    if top_n <= 0:
        return []

    per_page = 250
    pages = int(math.ceil(top_n / per_page))
    markets: List[Dict[str, Any]] = []
    for p in range(1, pages + 1):
        part = _get_markets_page(page=p, per_page=per_page)
        markets.extend(part)
        if len(markets) >= top_n:
            break

    # Monta pares terminando em USDT para as principais
    out: List[str] = []
    seen = set()
    for m in markets:
        sym = (m.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        # Evita stablecoins (usdt/usdc/busd/tusd/fdusd etc.) como base
        if sym in ("USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDD", "USDE"):
            continue
        out.append(f"{sym}USDT")
        if len(out) >= top_n:
            break

    return out

# ==============================
# OHLC
# ==============================

def fetch_ohlc(symbol: str, days: int = 14) -> List[List[float]]:
    """
    Retorna OHLC como list[[ts, o, h, l, c], ...] usando o endpoint CoinGecko:
    /coins/{id}/ohlc?vs_currency=usd&days={days}
    """
    # Dica de markets para resolver id com melhor rank quando possível
    markets_hint = _get_markets_page(page=1, per_page=250)

    cg_id = resolve_cg_id(symbol, markets_hint=markets_hint)
    if not cg_id:
        raise RuntimeError(f"Não foi possível mapear {symbol} em CoinGecko.")

    url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc"
    params = {"vs_currency": "usd", "days": str(days)}
    data = _get_json(url, params=params)

    # Resposta padrão do CG: [[ts, o, h, l, c], ...]
    if not isinstance(data, list):
        return []

    # Normalização (garantir floats e ints corretos)
    out: List[List[float]] = []
    for row in data:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        ts, o, h, l, c = row[:5]
        try:
            out.append([int(ts), float(o), float(h), float(l), float(c)])
        except Exception:
            continue
    return out
