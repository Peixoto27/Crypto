# -*- coding: utf-8 -*-
"""
data_fetcher_binance.py
- Busca OHLC primeiro na Binance.
- Se der HTTP 451 (ou qualquer falha), faz fallback automático para CoinGecko.
- Normaliza o retorno para: [[ts_ms, open, high, low, close], ...]
- Respeita MIN_BARS e DAYS_OHLC do .env (quando fornecidos).

Dependências externas: requests (nativa em muitas distros).
Arquivos auxiliares: cg_ids.json (mapeia 'SYMBOLUSDT' -> 'coingecko-id')
"""

import os
import json
import time
import math
from typing import List, Dict, Any, Optional, Tuple

try:
    import requests
except Exception:  # Railway normalmente tem requests
    requests = None


# ============== Utilidades básicas ==============

def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name, "")
    if val is None or val == "":
        return default
    return str(val).lower() in ("1", "true", "yes", "on")

def _get_env(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or v == "" else v

def _log(msg: str):
    print(msg, flush=True)


# ============== Leitura de cg_ids.json ==============

_CG_MAP: Dict[str, str] = {}

def _load_cg_map(path: str = "cg_ids.json") -> Dict[str, str]:
    global _CG_MAP
    if _CG_MAP:
        return _CG_MAP
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # aceita { "BTCUSDT": "bitcoin", ... } ou { "map": {..} }
            if isinstance(data, dict) and "map" in data:
                _CG_MAP = data["map"]
            elif isinstance(data, dict):
                _CG_MAP = data
    except Exception:
        _CG_MAP = {}
    return _CG_MAP

def _cg_id_for_symbol(symbol: str) -> Optional[str]:
    m = _load_cg_map()
    if symbol in m:
        return m[symbol]
    # tentativa “esperta”
    base = symbol.lower().replace("usdt", "").replace("busd", "")
    # alguns ids conhecidos podem coincidir
    guesses = [base, base + "-token", base + "-coin"]
    for g in guesses:
        if g in m.values():
            return g
    return None


# ============== Normalização de OHLC ==============

def _norm_klines_to_ohlc(rows: List[List[Any]]) -> List[List[float]]:
    """
    Espera klines estilo Binance:
      [
        [ open_time_ms, open, high, low, close, volume, close_time_ms, ... ],
        ...
      ]
    Retorna [[ts_ms, o, h, l, c], ...]
    """
    ohlc = []
    for r in rows:
        try:
            ts = int(r[0])
            o = float(r[1])
            h = float(r[2])
            l = float(r[3])
            c = float(r[4])
            ohlc.append([ts, o, h, l, c])
        except Exception:
            continue
    return ohlc

def _norm_cg_to_ohlc(prices: List[List[float]]) -> List[List[float]]:
    """
    CoinGecko market_chart retorna:
      "prices": [ [ts_ms, price], ... ]
    Não há OHLC direto por hora. Vamos sintetizar “pseudo OHLC” por janela (1h),
    agrupando por hora e usando min/max/first/last.
    """
    if not prices:
        return []
    # agrupar por hora (floor do ts/hora)
    buckets: Dict[int, List[float]] = {}
    for ts, price in prices:
        try:
            ts = int(ts)
            price = float(price)
        except Exception:
            continue
        hour = (ts // 3600000) * 3600000
        buckets.setdefault(hour, []).append(price)

    out = []
    for hour in sorted(buckets.keys()):
        vals = buckets[hour]
        if not vals:
            continue
        o = vals[0]
        h = max(vals)
        l = min(vals)
        c = vals[-1]
        out.append([hour, o, h, l, c])
    return out


# ============== Binance ==============

def _binance_klines(symbol: str, days: int, interval: str = "1h",
                    retries: int = 6) -> List[List[float]]:
    """
    Busca klines na Binance. Lida com HTTP 451 (bloqueio regional) e outros erros,
    retornando [] quando falhar.
    """
    if requests is None:
        return []
    base = "https://api.binance.com/api/v3/klines"
    # 30 dias de 1h ~ 720 candles; pedimos um pouco acima para margem
    limit = min(1000, int(days * 24) + 4)
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    backoff = 5.0
    for i in range(retries):
        try:
            resp = requests.get(base, params=params, timeout=20)
            if resp.status_code == 200:
                rows = resp.json()
                return _norm_klines_to_ohlc(rows)
            elif resp.status_code == 451:
                _log(f"⚠️ Binance api.binance.com: HTTP 451 — bloqueado (tentativa {i+1}/{retries})")
                time.sleep(backoff)
                backoff *= 2.25
            else:
                _log(f"⚠️ Binance {base}: HTTP {resp.status_code} — aguardando {backoff:.1f}s (tentativa {i+1}/{retries})")
                time.sleep(backoff)
                backoff *= 2.25
        except Exception as e:
            _log(f"⚠️ Binance falhou {symbol}: {e}")
            time.sleep(backoff)
            backoff *= 2.25
    return []


# ============== CoinGecko (fallback) ==============

def _coingecko_market_chart(coin_id: str, days: int) -> List[List[float]]:
    """
    Usa /market_chart?vs_currency=usd&days=X (retorna 'prices': [[ts, price], ...])
    """
    if requests is None:
        return []
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": str(days), "interval": "hourly"}
    try:
        r = requests.get(url, params=params, timeout=25)
        if r.status_code != 200:
            _log(f"⚠️ CoinGecko {coin_id}: HTTP {r.status_code}")
            return []
        obj = r.json()
        prices = obj.get("prices", [])
        return _norm_cg_to_ohlc(prices)
    except Exception as e:
        _log(f"⚠️ CoinGecko falhou {coin_id}: {e}")
        return []


# ============== API pública do módulo ==============

def fetch_ohlc(symbol: str,
               days: Optional[int] = None,
               min_bars: Optional[int] = None,
               interval: str = "1h") -> List[List[float]]:
    """
    Retorna [[ts_ms, open, high, low, close], ...]
    Tenta Binance -> fallback CoinGecko automaticamente.
    """
    days = days or int(_get_env("DAYS_OHLC", "30"))
    min_bars = min_bars or int(_get_env("MIN_BARS", "60"))

    # 1) Binance
    rows = _binance_klines(symbol, days, interval=interval)
    if len(rows) >= min_bars:
        return rows

    # 2) Fallback CoinGecko
    cg_id = _cg_id_for_symbol(symbol)
    if not cg_id:
        _log(f"🟨 Sem mapeamento CoinGecko para {symbol}. Adicione em cg_ids.json.")
        return rows  # talvez tenha vindo algo da Binance, mesmo que insuficiente

    rows_cg = _coingecko_market_chart(cg_id, days)
    if len(rows_cg) >= min_bars:
        return rows_cg

    return rows if len(rows) > len(rows_cg) else rows_cg


def fetch_many_ohlc(symbols: List[str],
                    days: Optional[int] = None,
                    min_bars: Optional[int] = None,
                    interval: str = "1h") -> Dict[str, List[List[float]]]:
    data: Dict[str, List[List[float]]] = {}
    for s in symbols:
        _log(f"📊 Coletando OHLC {s} (days={days or _get_env('DAYS_OHLC','30')})…")
        rows = fetch_ohlc(s, days=days, min_bars=min_bars, interval=interval)
        if len(rows) >= (min_bars or int(_get_env("MIN_BARS", "60"))):
            _log(f"   → OK | candles={len(rows)}")
        else:
            _log(f"⚠️ {s}: OHLC insuficiente ({len(rows)}/{min_bars or int(_get_env('MIN_BARS','60'))})")
        data[s] = rows
    return data
