# -*- coding: utf-8 -*-
import os
import json
import time
from datetime import datetime, timezone

# ----------------- Config / Módulos do projeto -----------------
from config import (
    DATA_RAW_FILE,
    SIGNALS_FILE,
    HISTORY_DIR,
    CURSOR_FILE,
    USE_AI,
    TRAINING_ENABLED,
    AI_THRESHOLD,
    SEND_STATUS_UPDATES,
)

# coleta / análise / notificação — ajuste os imports conforme o seu projeto
from ai_predictor import load_model, predict_batch  # <- usa o loader oficial
from result_resolver_notify import resolve_and_notify  # <- envia msg final/diária
from notifier_telegram import send_signal_card, send_status  # <- envio por TG
from history_manager import append_history_snapshot  # <- salva histórico
from utils import ensure_dir, save_json  # <- utilitários

# Se você tiver um cliente de mercado (CoinGecko/Ccxt), importe aqui:
# from coingecko_client import fetch_bulk_ohlc  # EXEMPLO: ajuste para seu client
# ou do seu módulo de indicadores:
# from indicators import score_symbols  # EXEMPLO

# ----------------- Config do loop -----------------
INTERVAL_MIN = int(os.getenv("INTERVAL_MIN", "20"))
MIN_SCORE_TO_NOTIFY = float(os.getenv("MIN_SCORE_TO_NOTIFY", "45.0"))  # %
MAX_SYMBOLS_PER_CYCLE = int(os.getenv("MAX_SYMBOLS_PER_CYCLE", "30"))

# ----------------- Checagem do modelo -----------------
def _check_model() -> bool:
    print(f"cwd: {os.getcwd()}")
    print(f"MODEL_FILE (env/config): {os.getenv('MODEL_FILE')}")
    try:
        mdl = load_model()  # cacheado dentro do ai_predictor
        if mdl is None:
            print("Modelo disponível?: False | detalhe: load_model() retornou None")
            return False
        print("Modelo disponível?: True | detalhe: modelo carregado via ai_predictor")
        return True
    except Exception as e:
        print(f"Modelo disponível?: False | detalhe: {type(e).__name__}: {e}")
        return False

MODEL_AVAILABLE = _check_model()

# ----------------- Auxiliares -----------------
def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def _log_header():
    print("Starting Container")
    print(f"▶ Runner iniciado. Intervalo = {INTERVAL_MIN:.1f} min.")
    print(
        f"NEWS ativo?: {str(os.getenv('NEWS_USE', 'true')).lower() == 'true'} | "
        f"IA ativa?: {USE_AI} | Historico ativado?: {os.getenv('SAVE_HISTORY','true')} | "
        f"Twitter ativo?: {str(os.getenv('TWITTER_USE','false')).lower() == 'true'}"
    )
    print(f"Modelo disponível?: {MODEL_AVAILABLE} | Treino habilitado?: {TRAINING_ENABLED}")

# ----------------- Pipeline principal -----------------
def collect_data():
    """
    Retorna estrutura:
    {
       "BTCUSDT": {"ohlc": [...], "tech": {...}},
       "ETHUSDT": {...},
       ...
    }
    """
    # >>>> AJUSTE ESTA FUNÇÃO PARA O SEU CLIENTE <<<<
    #
    # Abaixo está um esqueleto que só demonstra a estrutura.
    # No seu projeto real você já tem a coleta; então você pode
    # simplesmente importar e chamar sua função oficial aqui.
    #
    symbols = os.getenv("SYMBOLS", "").split(",")
    symbols = [s.strip() for s in symbols if s.strip()]
    data = {}

    # Se não vier por env, use a sua lista padrão (igual aos logs ~ 30/92)
    if not symbols:
        symbols = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT",
            "DOGEUSDT", "TRXUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT",
            "DOTUSDT", "LTCUSDT", "UNIUSDT", "BCHUSDT", "ETCUSDT",
            "APTUSDT", "IMXUSDT", "FILUSDT", "NEARUSDT", "OPUSDT",
            "XLMUSDT", "HBARUSDT", "INJUSDT", "ARBUSDT", "LDOUSDT",
            "ATOMUSDT", "STXUSDT"
        ]

    symbols = symbols[:MAX_SYMBOLS_PER_CYCLE]

    print(f"Moedas deste ciclo ({len(symbols)}/{len(symbols)}): {', '.join(symbols)}")

    # --- EXEMPLO de logs coerentes com seus prints ---
    for sym in symbols:
        print(f"Coletando OHLC {sym} (tf=30d)…")
        # Aqui você chama sua coleta real e normalização
        # candles = fetch_bulk_ohlc(sym, tf="30d", lookback=180)  # EXEMPLO
        # if not candles_ok: log de insuficiente etc.
        # data[sym] = {"ohlc": candles, "tech": calc_tech(candles)}

        # Somente para manter o fluxo de logs:
        print("  → OK | candles=180")
        data[sym] = {"ohlc": [0]*180, "tech": {}}

    # Salva matéria-prima (igual seus logs)
    try:
        save_json(DATA_RAW_FILE, {"as_of": _ts(), "symbols": list(data.keys())})
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(data)} ativos)")
    except Exception as e:
        print(f"⚠️ Erro ao salvar {DATA_RAW_FILE}: {e}")

    return data

