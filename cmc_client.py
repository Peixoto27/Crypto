# cmc_client.py
# Cliente simples para CoinMarketCap: top listings e quote atual (USD).
# Requer: CMC_API_KEY no ambiente.

import os, time, json, urllib.request, urllib.parse, ssl

CMC_API_KEY = os.getenv("CMC_API_KEY", "").strip()
CMC_BASE = "https://pro-api.coinmarketcap.com"

_cache = {"top_list": (0, []), "quote": {}}

def _get(url: str, params: dict) -> dict:
    if not CMC_API_KEY:
        raise RuntimeError("CMC_API_KEY não configurado")
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{CMC_BASE}{url}?{q}")
    req.add_header("X-CMC_PRO_API_KEY", CMC_API_KEY)
    req.add_header("Accept", "application/json")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def get_top_symbols(limit: int = 100) -> list:
    """
    Retorna símbolos como 'BTCUSDT', 'ETHUSDT'… (sufixo USDT),
    apenas para montar universo.
    """
    now = time.time()
    ts, cached = _cache["top_list"]
    if cached and now - ts < 900:  # 15 min cache
        return cached[:limit]
    data = _get("/v1/cryptocurrency/listings/latest", {
        "limit": min(limit, 5000),
        "convert": "USD",
        "sort": "market_cap",
        "sort_dir": "desc"
    })
    out = []
    for it in data.get("data", []):
        sym = str(it.get("symbol", "")).upper()
        if sym:
            out.append(f"{sym}USDT")
    _cache["top_list"] = (now, out)
    return out[:limit]

def get_quote_usd(symbol: str) -> float:
    """
    Preço USD instantâneo. Aceita 'BTCUSDT' ou 'BTC'.
    """
    if symbol.endswith("USDT"):
        symbol = symbol[:-4]
    symbol = symbol.upper()

    now = time.time()
    cached = _cache["quote"].get(symbol)
    if cached and now - cached[0] < 30:
        return cached[1]

    data = _get("/v1/cryptocurrency/quotes/latest",
                {"symbol": symbol, "convert": "USD"})
    quote = data.get("data", {}).get(symbol, {}).get("quote", {}).get("USD", {}).get("price")
    if quote is None:
        raise RuntimeError(f"Sem preço para {symbol} na CMC")
    price = float(quote)
    _cache["quote"][symbol] = (now, price)
    return price
