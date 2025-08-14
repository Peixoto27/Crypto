# -*- coding: utf-8 -*-
"""
data_fetcher_coingecko.py
Coleta OHLC da CoinGecko com backoff e normalização.
Saída de fetch_ohlc: lista de dicts [{time, open, high, low, close}, ...]
"""

import os
import time
import math
import requests

# =========================
# Config via ENV (mesmos nomes já usados no projeto)
# =========================
API_DELAY_OHLC = float(os.getenv("API_DELAY_OHLC", "12.0"))
MAX_RETRIES     = int(os.getenv("MAX_RETRIES", "6"))
BACKOFF_BASE    = float(os.getenv("BACKOFF_BASE", "2.5"))

# Mapeamento simples SYMBOL -> id da CoinGecko
# (cobre os pares que você está usando; adicione outros se precisar)
CG_IDS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "BNBUSDT": "binancecoin",
    "XRPUSDT": "ripple",
    "ADAUSDT": "cardano",
    "DOGEUSDT": "dogecoin",
    "SOLUSDT": "solana",
    "MATICUSDT": "matic-network",
    "DOTUSDT": "polkadot",
    "LTCUSDT": "litecoin",
    "LINKUSDT": "chainlink",
}

SESSION = requests.Session()
BASE = "https://api.coingecko.com/api/v3"


def _cg_id(symbol: str) -> str:
    """Retorna o id da CoinGecko a partir do símbolo USDT."""
    s = (symbol or "").upper().strip()
    if s in CG_IDS:
        return CG_IDS[s]
    # fallback: tenta deduzir ticker (ex.: ABCUSDT -> abc)
    guess = s.replace("USDT", "").lower()
    # alguns ids diferem do ticker; se não soubermos, tenta o guess mesmo
    return guess


def _normalize_ohlc(raw_rows):
    """
    raw_rows: [[ts_ms, o, h, l, c], ...]
    -> [{'time': ts_iso, 'open':..., 'high':..., 'low':..., 'close':...}, ...]
    """
    out = []
    for row in raw_rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        ts_ms, o, h, l, c = row[:5]
        try:
            ts_ms = int(ts_ms)
        except Exception:
            continue
        # ISO UTC simples
        ts_iso = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts_ms / 1000.0))
        try:
            out.append({
                "time": ts_iso,
                "open": float(o),
                "high": float(h),
                "low":  float(l),
                "close": float(c),
            })
        except Exception:
            continue
    return out


def fetch_ohlc(symbol: str, days: int = 14):
    """
    Busca candles OHLC na CoinGecko para o 'symbol' (ex.: BTCUSDT).
    Retorna lista normalizada [{time, open, high, low, close}, ...].
    """
    coin_id = _cg_id(symbol)
    url = f"{BASE}/coins/{coin_id}/ohlc"
    params = {"vs_currency": "usd", "days": int(days)}

    delay = API_DELAY_OHLC
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = SESSION.get(url, params=params, timeout=20)
            # Tratamento de rate limit (429) com backoff exponencial
            if resp.status_code == 429:
                # retry-after se vier, senão usa backoff
                ra = None
                try:
                    ra = resp.json().get("parameters", {}).get("retry_after")
                except Exception:
                    pass
                wait_s = float(ra) if ra else delay
                print(f"⚠️ 429 OHLC {coin_id}: aguardando {wait_s:.1f}s (tentativa {attempt}/{MAX_RETRIES})")
                time.sleep(wait_s)
                delay *= BACKOFF_BASE
                continue

            resp.raise_for_status()
            data = resp.json()  # lista de listas
            norm = _normalize_ohlc(data)
            # pausa mínima entre chamadas para respeitar uso
            time.sleep(API_DELAY_OHLC)
            return norm

        except requests.exceptions.RequestException as e:
            # 400 comuns quando o id não bate ou sem dados suficientes
            print(f"⚠️ Erro OHLC {coin_id}: {e} (tentativa {attempt}/{MAX_RETRIES})")
            time.sleep(delay)
            delay *= BACKOFF_BASE
        except Exception as e:
            print(f"⚠️ Erro inesperado OHLC {coin_id}: {e} (tentativa {attempt}/{MAX_RETRIES})")
            time.sleep(delay)
            delay *= BACKOFF_BASE

    # chegou aqui: falhou
    return []


def fetch_prices_bulk(symbols):
    """
    Opcional: retorna um dicionário simples com 'symbol' -> None.
    Mantido por compatibilidade se o main chamar isso.
    """
    # Caso precise implementar, pode usar /simple/price, mas para o pipeline atual não é requerido.
    return {s: None for s in symbols or []}
