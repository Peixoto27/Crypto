# -*- coding: utf-8 -*-
"""
main.py — pipeline completo com fast-lane + rotação
- Seleciona símbolos (fixos + rotação)
- Coleta OHLC (CoinGecko)
- Gera sinal técnico (signal_generator)
- Agrega sentimento (sentiment_analyzer)
- Filtra por SCORE_THRESHOLD e MIN_CONFIDENCE
- Evita duplicados (positions_manager)
- Notifica Telegram (notifier_telegram)
"""

import os
import json
import time
import traceback
from datetime import datetime

# -------- dependências do seu projeto --------
from symbol_rotator import get_next_batch, push_priority
from coingecko_client import fetch_ohlc                     # retorna candles [{open,high,low,close,ts}, ...]
from signal_generator import generate_signal                # retorna dict com confidence (técnico) + entry/tp/sl
from sentiment_analyzer import get_sentiment_score          # retorna polaridade [-1..1]
from positions_manager import should_send_and_register
from notifier_telegram import send_signal_notification

# ------------- parâmetros por ENV -------------
DAYS_OHLC        = int(os.getenv("DAYS_OHLC", "14"))
SCORE_THRESHOLD  = float(os.getenv("SCORE_THRESHOLD", os.getenv("MIN_CONFIDENCE", "0.70")))  # corte final
MIN_CONFIDENCE   = float(os.getenv("MIN_CONFIDENCE", "0.70"))  # gate do sentimento (0.0 para desativar)
WEIGHT_TECH      = float(os.getenv("WEIGHT_TECH", "0.8"))
WEIGHT_SENT      = float(os.getenv("WEIGHT_SENT", "0.2"))
COOLDOWN_H       = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_PCT       = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))
NEAR_MISS_PUSH   = float(os.getenv("NEAR_MISS_PUSH", "0.05"))   # se final ∈ [SCORE_THRESHOLD-NEAR_MISS_PUSH, SCORE_THRESHOLD), empurra p/ prioridade
DATA_RAW_FILE    = os.getenv("DATA_RAW_FILE", "data_raw.json")
SIGNALS_FILE     = os.getenv("SIGNALS_FILE", "signals.json")
DEBUG_SCORE      = os.getenv("DEBUG_SCORE", "True").lower() in ("1", "true", "yes")

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def _to_pct01(x, digits=2):
    try:
        return round(float(x), digits)
    except Exception:
        return x

def _to_pct100(x, digits=1):
    try:
        return round(float(x)*100.0, digits)
    except Exception:
        return x

def _save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def _append_signals(path, items):
    existing = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []
    existing.extend(items)
    _save_json(path, existing)

