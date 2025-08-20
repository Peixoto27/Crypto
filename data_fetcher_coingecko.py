# -*- coding: utf-8 -*-
"""
data_fetcher_coingecko.py
Coleta OHLC do CoinGecko com mapeamento SYMBOL -> coin_id via cg_ids.json.

Formata a saída como lista de listas: [[ts_ms, open, high, low, close], ...]
"""

import os
import json
import time
from typing import List, Dict, Any, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

_COINGECKO_BASE = os.getenv("COINGECKO_API_URL", "https://api.coingecko.com/api/v3")
_CG_IDS_FILE = os.getenv("CG_IDS_FILE", "cg_ids.json")
_CG_UA = os.getenv("CG_UA", "Mozilla/5.0 (CryptoRunner/1.0)")

def _load_cg_ids() -> Dict[str, str]:
    """
    Lê cg_ids.json. Formatos aceitos:
      {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", ...}
    """
    if not os.path.exists(_CG_IDS_FILE):
        return {}
    try:
        with open(_CG_IDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k.upper(): str(v) for k, v in data.items()}
        return {}
    except Exception:
        return {}

_CG_IDS = _load_cg_ids()

def symbol_to_cg_id(symbol: str) -> Optional[str]:
    if not symbol:
        return None
    sym = symbol.upper().strip()
    if sym in _CG_IDS:
        return _CG_IDS[sym]
    # heurística simples: remover sufixo de par (ex.: BTCUSDT -> BTC)
    base = sym.replace("USDT", "").replace("USD", "").lower()
    # alguns aliases comuns
    aliases = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "bnb": "binancecoin",
        "xrp": "ripple",
        "ada": "cardano",
        "sol": "solana",
        "doge": "dogecoin",
        "trx": "tron",
        "avax": "avalanche-2",
        "matic": "matic-network",
        "dot": "polkadot",
        "ltc": "litecoin",
        "uni": "uniswap",
        "link": "chainlink",
        "shib": "shiba-inu",
        "bch": "bitcoin-cash",
        "etc": "ethereum-classic",
        "apt": "aptos",
        "imx": "immutable-x",
        "fil": "filecoin",
        "near": "near",
        "op": "optimism",
        "xlm": "stellar",
        "hbar": "hedera-hashgraph",
        "inj": "injective-protocol",
        "arb": "arbitrum",
        "ldo": "lido-dao",
        "atom": "cosmos",
        "stx": "blockstack",
    }
    return aliases.get(base)

def _get_json(url: str, timeout: int = 30) -> Any:
    req = Request(url, headers={"User-Agent": _CG_UA})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_ohlc(symbol: str, days: int = 30) -> List[List[float]]:
    """
    Busca OHLC  (4h aprox. para days>=7; 1h para <7) do CoinGecko.
    Retorna: [[ts_ms, open, high, low, close], ...]
    """
    cg_id = symbol_to_cg_id(symbol)
    if not cg_id:
        raise ValueError(f"Sem mapeamento CoinGecko para {symbol}. Adicione em cg_ids.json.")

    # CoinGecko: /coins/{id}/ohlc?vs_currency=usd&days={days}
    url = f"{_COINGECKO_BASE}/coins/{cg_id}/ohlc?vs_currency=usd&days={int(days)}"

    # retries leves
    backoff = [0, 2, 5, 10]
    last_err = None
    for wait in backoff:
        if wait:
            time.sleep(wait)
        try:
            data = _get_json(url, timeout=40)
            # Formato oficial já vem como [ts, o, h, l, c]
            out = []
            if isinstance(data, list):
                for row in data:
                    if not isinstance(row, (list, tuple)) or len(row) < 5:
                        continue
                    ts, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
                    out.append([float(ts), float(o), float(h), float(l), float(c)])
            return out
        except (HTTPError, URLError, TimeoutError) as e:
            last_err = e
        except Exception as e:
            last_err = e
    raise RuntimeError(f"CoinGecko falhou {symbol}: {last_err}")
