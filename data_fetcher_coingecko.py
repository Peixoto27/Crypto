# -*- coding: utf-8 -*-
"""
data_fetcher_coingecko.py
Coleta OHLC e universo de moedas usando a API pública do CoinGecko.

- Respeita limites com backoff e cooldown configuráveis por ENV
- Faz mapeamento símbolo->id com tabela CG_IDS (ajustada) + fallback de busca
- Limpa barras inválidas (close<=0 ou NaN) para evitar séries zeradas

ENV úteis (com defaults):
  COINGECKO_API_URL=https://api.coingecko.com/api/v3
  REQUESTS_TIMEOUT_S=30
  REQUESTS_COOLDOWN_S=2.0
  MAX_RETRIES=6
  FIRST_BACKOFF_S=30
  BACKOFF_FACTOR=2.5
  VS_CURRENCY=usd
"""

from __future__ import annotations

import os
import time
import math
import json
import typing as T
from typing import List, Dict, Any, Optional

import requests

# ------------------------------
# Config via ENV
# ------------------------------
API_BASE          = os.getenv("COINGECKO_API_URL", "https://api.coingecko.com/api/v3")
VS_CURRENCY       = os.getenv("VS_CURRENCY", "usd")
REQUESTS_TIMEOUT  = float(os.getenv("REQUESTS_TIMEOUT_S", "30"))
REQUESTS_COOLDOWN = float(os.getenv("REQUESTS_COOLDOWN_S", "2.0"))
MAX_RETRIES       = int(os.getenv("MAX_RETRIES", "6"))
FIRST_BACKOFF     = float(os.getenv("FIRST_BACKOFF_S", "30"))
BACKOFF_FACTOR    = float(os.getenv("BACKOFF_FACTOR", "2.5"))

# ------------------------------
# Mapeamento símbolo -> id CoinGecko
# (ajustado e ampliado)
# ------------------------------
CG_IDS: Dict[str, str] = {
    # Majors
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "BNBUSDT": "binancecoin",
    "XRPUSDT": "ripple",
    "SOLUSDT": "solana",
    "ADAUSDT": "cardano",
    "DOGEUSDT": "dogecoin",
    "TRXUSDT":  "tron",

    # Extras frequentes
    "OPUSDT":   "optimism",
    "NEARUSDT": "near",
    "APTUSDT":  "aptos",
    "FILUSDT":  "filecoin",
    "ETCUSDT":  "ethereum-classic",
    "BCHUSDT":  "bitcoin-cash",
    "XLMUSDT":  "stellar",
    "ARBUSDT":  "arbitrum",
    "ATOMUSDT": "cosmos",
    "STXUSDT":  "stacks",               # (antigo "blockstack")
    "RNDRUSDT": "render",               # às vezes "render-token"
    "ICPUSDT":  "internet-computer",
    "PEPEUSDT": "pepe",
    "CROUSDT":  "cronos",               # (antigo "crypto-com-chain")
    "MKRUSDT":  "maker",
    "TAOUSDT":  "bittensor",

    # Adicione outros aqui conforme necessário…
}

_session = requests.Session()


