# -*- coding: utf-8 -*-
"""
config.py — centraliza leitura de variáveis de ambiente
Compatível com os módulos atuais do seu projeto.
"""

import os
from dotenv import load_dotenv

load_dotenv()

def _as_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1","true","yes","y","on")

# --------- Logs/Runner ----------
LOG_LEVEL          = os.getenv("LOG_LEVEL", "INFO")
RUN_INTERVAL_MIN   = os.getenv("RUN_INTERVAL_MIN", "20")

# --------- Universo / Seleção ----------
SYMBOLS            = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]
TOP_SYMBOLS        = int(os.getenv("TOP_SYMBOLS", "100"))
SELECT_PER_CYCLE   = int(os.getenv("SELECT_PER_CYCLE", "8"))

# --------- OHLC / Janela ----------
DAYS_OHLC          = int(os.getenv("DAYS_OHLC", "30"))
MIN_BARS           = int(os.getenv("MIN_BARS", "180"))

# --------- Scores / Limiar ----------
SCORE_THRESHOLD    = float(os.getenv("SCORE_THRESHOLD", "0.45"))
MIN_CONFIDENCE     = float(os.getenv("MIN_CONFIDENCE", "0.45"))
WEIGHT_TECH        = float(os.getenv("WEIGHT_TECH", "1.5"))
WEIGHT_AI          = float(os.getenv("WEIGHT_AI", "1.0"))

# --------- Anti-duplicados ----------
COOLDOWN_HOURS       = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))
SEND_STATUS_UPDATES  = _as_bool("SEND_STATUS_UPDATES", "true")

# --------- Arquivos ----------
DATA_RAW_FILE       = os.getenv("DATA_RAW_FILE", "data_raw.json")
SIGNALS_FILE        = os.getenv("SIGNALS_FILE", "data/signals.json")
HISTORY_FILE        = os.getenv("HISTORY_FILE", "history.json")
HISTORY_DIR         = os.getenv("HISTORY_DIR", "data/history")
CURSOR_FILE         = os.getenv("CURSOR_FILE", "scan_state.json")
MODEL_FILE          = os.getenv("MODEL_FILE", "model/model.pkl")
POSITIONS_FILE      = os.getenv("POSITIONS_FILE", "positions.json")

# --------- Flags de features ----------
USE_AI             = _as_bool("USE_AI", "true")
TRAINING_ENABLED   = _as_bool("TRAINING_ENABLED", "true")
USE_RSS_NEW        = _as_bool("USE_RSS_NEW", "false")
USE_THENEWSAPI     = _as_bool("USE_THENEWSAPI", "false")
USE_TWITTER        = _as_bool("USE_TWITTER", "false")
REMOVE_STABLES     = _as_bool("REMOVE_STABLES", "true")

# --------- Telegram ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
