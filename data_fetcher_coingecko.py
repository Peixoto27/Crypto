# -*- coding: utf-8 -*-
"""
Coleta OHLC do CoinGecko com backoff e funções utilitárias.
- fetch_ohlc(symbol, days)  -> lista de dicts [{time, open, high, low, close}, ...]
- fetch_top_symbols(top_n)  -> lista como ["BTCUSDT", "ETHUSDT", ...]
"""
import os
import time
import math
import requests
from typing import List, Dict, Any

API_DELAY_OHLC = float(os.getenv("API_DELAY_OHLC", "12.0"))
MAX_RETRIES    = int(os.getenv("MAX_RETRIES", "6"))
BACKOFF_BASE   = float(os.getenv("BACKOFF_BASE", "2.5"))

# Mapa símbolo -> id CoinGecko (mínimo para fallback)
CG_IDS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "BNBUSDT": "binancecoin",
    "XRPUSDT": "ripple",
    "ADAUSDT": "cardano",
    "SOLUSDT": "solana",
    "DOGEUSDT": "dogecoin",
    "DOTUSDT": "polkadot",
    "MATICUSDT": "matic-network",
    "LTCUSDT": "litecoin",
    "LINKUSDT": "chainlink",
}

def _id_from_symbol(symbol: str) -> str:
    s = (symbol or "").upper()
    if s in CG_IDS:
        return CG_IDS[s]
    # heurística simples para tentar buscar id
    base = s.replace("USDT", "").lower()
    return base

def fetch_ohlc(symbol: str, days: int = 14) -> List[Dict[str, Any]]:
    """
    Busca OHLC diário (1d) no CoinGecko e normaliza para a estrutura:
    [{time, open, high, low, close}, ...]
    """
    coin_id = _id_from_symbol(symbol)
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    params = {"vs_currency": "usd", "days": int(days)}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:
                # rate limit -> backoff progressivo
                wait = API_DELAY_OHLC if attempt == 1 else API_DELAY_OHLC * (BACKOFF_BASE ** (attempt - 1))
                wait = min(300.0, wait)
                print(f"⚠️ 429 OHLC {coin_id}: aguardando {round(wait,1)}s (tentativa {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue

            r.raise_for_status()
            raw = r.json() or []
            # CoinGecko OHLC: [[timestamp_ms, open, high, low, close], ...]
            out = []
            for row in raw:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                out.append({
                    "time": int(row[0]) // 1000,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low":  float(row[3]),
                    "close":float(row[4]),
                })
            return out

        except Exception as e:
            if attempt >= MAX_RETRIES:
                raise
            wait = API_DELAY_OHLC if attempt == 1 else API_DELAY_OHLC * (BACKOFF_BASE ** (attempt - 1))
            wait = min(300.0, wait)
            print(f"⚠️ Erro OHLC {coin_id}: {e} -> aguardando {round(wait,1)}s (tentativa {attempt}/{MAX_RETRIES})")
            time.sleep(wait)

    return []


def fetch_top_symbols(top_n: int = 50) -> List[str]:
    """
    Retorna uma lista de tickers no formato *USDT* (ex.: BTCUSDT, ETHUSDT).
    Usa /coins/markets (market cap desc). Dedup e corta em top_n.
    """
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": min(max(int(top_n), 1), 250),
            "page": 1,
            "sparkline": "false"
        }
        r = requests.get(url, params=params, timeout=25)
        r.raise_for_status()
        rows = r.json() or []

        out: List[str] = []
        for it in rows:
            sym = (it.get("symbol") or "").upper()
            if sym and sym.isalpha():
                out.append(f"{sym}USDT")

        seen = set()
        dedup = []
        for s in out:
            if s not in seen:
                seen.add(s)
                dedup.append(s)
        return dedup[:top_n]

    except Exception as e:
        print(f"⚠️ fetch_top_symbols falhou: {e}")
        return ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","ADAUSDT","SOLUSDT","DOGEUSDT","DOTUSDT","MATICUSDT","LTCUSDT","LINKUSDT"]
