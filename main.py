# -*- coding: utf-8 -*-
"""
main.py — pipeline principal com logs de Técnico %, Sentimento % e Mix %
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Tuple

# ---- Módulos do projeto ----
from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register
from signal_generator import append_signal  # salva no signals.json

# ---- Sentimento (opcional; suporta duas assinaturas) ----
HAVE_SENTIMENT = False
def _sentiment_adapter(symbol: str) -> Tuple[float, int]:
    """
    Retorna (score, n). score ∈ [-1, 1]. n = nº de notícias usadas.
    Se não houver módulo/chave ou nada for encontrado, retorna (0.0, 0).
    """
    global HAVE_SENTIMENT
    try:
        # tenta import com duas assinaturas diferentes
        from sentiment_analyzer import get_sentiment  # esperado: (score, n)
        HAVE_SENTIMENT = True
        out = get_sentiment(symbol)
        if isinstance(out, tuple) and len(out) >= 1:
            score = float(out[0])
            n = int(out[1]) if len(out) > 1 else 0
            return max(-1.0, min(1.0, score)), max(0, n)
        # fallback: função retorna só um float
        score = float(out)
        return max(-1.0, min(1.0, score)), 0
    except Exception:
        # segunda tentativa: função antiga get_sentiment_score(symbol) -> float
        try:
            from sentiment_analyzer import get_sentiment_score
            HAVE_SENTIMENT = True
            score = float(get_sentiment_score(symbol))
            return max(-1.0, min(1.0, score)), 0
        except Exception:
            return 0.0, 0

# ==============================
# Config via Environment
# ==============================
SYMBOLS = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]  # vazio = dinâmico
TOP_SYMBOLS       = int(os.getenv("TOP_SYMBOLS", "100"))
SELECT_PER_CYCLE  = int(os.getenv("SELECT_PER_CYCLE", "8"))
DAYS_OHLC         = int(os.getenv("DAYS_OHLC", "14"))
MIN_BARS          = int(os.getenv("MIN_BARS", "40"))

SCORE_THRESHOLD   = float(os.getenv("SCORE_THRESHOLD", "0.70"))
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.60"))

COOLDOWN_HOURS        = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT  = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

WEIGHT_TECH = float(os.getenv("WEIGHT_TECH", "1.0"))
WEIGHT_SENT = float(os.getenv("WEIGHT_SENT", "0.5"))  # se 0, ignora sentimento

DATA_RAW_FILE  = os.getenv("DATA_RAW_FILE", "data_raw.json")
CURSOR_FILE    = os.getenv("CURSOR_FILE", "scan_state.json")
SIGNALS_FILE   = os.getenv("SIGNALS_FILE", "signals.json")

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _ensure_cursor() -> Dict[str, Any]:
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
    st = _ensure_cursor()
    off = st.get("offset", 0) % len(symbols)
    batch = [symbols[(off + i) % len(symbols)] for i in range(min(take, len(symbols)))]
    st["offset"] = (off + take) % len(symbols)
    st["cycle"] = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _safe_score(ohlc) -> float:
    """Aceita float, tuple, dict; normaliza p/ 0..1."""
    try:
        res = score_signal(ohlc)
        if isinstance(res, tuple):
            s = float(res[0])
        elif isinstance(res, dict):
            s = float(res.get("score", res.get("value", res.get("confidence", res.get("prob", 0.0)))))
        else:
            s = float(res)
    except Exception as e:
        print(f"⚠️ _safe_score: retorno inesperado de score_signal -> {type(res) if 'res' in locals() else type(e)} ({e})")
        s = 0.0
    if s > 1.0:
        s = s / 100.0
    return max(0.0, min(1.0, round(s, 6)))

def _mix_confidence(score_tech: float, sent_score: float) -> float:
    """score_tech ∈ [0,1]; sent_score ∈ [-1,1] → 0..1"""
    sent01 = (sent_score + 1.0) / 2.0
    total_w = max(1e-9, WEIGHT_TECH + WEIGHT_SENT)
    mixed = (WEIGHT_TECH * score_tech + WEIGHT_SENT * sent01) / total_w
    return max(0.0, min(1.0, mixed))

def run_pipeline():
    # chave de notícias presente?
    have_news_key = bool(os.getenv("NEWS_API_KEY") or os.getenv("CHAVE_API_DE_NOTÍCIAS"))
    print(f"🔎 NEWS key presente?: {have_news_key}")

    print("▶️ Runner iniciado. Intervalo = 20.0 min.")
    print("🧩 Coletando PREÇOS / OHLC…")

    # universo
    if SYMBOLS:
        universe = SYMBOLS[:]
    else:
        universe = fetch_top_symbols(TOP_SYMBOLS)

    selected = _rotate(universe, SELECT_PER_CYCLE)
    print(f"🧪 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    # coleta OHLC
    collected: Dict[str, Any] = {}
    ok_symbols: List[str] = []
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

    # dump debug
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_symbols, "data": collected}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    saved_count = 0
    for sym in ok_symbols:
        ohlc = collected[sym]

        # 1) técnico
        tech = _safe_score(ohlc)             # 0..1
        tech_pct = round(tech * 100, 1)

        # 2) sentimento (opcional)
        sent_score, n_news = (0.0, 0)
        used_sentiment = False
        if have_news_key and WEIGHT_SENT > 0:
            s, n = _sentiment_adapter(sym)
            sent_score, n_news = s, n
            used_sentiment = True

        # 3) mix
        mixed = _mix_confidence(tech, sent_score if used_sentiment else 0.0)
        mixed_pct = round(mixed * 100, 1)

        # LOG detalhado (sempre)
        if used_sentiment and HAVE_SENTIMENT:
            s_pct = round(((sent_score + 1.0) / 2.0) * 100, 1)  # converter -1..1 → 0..100%
            print(
                f"📊 {sym} | Técnico: {tech_pct}% "
                f"| Sentimento: {s_pct}% (n={n_news}) "
                f"| Mix(T:{WEIGHT_TECH},S:{WEIGHT_SENT}): {mixed_pct}% "
                f"(min {int(SCORE_THRESHOLD*100)}%)"
            )
        else:
            print(f"ℹ️ _safe_score: usando fallback técnico = {tech_pct}%")
            print(f"ℹ️ Score {sym}: {tech_pct}% (min {int(SCORE_THRESHOLD*100)}%)")

        # filtros
        if mixed < MIN_CONFIDENCE or tech < SCORE_THRESHOLD:
            continue

        # 4) gera sinal
        try:
            sig = generate_signal(ohlc)  # dict com entry/tp/sl/rr/strategy...
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")
            sig = None

        if not isinstance(sig, dict):
            print(f"⚠️ {sym}: sem sinal técnico.")
            continue

        # completa sinal
        sig["symbol"]     = sym
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["confidence"] = float(mixed)
        sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB")
        sig["created_at"] = sig.get("created_at", _ts())
        sig.setdefault("id", f"{sym}-{int(time.time())}")

        # anti-duplicado
        ok_to_send, reason = should_send_and_register(
            {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT
        )
        if not ok_to_send:
            print(f"🟡 {sym} não enviado ({reason}).")
            continue

        # envia
        pushed = False
        try:
            pushed = send_signal_notification({
                "symbol": sym,
                "entry_price": sig.get("entry"),
                "target_price": sig.get("tp"),
                "stop_loss": sig.get("sl"),
                "risk_reward": sig.get("rr", 2.0),
                "confidence_score": mixed_pct,
                "strategy": sig.get("strategy"),
                "created_at": sig.get("created_at"),
                "id": sig.get("id"),
            })
        except Exception as e:
            print(f"⚠️ Falha no envio (notifier): {e}")

        print("✅ Notificação enviada." if pushed else "❌ Falha no envio (ver notifier_telegram).")

        # registra
        try:
            append_signal(sig)
            saved_count += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar em {SIGNALS_FILE}: {e}")

    print(f"🗂 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {_ts()}")

if __name__ == "__main__":
    run_pipeline()
