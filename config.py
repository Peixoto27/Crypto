# config.py
import os

# =========================
# Funções auxiliares
# =========================
def _as_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")

def _as_int(name: str, default: str) -> int:
    return int(os.getenv(name, default).strip())

def _as_float(name: str, default: str) -> float:
    return float(os.getenv(name, default).strip())

# =========================
# Intervalo / Execução
# =========================
INTERVAL_MIN        = _as_int("INTERVAL_MIN", "20")      # minutos entre ciclos
LOOKBACK_HOURS      = _as_int("LABEL_LOOKBACK_HOURS", "24")

# =========================
# Flags de uso de fontes / features
# =========================
NEWS_USE            = _as_bool("NEWS_USE", "true")
TWITTER_USE         = _as_bool("TWITTER_USE", "true")
USE_AI              = _as_bool("USE_AI", "true")
TRAINING_ENABLED    = _as_bool("TRAINING_ENABLED", "true")
SAVE_HISTORY        = _as_bool("SAVE_HISTORY", "true")
SEND_STATUS_UPDATES = _as_bool("SEND_STATUS_UPDATES", "true")

# =========================
# Arquivos e diretórios
# (mantemos tudo organizado em /data e /model)
# =========================
DATA_DIR            = os.getenv("DATA_DIR", "data")
HISTORY_DIR         = os.getenv("HISTORY_DIR", f"{DATA_DIR}/history")
DATA_RAW_FILE       = os.getenv("DATA_RAW_FILE", f"{DATA_DIR}/data_raw.json")
SIGNALS_FILE        = os.getenv("SIGNALS_FILE", f"{DATA_DIR}/signals.json")
HISTORY_FILE        = os.getenv("HISTORY_FILE", f"{DATA_DIR}/history.json")
POSITIONS_FILE      = os.getenv("POSITIONS_FILE", f"{DATA_DIR}/positions.json")

MODEL_DIR           = os.getenv("MODEL_DIR", "model")
MODEL_FILE          = os.getenv("MODEL_FILE", f"{MODEL_DIR}/model.pkl")

CURSOR_FILE         = os.getenv("CURSOR_FILE", f"{DATA_DIR}/scan_state.json")

# =========================
# Treinamento
# =========================
TRAIN_MIN_SAMPLES   = _as_int("TRAIN_MIN_SAMPLES", "200")
RANDOM_STATE        = _as_int("RANDOM_STATE", "42")
AI_THRESHOLD        = _as_float("AI_THRESHOLD", "0.70")

# =========================
# Telegram (opcional)
# =========================
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# =========================
# Outras configs úteis para logs/indicadores
# =========================
TECH_MIN_THRESHOLD  = _as_float("TECH_MIN_THRESHOLD", "0.45")   # ex.: 45% mínimo para enviar
MAX_SYMBOLS_PER_CYCLE = _as_int("MAX_SYMBOLS_PER_CYCLE", "30")

# =========================
# Garantia de diretórios
# =========================
def ensure_dirs():
    for d in (DATA_DIR, HISTORY_DIR, MODEL_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            # não deixa o processo quebrar por falta de permissão; o main loga se necessário
            pass
