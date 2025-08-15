# -*- coding: utf-8 -*-
"""
main.py — pipeline principal
- Seleciona o conjunto de moedas (dinâmico via CoinGecko ou fixo via env)
- Coleta OHLC
- Calcula score técnico (score_signal)
- (Opcional) mistura com sentimento (get_sentiment_score)
- Gera sinal técnico (generate_signal)
- Evita duplicados (positions_manager.should_send_and_register)
- Envia Telegram (notifier_telegram) e grava em signals.json (signal_generator.append_signal)
"""

from __future__ import annotations

import os
import json
import time
from datetime import datetime
from typing import Any, Dict, List

# ==============================
# Importa módulos do projeto
# ==============================
from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register
from signal_generator import append_signal  # salva/atualiza signals.json

# ---- Sentimento (opcional) ----
try:
    # se o arquivo existir, usa; se não, seguimos sem sentimento
    from sentiment_analyzer import get_sentiment_score  # retorna -1..1
except Exception:
    def get_sentiment_score(symbol: str) -> float:
        return 0.0

# ==============================
# Config via Environment
# ==============================
# universo: se SYMBOLS vazio -> usa TOP_SYMBOLS dinâmico no CoinGecko
SYMBOLS          = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]
TOP_SYMBOLS      = int(os.getenv("TOP_SYMBOLS", "100"))
SELECT_PER_CYCLE = int(os.getenv("SELECT_PER_CYCLE", "12"))

# coleta OHLC
DAYS_OHLC        = int(os.getenv("DAYS_OHLC", "14"))
MIN_BARS         = int(os.getenv("MIN_BARS", "40"))

# limiares
SCORE_THRESHOLD  = float(os.getenv("SCORE_THRESHOLD", "0.70"))  # técnico
MIN_CONFIDENCE   = float(os.getenv("MIN_CONFIDENCE", "0.60"))   # após mistura

# anti-duplicados
COOLDOWN_HOURS       = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

# mistura técnica + sentimento (se WEIGHT_SENT = 0, ignora sentimento)
WEIGHT_TECH = float(os.getenv("WEIGHT_TECH", "1.0"))
WEIGHT_SENT = float(os.getenv("WEIGHT_SENT", "0.0"))

# arquivos utilitários
DATA_RAW_FILE = os.getenv("DATA_RAW_FILE", "data_raw.json")       # dump de debug
CURSOR_FILE   = os.getenv("CURSOR_FILE", "scan_state.json")       # rotação das moedas
SIGNALS_FILE  = os.getenv("SIGNALS_FILE", "signals.json")         # usado por append_signal

# ==============================
# Helpers
# ==============================
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _ensure_cursor() -> Dict[str, Any]:
    try:
        with open(CURSOR_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"offset": 0, "cycle": 0}

