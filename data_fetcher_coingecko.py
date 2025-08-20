# data_fetcher_coingecko.py
import os, json, time, math
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

COINGECKO_TIMEOUT = int(os.getenv("COINGECKO_TIMEOUT", "30"))
COINGECKO_MAX_RETRY = int(os.getenv("COINGECKO_MAX_RETRY", "6"))

def _load_cg_ids(path="cg_ids.json"):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

_CG_IDS = _load_cg_ids()

def _pair_to_cgid(symbol: str) -> str | None:
    return _CG_IDS.get(symbol)

def _http_get_json(url: str, timeout=30):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.load(resp)

def fetch_ohlc(symbol: str, days: int = 30):
    """Retorna lista de [ts, open, high, low, close] pelo CoinGecko."""
    coin = _pair_to_cgid(symbol)
    if not coin:
        raise RuntimeError(f"Sem mapeamento CoinGecko para {symbol}. Adicione em cg_ids.json.")

    url = f"https://api.coingecko.com/api/v3/coins/{coin}/ohlc?vs_currency=usd&days={days}"

    backoff = 5.0
    for attempt in range(1, COINGECKO_MAX_RETRY + 1):
        try:
            data = _http_get_json(url, timeout=COINGECKO_TIMEOUT)
            # CoinGecko volta: [[timestamp, open, high, low, close], ...]
            if isinstance(data, list) and data and isinstance(data[0], list):
                return data
            # Alguns endpoints do CG retornam dict com message em rate limit
            if isinstance(data, dict) and "status" in data:
                raise RuntimeError(data["status"])
            return []
        except (HTTPError, URLError, TimeoutError, RuntimeError) as e:
            if attempt == COINGECKO_MAX_RETRY:
                raise
            time.sleep(backoff)
            backoff *= 2.0

def norm_rows(rows):
    out = []
    for r in rows or []:
        if len(r) >= 5:
            out.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])])
    return out