def run_pipeline():
    start = datetime.utcnow()
    print(f"🕒 Início: {start:%Y-%m-%d %H:%M:%S} UTC")

    # -------- seleção de símbolos (fast-lane + rotação + prioridade) --------
    try:
        selected = get_next_batch()
        print(f"✅ Selecionados (fast-lane + rotação): {', '.join(selected)}")
    except Exception as e:
        print(f"⚠️ Rotator falhou: {e}. Abortando ciclo para evitar 429 desnecessário.")
        return

    # -------- coleta OHLC --------
    data_raw = {}
    for sym in selected:
        try:
            print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
            candles = fetch_ohlc(sym, days=DAYS_OHLC)
            if not candles or len(candles) < 40:
                print(f"⚠️ {sym}: dados insuficientes ({0 if not candles else len(candles)})")
                continue
            data_raw[sym] = candles
        except Exception as e:
            print(f"⚠️ {sym}: falha ao coletar OHLC: {e}")

    try:
        _save_json(DATA_RAW_FILE, data_raw)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(data_raw)} ativos).")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # -------- geração + avaliação de sinais --------
    approved = []
    near_miss = []

    for sym, candles in data_raw.items():
        # 1) sinal técnico
        sig = None
        try:
            sig = generate_signal(sym, candles)
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")

        if sig is None:
            print(f"ℹ️ {sym}: sem sinal técnico.")
            continue

        tech_conf = float(sig.get("confidence", 0.0))          # [0..1]
        tech_pct  = _to_pct100(tech_conf)

        # 2) sentimento → normaliza para [0..1]
        try:
            senti_raw = float(get_sentiment_score(sym))         # [-1..1]
        except Exception as e:
            print(f"⚠️ Sentimento falhou para {sym}: {e}")
            senti_raw = 0.0
        senti_conf = _clamp01((senti_raw + 1.0) / 2.0)         # [0..1]
        senti_pct  = _to_pct100(senti_conf)

        # 3) combinação final
        final_conf = _clamp01(WEIGHT_TECH*tech_conf + WEIGHT_SENT*senti_conf)
        final_pct  = _to_pct100(final_conf)

        if DEBUG_SCORE:
            print(f"   • {sym} Técnico: {tech_pct}% | Sentimento: {senti_pct}%  →  Final: {final_pct}%  (min {int(SCORE_THRESHOLD*100)}% / conf {int(MIN_CONFIDENCE*100)}%)")

        # 4) gates
        if final_conf < SCORE_THRESHOLD:
            # perto do gatilho? coloca na prioridade do próximo ciclo
            if final_conf >= max(0.0, SCORE_THRESHOLD - NEAR_MISS_PUSH):
                near_miss.append(sym)
            print(f"❌ {sym} reprovado por score final.")
            continue

        if MIN_CONFIDENCE > 0.0 and senti_conf < MIN_CONFIDENCE:
            print(f"⛔ {sym} bloqueado por confiança (sentimento): {senti_pct}% < {int(MIN_CONFIDENCE*100)}%")
            continue

        # 5) anti-duplicado / cooldown
        ok_to_send, reason = should_send_and_register(
            {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
            cooldown_hours=COOLDOWN_H,
            change_threshold_pct=CHANGE_PCT,
        )
        if not ok_to_send:
            print(f"⏭️ {sym} pulado ({reason}).")
            continue

        # enriquecer e aprovar
        sig["confidence_tech"] = _to_pct01(tech_conf, 4)
        sig["confidence_sent"] = _to_pct01(senti_conf, 4)
        sig["confidence"]      = _to_pct01(final_conf, 4)
        sig["created_at"]      = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        if "id" not in sig:
            sig["id"] = f"{sym}-{int(time.time())}"

        print(f"✅ {sym} aprovado ({final_pct}%), motivo: {reason}.")
        approved.append(sig)

        # 6) notificar (não trava o loop se falhar)
        try:
            sent = send_signal_notification({
                "symbol": sig["symbol"],
                "entry":  sig.get("entry"),
                "tp":     sig.get("tp"),
                "sl":     sig.get("sl"),
                "risk_reward": sig.get("risk_reward", 2.0),
                "confidence_score": _to_pct100(sig["confidence"]),
                "strategy": (sig.get("strategy") or "RSI+MACD+EMA+BB") + "+NEWS",
                "created_at": sig["created_at"],
                "id": sig["id"],
                "ai_proba": None,   # reservado p/ quando o modelo supervisionado estiver ativo
            })
            print("   ↪️ Notificação enviada." if sent else "   ↪️ Falha ao notificar (veja logs acima).")
        except Exception as e:
            print(f"   ↪️ Erro no envio Telegram: {e}")

        # Politeness para News API (se necessário)
        time.sleep(0.1)

    # 7) push de prioridades (near-miss)
    if near_miss:
        try:
            push_priority(near_miss)
            print(f"📌 Empurrados para prioridade no próximo ciclo: {', '.join(near_miss)}")
        except Exception as e:
            print(f"⚠️ Falha ao push_priority: {e}")

    # 8) persistência de sinais aprovados
    if approved:
        try:
            _append_signals(SIGNALS_FILE, approved)
            print(f"💾 {len(approved)} sinais salvos em {SIGNALS_FILE}.")
        except Exception as e:
            print(f"⚠️ Falha ao salvar {SIGNALS_FILE}: {e}")
    else:
        print("ℹ️ Nenhum sinal aprovado neste ciclo.")

    end = datetime.utcnow()
    print(f"🕒 Fim: {end:%Y-%m-%d %H:%M:%S} UTC")

# ------------- Runner opcional -------------
if __name__ == "__main__":
    # Se você usa runner.py como Start Command, este bloco não roda.
    # Mas manter aqui permite executar manualmente: python main.py
    try:
        run_pipeline()
    except Exception as e:
        print("❌ Erro no ciclo:", e)
        traceback.print_exc()
