# -*- coding: utf-8 -*-
"""
main.py — pipeline principal
- Seleciona pares (fixo via env SYMBOLS ou dinâmico via TOP_SYMBOLS)
- Coleta OHLC (CoinGecko)
- Calcula score técnico
- (Opcional) mistura com sentimento (NewsData + Twitter) com orçamento/caches
- Gera plano (entry/tp/sl) e aplica anti-duplicados
- Notifica Telegram e salva signals.json / history
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List

# ---- Projeto ----
from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal     # seu módulo técnico
from notifier_telegram import send_signal_notification         # já integrado ao Telegram
from positions_manager import should_send_and_register         # anti-duplicados
from signal_generator import append_signal                     # salva no signals.json

# Sentimento (runtime com orçamento)
from sentiment_analyzer import init_sentiment_runtime, runtime

# ==============================
# Config via Environment
# ==============================
def _get_float(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default

def _now_ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

RUN_LOOP        = os.getenv("RUN_LOOP", "false").lower() in ("1","true","yes")
INTERVAL_MIN    = _get_float("RUN_INTERVAL_MIN", 20.0)

SYMBOLS         = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]
TOP_SYMBOLS     = int(os.getenv("TOP_SYMBOLS", "100"))
SELECT_PER_CYCLE= int(os.getenv("SELECT_PER_CYCLE", "12"))

DAYS_OHLC       = int(os.getenv("DAYS_OHLC", "30"))
MIN_BARS        = int(os.getenv("MIN_BARS", "180"))

SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.55"))
MIN_CONFIDENCE  = float(os.getenv("MIN_CONFIDENCE", "0.50"))

COOLDOWN_HOURS       = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

WEIGHT_TECH     = float(os.getenv("WEIGHT_TECH", "1.5"))
WEIGHT_SENT     = float(os.getenv("WEIGHT_SENT", "1.0"))

DATA_RAW_FILE   = os.getenv("DATA_RAW_FILE", "data_raw.json")
CURSOR_FILE     = os.getenv("CURSOR_FILE", "scan_state.json")
SIGNALS_FILE    = os.getenv("SIGNALS_FILE", "signals.json")

SAVE_HISTORY    = os.getenv("SAVE_HISTORY", "true").lower() in ("1","true","yes")
HISTORY_DIR     = os.getenv("HISTORY_DIR", "data/history")

# ==============================
# Cursor / Rotação
# ==============================
def _load_cursor() -> Dict[str, Any]:
    try:
        with open(CURSOR_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"offset": 0, "cycle": 0}

def _save_cursor(state: Dict[str, Any]) -> None:
    with open(CURSOR_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def _rotate(symbols: List[str], take: int) -> List[str]:
    if take <= 0 or not symbols:
        return symbols
    st = _load_cursor()
    off = st.get("offset", 0) % len(symbols)
    batch = [symbols[(off + i) % len(symbols)] for i in range(min(take, len(symbols)))]
    st["offset"] = (off + take) % len(symbols)
    st["cycle"]  = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

# ==============================
# Scoring helpers
# ==============================
def _safe_score(ohlc) -> float:
    """Aceita retorno de score como float, tuple, dict etc. Normaliza p/ [0..1]."""
    try:
        res = score_signal(ohlc)
        if isinstance(res, tuple):
            s = float(res[0])
        elif isinstance(res, dict):
            s = float(res.get("score", res.get("value", res.get("confidence", res.get("prob", 0.0)))))
        else:
            s = float(res)
    except Exception:
        s = 0.0
    if s > 1.0:
        s = s / 100.0
    return max(0.0, min(1.0, round(s, 6)))

def _mix(tech: float, sent: float) -> float:
    total = max(1e-9, WEIGHT_TECH + WEIGHT_SENT)
    return max(0.0, min(1.0, (WEIGHT_TECH*tech + WEIGHT_SENT*sent) / total))

# ==============================
# Histórico (salvar OHLC por símbolo)
# ==============================
def _save_history_ohlc(collected: Dict[str, Any]):
    if not SAVE_HISTORY:
        return
    try:
        os.makedirs(os.path.join(HISTORY_DIR, "ohlc"), exist_ok=True)
        for sym, rows in collected.items():
            path = os.path.join(HISTORY_DIR, "ohlc", f"{sym}.json")
            payload = {"symbol": sym, "bars": rows}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Falha ao salvar histórico OHLC: {e}")

# ==============================
# Pipeline
# ==============================
def run_pipeline():
    print(f"▶️ Runner iniciado. Intervalo = {INTERVAL_MIN} min.")
    # iniciar runtime de sentimento (abre ciclo de orçamento)
    SENT = init_sentiment_runtime()
    SENT.new_cycle()

    # universo
    if SYMBOLS:
        universe = SYMBOLS[:]
    else:
        try:
            universe = fetch_top_symbols(TOP_SYMBOLS)
        except Exception:
            universe = []

    selected = _rotate(universe, SELECT_PER_CYCLE)
    print(f"🧪 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected) if selected else '-'}")

    # coleta OHLC
    collected, ok_symbols = {}, []
    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)
            if not raw or len(raw) < MIN_BARS:
                print(f"❌ Dados insuficientes para {sym}")
                continue
            collected[sym] = raw
            ok_symbols.append(sym)
            print(f"   → OK | candles={len(raw)}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_symbols:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # salvar snapshot do ciclo
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_symbols, "data": collected}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # opcional: salvar histórico por símbolo
    _save_history_ohlc(collected)

    # loop de pontuação + sinal
    saved_count = 0
    for sym in ok_symbols:
        ohlc = collected[sym]
        # técnico
        tech = _safe_score(ohlc)
        print(f"ℹ️ Score {sym}: {round(tech*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")
        if tech < SCORE_THRESHOLD:
            continue

        # sentimento (com orçamento/caches)
        last_close = ohlc[-2][4] if (isinstance(ohlc[0], list) and len(ohlc) >= 2) else (ohlc[-2]["c"] if len(ohlc) >= 2 else None)
        curr_close = ohlc[-1][4] if isinstance(ohlc[0], list) else ohlc[-1]["c"]

        sent_val, s_info = runtime().get_sentiment_score(sym, tech_score=tech, last_close=last_close, curr_close=curr_close)
        print(f"🧠 Sent {sym}: news={s_info['news']}%[{s_info['news_src']}], "
              f"tw={s_info['twitter']}%[{s_info['tw_src']}], mix={s_info['mix']}%")

        conf = _mix(tech, sent_val)
        if conf < MIN_CONFIDENCE:
            continue

        # gerar plano
        try:
            sig = generate_signal(ohlc)
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")
            sig = None
        if not isinstance(sig, dict):
            print(f"⚠️ {sym}: sem sinal técnico.")
            continue

        # completar payload
        sig["symbol"]     = sym
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["confidence"] = float(conf)
        sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB+EXTRA")
        sig["created_at"] = sig.get("created_at", _now_ts())
        if "id" not in sig:
            sig["id"] = f"sig-{int(time.time())}"

        # anti-duplicados
        ok_to_send, reason = should_send_and_register(
            {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT
        )
        if not ok_to_send:
            print(f"🟡 {sym} não enviado ({reason}).")
            continue

        # notificar
        try:
            pushed = send_signal_notification({
                "symbol": sym,
                "entry_price": sig.get("entry"),
                "target_price": sig.get("tp"),
                "stop_loss": sig.get("sl"),
                "risk_reward": sig.get("rr", 2.0),
                "confidence_score": round(conf * 100, 2),
                "strategy": sig.get("strategy", "RSI+MACD+EMA+BB+EXTRA"),
                "created_at": sig.get("created_at"),
                "id": sig.get("id"),
            })
            print("✅ Notificação enviada." if pushed else "❌ Falha no envio (notifier).")
        except Exception as e:
            print(f"⚠️ Falha no envio (notifier): {e}")

        # persistir sinal
        try:
            append_signal(sig)
            saved_count += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar em {SIGNALS_FILE}: {e}")

    print(f"🗂 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {_now_ts()}")

# ==============================
# Runner
# ==============================
if __name__ == "__main__":
    if RUN_LOOP:
        while True:
            t0 = time.time()
            run_pipeline()
            elapsed = time.time() - t0
            sleep_s = max(0.0, INTERVAL_MIN*60 - elapsed)
            for _ in range(int(sleep_s // 30)):
                print(f"⏳ aguardando 30s… (restante {int((sleep_s - _*30))}s)")
                time.sleep(30)
            rem = sleep_s % 30
            if rem > 0:
                time.sleep(rem)
    else:
        run_pipeline()
