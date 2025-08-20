# -*- coding: utf-8 -*-
"""
data_fetcher_binance.py
Coleta OHLC da Binance com rotação de endpoints (api-gcp, api, api1..api4),
detecção de HTTP 451 (geo-block) e backoff.

Retorno: lista [[ts_sec, open, high, low, close], ...] em ordem cronológica.
"""

import time
import math
import json
from datetime import datetime, timedelta
from urllib import request, parse, error

# Endpoints em ordem de preferência
BINANCE_HOSTS = [
    "https://api-gcp.binance.com",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
]

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

class GeoBlocked(Exception):
    pass

def _http_get(url: str, qs: dict, timeout=20):
    q = parse.urlencode(qs)
    full = f"{url}?{q}"
    req = request.Request(full, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except error.HTTPError as e:
        # 451: indisponível por razões legais (geo/região)
        if e.code == 451:
            raise GeoBlocked("HTTP 451 (geo-block)") from e
        raise
    except Exception:
        raise

def _interval_to_ms(interval: str) -> int:
    """Converte '1h','4h','1d','15m' para milissegundos."""
    unit = interval[-1]
    val = int(interval[:-1])
    if unit == "m":
        return val * 60_000
    if unit == "h":
        return val * 3_600_000
    if unit == "d":
        return val * 86_400_000
    raise ValueError(f"Intervalo inválido: {interval}")

def _bars_needed(days: int, interval: str) -> int:
    ms = days * 86_400_000
    step = _interval_to_ms(interval)
    return max(1, math.ceil(ms / step))

def _get_klines(host: str, symbol: str, interval: str, limit: int = 1000,
                end_time_ms: int | None = None):
    url = f"{host}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time_ms:
        params["endTime"] = end_time_ms
    raw = _http_get(url, params)
    data = json.loads(raw.decode("utf-8"))
    # Cada item: [openTime, open, high, low, close, volume, closeTime, ...]
    out = []
    for r in data:
        ts_sec = int(r[0] // 1000)
        o = float(r[1]); h = float(r[2]); l = float(r[3]); c = float(r[4])
        out.append([ts_sec, o, h, l, c])
    return out

def fetch_ohlc(symbol: str, days: int = 30, interval: str = "1h"):
    """
    Tenta baixar OHLC de vários hosts da Binance com fallback.
    Retorna [[ts,o,h,l,c], ...] (ordem cronológica). Pode retornar [] se todos falharem.
    """
    need = _bars_needed(days, interval)
    page_limit = 1000  # limite máximo da API
    collected: list[list[float]] = []

    # endTime começa "agora" e vai voltando para trás
    end_time_ms = int(time.time() * 1000)

    def _try_on_host(host: str) -> bool:
        nonlocal end_time_ms, collected
        try:
            # Enquanto faltar barra, pagina
            while len(collected) < need:
                got = _get_klines(host, symbol, interval, limit=min(page_limit, need - len(collected)),
                                  end_time_ms=end_time_ms)
                if not got:
                    break
                collected = got + collected  # prefixa para manter ordem cronológica depois
                # Novo endTime = openTime da primeira barra - 1ms
                open_ms = (got[0][0]) * 1000
                end_time_ms = open_ms - 1
            return True
        except GeoBlocked:
            print(f"⚠️ Binance {host}: HTTP 451 — bloqueado")
            return False
        except Exception as e:
            print(f"⚠️ Binance {host}: {e}")
            return False

    # Tenta hosts em ordem, com backoff pequeno entre eles
    for i, host in enumerate(BINANCE_HOSTS):
        ok = _try_on_host(host)
        if ok and len(collected) >= min(60, need):  # pelo menos 60 velas (compatível com teu MIN_BARS)
            break
        # pequeno backoff progressivo antes do próximo host
        time.sleep(1.5 * (i + 1))

    # Ordena e corta ao tamanho necessário
    collected.sort(key=lambda x: x[0])
    if len(collected) > need:
        collected = collected[-need:]

    return collected
