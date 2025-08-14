# data_fetcher_coingecko.py
import requests
import time
import logging

API_BASE = "https://api.coingecko.com/api/v3"
API_DELAY_BULK = 2.5   # delay entre requisições de lista
API_DELAY_OHLC = 30.0  # delay entre requisições de OHLC
MAX_RETRIES = 6
BACKOFF_BASE = 3

logger = logging.getLogger(__name__)

def get_all_coins():
    """Pega lista completa de moedas e cria dicionário {symbol: id}."""
    try:
        r = requests.get(f"{API_BASE}/coins/list", timeout=15)
        r.raise_for_status()
        data = r.json()
        return {c["symbol"].upper(): c["id"] for c in data}
    except Exception as e:
        logger.error(f"Erro ao buscar lista de moedas: {e}")
        return {}

def fetch_top_symbols(limit=50):
    """Pega top moedas por market cap."""
    try:
        r = requests.get(
            f"{API_BASE}/coins/markets",
            params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": limit, "page": 1},
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        return [coin["symbol"].upper() + "USDT" for coin in data]
    except Exception as e:
        logger.error(f"Erro ao buscar top moedas: {e}")
        return []

def fetch_ohlc(symbol, days=14, symbol_map=None):
    """Busca OHLC da moeda no CoinGecko."""
    try:
        coin_symbol = symbol.replace("USDT", "").upper()
        coin_id = symbol_map.get(coin_symbol)

        if not coin_id:
            logger.warning(f"⚠️ Sem ID no CoinGecko para {symbol}, pulando...")
            return None

        url = f"{API_BASE}/coins/{coin_id}/ohlc"
        params = {"vs_currency": "usd", "days": days}

        for attempt in range(MAX_RETRIES):
            try:
                r = requests.get(url, params=params, timeout=15)
                if r.status_code == 404:
                    logger.warning(f"⚠️ OHLC não disponível para {symbol}, pulando...")
                    return None
                if r.status_code == 429:
                    wait_time = API_DELAY_OHLC if attempt == 0 else BACKOFF_BASE ** attempt
                    logger.warning(f"429 OHLC {symbol}: aguardando {wait_time:.1f}s (tentativa {attempt+1}/{MAX_RETRIES})")
                    time.sleep(wait_time)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                logger.error(f"Erro OHLC {symbol}: {e}")
                time.sleep(BACKOFF_BASE ** attempt)
        return None
    except Exception as e:
        logger.error(f"Erro inesperado em fetch_ohlc({symbol}): {e}")
        return None
