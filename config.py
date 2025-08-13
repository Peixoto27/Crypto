# -*- coding: utf-8 -*-
import os

def _bool(name: str, default: str = "false"):
    return os.getenv(name, default).strip().lower() in ("1","true","yes","on")

def _float(name: str, default: str):
    try: return float(os.getenv(name, default))
    except: return float(default)

def _int(name: str, default: str):
    try: return int(float(os.getenv(name, default)))
    except: return int(float(default))

# ---------- Núcleo ----------
MIN_CONFIDENCE      = _float("MIN_CONFIDENCE", "0.50")
DEBUG_SCORE         = _bool("DEBUG_SCORE", "true")

TOP_SYMBOLS         = _int("TOP_SYMBOLS", "20")
SYMBOLS             = [s.strip() for s in os.getenv("SYMBOLS",
                        "BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,SOLUSDT,MATICUSDT,DOTUSDT,LTCUSDT,LINKUSDT"
                      ).split(",") if s.strip()]

# ---------- CoinGecko ----------
API_DELAY_BULK      = _float("API_DELAY_BULK", "2.5")
API_DELAY_OHLC      = _float("API_DELAY_OHLC", "12.0")
MAX_RETRIES         = _int("MAX_RETRIES", "6")
BACKOFF_BASE        = _float("BACKOFF_BASE", "2.5")
OHLC_DAYS           = _int("OHLC_DAYS", "14")
MIN_BARS            = _int("MIN_BARS", "40")
BATCH_OHLC          = _int("BATCH_OHLC", "8")
BATCH_PAUSE_SEC     = _int("BATCH_PAUSE_SEC", "60")

# ---------- Fonte de dados (coingecko | ccxt) ----------
DATA_SOURCE         = os.getenv("DATA_SOURCE", "coingecko").lower()
CCXT_RATE_LIMIT_MS  = _int("CCXT_RATE_LIMIT_MS", "1200")

# ---------- Arquivos ----------
DATA_RAW_FILE       = os.getenv("DATA_RAW_FILE", "data_raw.json")
SIGNALS_FILE        = os.getenv("SIGNALS_FILE", "signals.json")
HISTORY_FILE        = os.getenv("HISTORY_FILE", "history.json")
MODEL_FILE          = os.getenv("MODEL_FILE", "model.pkl")
POSITIONS_FILE      = os.getenv("POSITIONS_FILE", "positions.json")

# ---------- Anti-duplicados ----------
COOLDOWN_HOURS         = _float("COOLDOWN_HOURS", "6")
CHANGE_THRESHOLD_PCT   = _float("CHANGE_THRESHOLD_PCT", "1.0")
SEND_STATUS_UPDATES    = _bool("SEND_STATUS_UPDATES", "true")

# ---------- IA ----------
TRAINING_ENABLED    = _bool("TRAINING_ENABLED", "true")
USE_AI              = _bool("USE_AI", "true")
AI_THRESHOLD        = _float("AI_THRESHOLD", "0.55")

# ---------- Indicadores extras ----------
USE_TECH_EXTRA      = _bool("USE_TECH_EXTRA", "true")      # Ichimoku/SAR/Stochastic
USE_VOLUME_INDICATORS = _bool("USE_VOLUME_INDICATORS", "false")  # VWAP/OBV (precisa de volume)
TECH_W_ICHI         = _float("TECH_W_ICHI", "0.25")
TECH_W_SAR          = _float("TECH_W_SAR", "0.20")
TECH_W_STOCH        = _float("TECH_W_STOCH", "0.20")
TECH_W_VWAP         = _float("TECH_W_VWAP", "0.20")
TECH_W_OBV          = _float("TECH_W_OBV", "0.15")

# ---------- Notícias / Sentimento ----------
USE_NEWS            = _bool("USE_NEWS", "false")  # fica OFF até você ligar
THENEWSAPI_KEY      = os.getenv("THENEWSAPI_KEY", "")
NEWS_WEIGHT         = _float("NEWS_WEIGHT", "0.20")
NEWS_MAX_BOOST      = _float("NEWS_MAX_BOOST", "0.15")
NEWS_MAX_PEN        = _float("NEWS_MAX_PEN", "0.25")
NEWS_VETO_NEG       = _float("NEWS_VETO_NEG", "-0.60")
NEWS_CACHE_FILE     = os.getenv("NEWS_CACHE_FILE", "news_cache.json")
NEWS_CACHE_TTL_SEC  = _int("NEWS_CACHE_TTL_SEC", "3600")

# ---------- Telegram ----------
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------- Logs ----------
LOG_LEVEL           = os.getenv("LOG_LEVEL", "INFO")