def _save_cursor(state: Dict[str, Any]) -> None:
    try:
        with open(CURSOR_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _rotate(symbols: List[str], take: int) -> List[str]:
    """Seleciona lote diferente a cada ciclo, sem ficar preso nas mesmas moedas."""
    if take <= 0 or not symbols:
        return symbols
    st = _ensure_cursor()
    off = int(st.get("offset", 0)) % len(symbols)
    batch: List[str] = []
    for i in range(min(take, len(symbols))):
        batch.append(symbols[(off + i) % len(symbols)])
    st["offset"] = (off + take) % len(symbols)
    st["cycle"] = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _safe_score(ohlc) -> float:
    """
    Aceita retornos variados do score_signal:
      - float 0..1
      - tuple (score, ...)
      - dict {"score": 0..1, ...} (ou "value"/"confidence"/"prob")
      - porcentagem (>1.0) -> normaliza para 0..1
    """
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

    if s > 1.0:  # caso venha em %
        s = s / 100.0
    return max(0.0, min(1.0, round(s, 6)))

def _mix_confidence(score_tech: float, sent: float) -> float:
    """
    Junta técnico (0..1) com sentimento (-1..1) -> (0..1).
    Se WEIGHT_SENT = 0, sai igual ao técnico.
    """
    sent01 = (sent + 1.0) / 2.0  # -1..1 -> 0..1
    total_w = max(1e-9, WEIGHT_TECH + WEIGHT_SENT)
    mixed = (WEIGHT_TECH * score_tech + WEIGHT_SENT * sent01) / total_w
    return max(0.0, min(1.0, mixed))

def _news_key_probe() -> None:
    # só pra facilitar debug rápido no log
    has_key = bool(os.getenv("NEWSDATA_API_KEY") or os.getenv("THENEWS_API_KEY") or os.getenv("NEWS_API_KEY"))
    print("🔎 NEWS key presente?:", has_key)

# ==============================
# Pipeline principal
# ==============================
def run_pipeline():
    _news_key_probe()
    print("🧩 Coletando PREÇOS / OHLC…")

    # 1) escolhe universo (fixo via env OU dinâmico no CG)
    if SYMBOLS:
        universe = SYMBOLS[:]
    else:
        universe = fetch_top_symbols(TOP_SYMBOLS)

    # 2) rotaciona para este ciclo
    selected = _rotate(universe, SELECT_PER_CYCLE)
    print(f"🧪 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    # 3) coleta OHLC
    collected: Dict[str, Any] = {}
    ok_symbols: List[str] = []

    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)  # retorna [[ts, o, h, l, c], ...]
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

    # 4) salva dump de debug
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_symbols, "data": collected}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # 5) pontua, mistura sentimento e gera sinais
    saved_count = 0
    for sym in ok_symbols:
        ohlc = collected[sym]

        # score técnico
        score = _safe_score(ohlc)
        print(f"ℹ️ Score {sym}: {round(score*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")
        if score < SCORE_THRESHOLD:
            continue

        # sentimento (opcional)
        try:
            sent = get_sentiment_score(sym)  # -1..1
        except Exception:
            sent = 0.0

        confidence = _mix_confidence(score, sent)
        if confidence < MIN_CONFIDENCE:
            continue

        # gera plano (entry/tp/sl)
        try:
            sig = generate_signal(ohlc)  # deve retornar dict com entry/tp/sl/rr/strategy
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")
            sig = None

        if not isinstance(sig, dict):
            print(f"⚠️ {sym}: sem sinal técnico.")
            continue

        # completa payload
        sig["symbol"]     = sym
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["confidence"] = float(confidence)
        sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB")
        sig["created_at"] = sig.get("created_at", _ts())
        if "id" not in sig:
            sig["id"] = f"{sym}-{int(time.time())}"

        # anti-duplicado
        ok_to_send, reason = should_send_and_register(
            {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT
        )
        if not ok_to_send:
            print(f"🟡 {sym} não enviado ({reason}).")
            continue

        # envia Telegram
        pushed = False
        try:
            pushed = send_signal_notification({
                "symbol": sym,
                "entry_price": sig.get("entry"),
                "target_price": sig.get("tp"),
                "stop_loss": sig.get("sl"),
                "risk_reward": sig.get("rr", 2.0),
                "confidence_score": round(confidence * 100, 2),
                "strategy": sig.get("strategy", "RSI+MACD+EMA+BB"),
                "created_at": sig.get("created_at"),
                "id": sig.get("id"),
            })
        except Exception as e:
            print(f"⚠️ Falha no envio (notifier): {e}")

        if pushed:
            print("✅ Notificação enviada.")
        else:
            print("❌ Falha no envio (ver notifier_telegram).")

        # persiste em signals.json
        try:
            append_signal(sig)
            saved_count += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar em {SIGNALS_FILE}: {e}")

    print(f"🗂 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {_ts()}")

# exec local/debug
if __name__ == "__main__":
    run_pipeline()