def score_and_build_signals(data: dict):
    """
    Monta a lista de sinais:
    [
      {"symbol":"ETHUSDT","score_tech":64.5,"score_ai":0.72,"mix":64.5,"targets":[...],"risk":"M"},
      ...
    ]
    """
    signals = []

    # --------- 1) score técnico (ajuste para sua função real) ----------
    def score_tech_stub(sym: str, payload: dict) -> float:
        # coloque aqui sua métrica real; deixo um valor “fake” só pra fluxo
        import random
        return round(35 + random.random() * 30, 1)  # 35% ~ 65%

    # --------- 2) IA (opcional) ----------
    use_ai_now = bool(USE_AI and MODEL_AVAILABLE)

    # Para predição em lote (melhor), junte as features aqui e chame predict_batch
    # features_map = build_features_for_batch(data)  # se você tiver
    # probs = predict_batch(features_map)  # dict: symbol -> prob (0~1)

    for sym, payload in data.items():
        s_tech = score_tech_stub(sym, payload)

        s_ai_prob = None
        if use_ai_now:
            try:
                # Se você tiver batch, use-o. Aqui chamo unitário por simplicidade:
                # features = build_features_for_symbol(payload)  # sua função
                # s_ai_prob = predict_single(features)
                # Como não sei sua API exata, deixo None e o Mix usa só técnico.
                pass
            except Exception as e:
                print(f"⚠️ IA falhou {sym}: {e}")

        # Mix: se não houver IA, fica igual ao técnico (como nos seus logs: IA: - | Mix: técnico)
        if s_ai_prob is None:
            s_mix = s_tech
        else:
            # exemplo: mistura simples ponderada técnico x IA
            w_t = float(os.getenv("WEIGHT_TECH", "1.5"))
            w_a = float(os.getenv("WEIGHT_AI", "1.0"))
            s_mix = round((w_t * s_tech + w_a * (s_ai_prob * 100.0)) / (w_t + w_a), 1)

        # Monte alvos/risco (você já tem isso no notificador; mantenho aqui simples)
        targets = []
        risk = "M"

        signals.append({
            "symbol": sym,
            "score_tech": s_tech,
            "score_ai": s_ai_prob,
            "mix": s_mix,
            "targets": targets,
            "risk": risk,
        })

    # ordena por mix desc e filtra mínimo
    signals.sort(key=lambda s: s["mix"], reverse=True)
    signals = [s for s in signals if s["mix"] >= MIN_SCORE_TO_NOTIFY]

    return signals

def notify(signals: list):
    sent = 0
    for s in signals:
        sym = s["symbol"]
        s_tech = s["score_tech"]
        s_ai = s["score_ai"]
        s_mix = s["mix"]

        # ✅ ENVIO — AJUSTE AQUI para sua função de envio:
        try:
            send_signal_card(
                symbol=sym,
                score_tech=s_tech,
                score_ai=s_ai,
                score_mix=s_mix,
                min_required=MIN_SCORE_TO_NOTIFY,
                targets=s.get("targets", []),
                risk=s.get("risk", "M"),
            )
            print("✅ Enviado com imagem.")
            print("✅ Notificado.")
            sent += 1
        except Exception as e:
            print(f"⚠️ Falha ao notificar {sym}: {e}")

    # relatório/fechamento (se você tiver)
    try:
        resolve_and_notify()
    except Exception as e:
        print(f"⚠️ resolve_and_notify falhou: {e}")

    return sent

def save_signals_file(signals: list):
    """
    Salva SEMPRE no caminho definido por SIGNALS_FILE (ex.: data/signals.json),
    corrigindo o bug de passar dict no lugar do path (o erro que você tinha).
    """
    try:
        ensure_dir(os.path.dirname(SIGNALS_FILE) or ".")
        payload = {
            "as_of": _ts(),
            "count": len(signals),
            "signals": signals,
        }
        with open(SIGNALS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"🟨 {len(signals)} sinais salvos em {SIGNALS_FILE}")
    except Exception as e:
        print(f"⚠️ Erro ao salvar {SIGNALS_FILE}: {e}")

def run_cycle():
    data = collect_data()
    signals = score_and_build_signals(data)
    save_signals_file(signals)

    if SEND_STATUS_UPDATES:
        try:
            send_status(f"Fim: { _ts() } | {len(signals)} sinais >= {MIN_SCORE_TO_NOTIFY:.0f}%")
        except Exception as e:
            print(f"⚠️ Falha ao enviar status: {e}")

    # histórico (se desejar salvar snapshot)
    try:
        append_history_snapshot(signals)
    except Exception as e:
        print(f"⚠️ Falha no histórico: {e}")

def main():
    _log_header()
    while True:
        start = time.time()
        try:
            run_cycle()
        except Exception as e:
            print(f"❌ Erro no ciclo: {type(e).__name__}: {e}")

        elapsed = time.time() - start
        sleep_s = max(0, int(INTERVAL_MIN * 60 - elapsed))
        if sleep_s > 0:
            time.sleep(sleep_s)

if __name__ == "__main__":
    main()
