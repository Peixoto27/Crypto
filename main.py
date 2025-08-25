# main.py
import os
import time
import logging

from config import (
    # diretórios/arquivos
    DATA_DIR, HISTORY_DIR, DATA_RAW_FILE, SIGNALS_FILE, HISTORY_FILE,
    MODEL_DIR, MODEL_FILE, POSITIONS_FILE, CURSOR_FILE,
    # flags
    NEWS_USE, TWITTER_USE, USE_AI, TRAINING_ENABLED, SAVE_HISTORY, SEND_STATUS_UPDATES,
    # números
    INTERVAL_MIN, AI_THRESHOLD, TRAIN_MIN_SAMPLES,
    # helpers
    ensure_dirs,
)

# ---- módulos internos (ajuste os nomes para os seus arquivos) ----
# Estes nomes seguem o que você me mandou/mostrou nos prints:
from utils import save_json                         # já corrige path/dir e salva JSON
from data_collector import collect_all              # coleta OHLC/preços/notícias/tweets (se aplicável)
from analyzer import analyze_signals                # calcula indicadores e score técnico
from ai_predictor import load_model, predict_with_ai
from history_manager import append_cycle_to_history # salva histórico por ciclo
from result_resolver_notify import resolve_and_notify  # notifica (texto + imagem) e atualiza status

# treino
from trainer import train_and_save                  # função de treino + persistência do modelo

# ---------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

def _model_available() -> bool:
    return os.path.exists(MODEL_FILE) and os.path.getsize(MODEL_FILE) > 0

def _log_header():
    logging.info("🚀 Runner iniciado. Intervalo = %.1f min.", float(INTERVAL_MIN))
    logging.info(
        "NEWS ativo?: %s | IA ativa?: %s | Historico ativado?: %s | Twitter ativo?: %s",
        NEWS_USE, USE_AI, SAVE_HISTORY, TWITTER_USE
    )
    logging.info(
        "Modelo disponível?: %s | Treino habilitado?: %s",
        _model_available(), TRAINING_ENABLED
    )

def _try_autotrain(history_items: list):
    """
    Se TREINING_ENABLED=True e houver amostras suficientes,
    treina e salva o modelo em MODEL_FILE.
    """
    if not TRAINING_ENABLED:
        return

    n_samples = len(history_items or [])
    if n_samples < TRAIN_MIN_SAMPLES:
        logging.info("📉 Amostras rotuladas ainda insuficientes para treino (%d < %d).",
                     n_samples, TRAIN_MIN_SAMPLES)
        return

    try:
        logging.info("🧪 Iniciando treino automático (amostras=%d)…", n_samples)
        model = train_and_save(history_items, MODEL_FILE)
        if model:
            logging.info("✅ Modelo treinado e salvo em %s", MODEL_FILE)
        else:
            logging.warning("⚠️ train_and_save não retornou modelo (verificar trainer.py).")
    except Exception as e:
        logging.exception("❌ Erro no treino automático: %s", e)

def runner_once():
    _log_header()

    # Garante estrutura de pastas
    try:
        ensure_dirs()
    except Exception as e:
        logging.warning("Não foi possível garantir diretórios: %s", e)

    # 1) Coleta
    raw = collect_all(
        use_news=NEWS_USE,
        use_twitter=TWITTER_USE
    )
    save_json(DATA_RAW_FILE, raw)
    logging.info("💾 Salvo %s", DATA_RAW_FILE)

    # 2) Análise técnica
    signals = analyze_signals(raw, min_threshold=AI_THRESHOLD)
    # signals é uma lista de dicts por símbolo

    # 3) IA (se habilitada e houver modelo)
    if USE_AI and _model_available():
        try:
            load_model()  # carrega só uma vez no processo
            signals = predict_with_ai(signals, MODEL_FILE, threshold=AI_THRESHOLD)
            logging.info("🤖 IA aplicada sobre os sinais.")
        except Exception as e:
            logging.exception("Erro aplicando IA: %s", e)
    else:
        if USE_AI:
            logging.info("⚠️ IA ativa mas o modelo ainda não está disponível (%s).", MODEL_FILE)

    # 4) Salvar sinais da rodada
    save_json(SIGNALS_FILE, signals)
    logging.info("💾 Sinais salvos em %s", SIGNALS_FILE)

    # 5) Gravar histórico (se habilitado)
    if SAVE_HISTORY:
        try:
            # append o snapshot desses sinais no histórico
            hist = append_cycle_to_history(HISTORY_FILE, signals)
            logging.info("🗂️ Histórico atualizado em %s (entries=%d)", HISTORY_FILE, len(hist))

            # 6) Auto-train (se habilitado + dados suficientes)
            _try_autotrain(hist)
        except Exception as e:
            logging.exception("Erro ao gravar histórico/treinar: %s", e)

    # 7) Resolver resultados e notificar (texto + imagem)
    try:
        resolve_and_notify()  # já usa SIGNALS_FILE/HISTORY_FILE internamente
        logging.info("📣 Notificações enviadas (quando aplicável).")
    except Exception as e:
        logging.exception("Erro ao notificar: %s", e)

def main_loop():
    while True:
        try:
            runner_once()
        except Exception as e:
            logging.exception("Falha no ciclo: %s", e)
        time.sleep(INTERVAL_MIN * 60)

if __name__ == "__main__":
    main_loop()