# ------------------------------
# util: GET com backoff
# ------------------------------
def _get(url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    last_err: Optional[Exception] = None
    backoff = FIRST_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=REQUESTS_TIMEOUT)
            if resp.status_code == 429:
                # rate limited
                wait = backoff
                print(f"⚠️ 429: aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                backoff *= BACKOFF_FACTOR
                continue
            if 500 <= resp.status_code < 600:
                wait = backoff
                print(f"⚠️ {resp.status_code} servidor: aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                backoff *= BACKOFF_FACTOR
                continue
            resp.raise_for_status()
            # cooldown suaviza cadência
            if REQUESTS_COOLDOWN > 0:
                time.sleep(REQUESTS_COOLDOWN)
            return resp
        except requests.exceptions.ReadTimeout as e:
            last_err = e
            wait = backoff
            print(f"⚠️ Erro de rede (timeout). Aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            backoff *= BACKOFF_FACTOR
        except Exception as e:
            last_err = e
            wait = backoff
            print(f"⚠️ Erro de rede: {e}. Aguardando {wait:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            backoff *= BACKOFF_FACTOR
    if last_err:
        raise last_err
    raise RuntimeError("Falha de rede desconhecida")


# ------------------------------
# Busca ID por símbolo (fallback)
# ------------------------------
def _search_id_for_symbol(symbol: str) -> Optional[str]:
    """
    Tenta encontrar o id no CoinGecko via /search quando CG_IDS não tem o símbolo.
    Heurística simples: usa a parte "base" sem USDT (e.g. RNDRUSDT -> RNDR).
    """
    base = symbol.upper().replace("USDT", "").strip()
    try:
        resp = _get(f"{API_BASE}/search", params={"query": base})
        data = resp.json()
        coins = data.get("coins", [])
        # primeiro match exato em symbol ou name
        for c in coins:
            sym = str(c.get("symbol", "")).upper()
            name = str(c.get("name", "")).upper()
            cid  = c.get("id")
            if sym == base or name == base:
                return cid
        # se não houver match exato, pega o primeiro id
        if coins:
            return coins[0].get("id")
    except Exception:
        pass
    return None


# ------------------------------
# Normalização de OHLC
# ------------------------------
def _norm_ohlc_rows(rows: List[List[float]]) -> List[List[float]]:
    """
    Espera formato do CG: [[ts, o, h, l, c], ...]
    Limpa barras inválidas (close<=0 ou NaN).
    """
    out: List[List[float]] = []
    for r in rows or []:
        if not isinstance(r, (list, tuple)) or len(r) < 5:
            continue
        t, o, h, l, c = r[0], r[1], r[2], r[3], r[4]
        try:
            c = float(c)
            o = float(o); h = float(h); l = float(l); t = float(t)
        except Exception:
            continue
        if (not math.isfinite(c)) or c <= 0.0:
            # barra inválida
            continue
        out.append([t, o, h, l, c])
    return out


# ------------------------------
# fetch_ohlc
# ------------------------------
def fetch_ohlc(symbol: str, days: int) -> List[List[float]]:
    """
    Retorna OHLC em formato [[ts, o, h, l, c], ...] após limpeza.
    """
    sym = symbol.upper()
    cg_id = CG_IDS.get(sym)
    if not cg_id:
        # tenta buscar
        cg_id = _search_id_for_symbol(sym)
        if cg_id:
            CG_IDS[sym] = cg_id
            print(f"🟦 CG_IDS atualizado: {sym} -> {cg_id}")
        else:
            msg = f"Não foi possível mapear {sym} em CoinGecko."
            print(f"🟨 Sem mapeamento CoinGecko para {sym}. Adicione em CG_IDS.")
            raise RuntimeError(msg)

    url = f"{API_BASE}/coins/{cg_id}/ohlc"
    params = {"vs_currency": VS_CURRENCY, "days": int(days)}
    resp = _get(url, params=params)
    data = resp.json()

    rows = _norm_ohlc_rows(data)
    if not rows:
        print(f"🟧 Série vazia/limpa para {sym} (id={cg_id}).")
        return []

    print(f"   → OK | candles={len(rows)}")
    return rows


# ------------------------------
# fetch_top_symbols
# ------------------------------
def fetch_top_symbols(n: int = 100) -> List[str]:
    """
    Retorna os 'n' principais símbolos em formato XXXUSDT.
    Usa /coins/markets ordenando por market_cap_desc.
    """
    out: List[str] = []
    per_page = 250
    pages = math.ceil(max(1, n) / per_page)
    got = 0
    for p in range(1, pages + 1):
        limit = min(per_page, n - got)
        if limit <= 0:
            break
        try:
            resp = _get(
                f"{API_BASE}/coins/markets",
                params={
                    "vs_currency": VS_CURRENCY,
                    "order": "market_cap_desc",
                    "per_page": limit,
                    "page": p,
                    "sparkline": "false",
                },
            )
            data = resp.json() or []
            for item in data:
                sym = str(item.get("symbol", "")).upper()
                if not sym:
                    continue
                out.append(f"{sym}USDT")
                got += 1
                if got >= n:
                    break
        except Exception as e:
            print(f"⚠️ Erro ao buscar top symbols (p{p}): {e}")
            break
    # remove duplicatas mantendo ordem
    seen = set()
    uniq = []
    for s in out:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq
