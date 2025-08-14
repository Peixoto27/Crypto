# -*- coding: utf-8 -*-
"""
data_fetcher_coingecko.py
- Coleta OHLC do CoinGecko com backoff e normalização (lista de dicts)
- Lista Top N símbolos (pares XXXUSDT) para ciclos dinâmicos

Variáveis de ambiente usadas:
  API_DELAY_OHLC   (float, seg)   -> tempo base entre requisições (padrão 12.0)
  MAX_RETRIES      (int)          -> tentativas em 429/erros (padrão 6)
  BACKOFF_BASE     (float, seg)   -> backoff exponencial: 12, 30, 75... (padrão 2.5)
  TOP_SYMBOLS      (int)          -> usados em fetch_top_symbols() (padrão 50)
"""
import os
import time
import math
import requests
from typing import List, Dict

API_DELAY_OHLC = float(os.getenv("API_DELAY_OHLC", "12.0"))
MAX_RETRIES    = int(os.getenv("MAX_RETRIES", "6"))
BACKOFF_BASE   = float(os.getenv("BACKOFF_BASE", "2.5"))
TOP_SYMBOLS    = int(os.getenv("TOP_SYMBOLS", "50"))

# Mapa SYMBOL -> id CoinGecko (adicione mais se quiser fixar)
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
    # você pode adicionar mais bases fixas aqui se desejar
}

def _is_stable_or_invalid(sym: str) -> bool:
    """Evita tentativas inválidas como USDTUSDT e stablecoins como base."""
    s = sym.upper().strip()
    if not s.endswith("USDT"):
        return True
    base = s[:-4]
    return base in ("USDT", "BUSD", "USDC", "TUSD", "FDUSD", "DAI", "")

def _backoff_sleep(try_idx: int):
    # 1->API_DELAY_OHLC, 2->~2.5x, 3->~6.25x ...
    if try_idx <= 1:
        wait = API_DELAY_OHLC
    else:
        wait = API_DELAY_OHLC * (BACKOFF_BASE ** (try_idx - 1))
    wait = max(3.0, min(wait, 240.0))
    print(f"⚠️ 429 OHLC: aguardando {wait:.1f}s (tentativa {try_idx}/{MAX_RETRIES})")
    time.sleep(wait)

def fetch_ohlc(symbol: str, days: int = 14, vs: str = "usd") -> List[Dict]:
    """
    Retorna lista de candles: [{time, open, high, low, close}, ...]
    Se não conseguir (ex.: stable, mapeamento ausente, 404), retorna [].
    """
    s = symbol.upper().strip()

    # corta casos inválidos (ex.: USDTUSDT)
    if _is_stable_or_invalid(s):
        print(f"⚠️ Ignorando OHLC inválido/estável: {s}")
        return []

    # id do CoinGecko
    coin_id = CG_IDS.get(s)
    if not coin_id:
        # fallback: tenta descobrir por mercado/top (melhor manter fixo no seu set)
        # aqui vamos apenas logar e retornar vazio (evita 404)
        print(f"⚠️ Sem mapeamento CoinGecko para {s}. Adicione em CG_IDS.")
        return []

    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc?vs_currency={vs}&days={days}"

    for t in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=25)
            if resp.status_code == 429:
                _backoff_sleep(t)
                continue
            if resp.status_code == 404:
                print(f"⚠️ 404 OHLC {s}: não encontrado ({url})")
                return []
            resp.raise_for_status()
            rows = resp.json()  # [[ts, o, h, l, c], ...]
            if not isinstance(rows, list) or not rows:
                return []
            out = []
            for r in rows:
                try:
                    ts, o, h, l, c = r
                    out.append({
                        "time": int(ts),
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                    })
                except Exception:
                    continue
            time.sleep(API_DELAY_OHLC)  # delay saudável a cada call
            return out
        except requests.RequestException as e:
            if t >= MAX_RETRIES:
                print(f"⚠️ Erro OHLC {s}: {e} (tentativa {t}/{MAX_RETRIES})")
                return []
            _backoff_sleep(t)
    return []

def fetch_top_symbols(limit: int = None) -> List[str]:
    """
    Busca Top N (market_cap_desc) no CoinGecko e retorna pares XXXUSDT (filtra stablecoins).
    """
    n = limit or TOP_SYMBOLS
    n = max(1, min(n, 250))
    url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={n}&page=1&sparkline=false"
    try:
        resp = requests.get(url, timeout=25)
        if resp.status_code == 429:
            _backoff_sleep(1)
            resp = requests.get(url, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        out = []
        for item in data:
            try:
                sym = str(item.get("symbol", "")).upper()
                # pula estáveis/usd-based
                if sym in ("USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI"):
                    continue
                pair = f"{sym}USDT"
                if not _is_stable_or_invalid(pair):
                    out.append(pair)
            except Exception:
                continue
        # remove duplicatas preservando ordem
        seen = set()
        uniq = []
        for p in out:
            if p not in seen:
                uniq.append(p); seen.add(p)
        return uniq[:n]
    except Exception as e:
        print(f"⚠️ Falha em fetch_top_symbols: {e}")
        return []
