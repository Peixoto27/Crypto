# -*- coding: utf-8 -*-
"""
main.py — pipeline principal (v2)
- Seleciona o universo de moedas (fixo via env SYMBOLS ou dinâmico TOP_SYMBOLS)
- Coleta OHLC do CoinGecko (data_fetcher_coingecko)
- Calcula score técnico e opcionalmente mistura com sentimento
- Gera sinal (entry/tp/sl) quando houver
- Evita duplicados (positions_manager)
- Envia sinal novo (notifier_v2.notify_new_signal)
- Registra no histórico (history_manager.record_signal) e no arquivo de sinais (signal_generator.append_signal)
- Rotula sinais antigos (history_manager.evaluate_pending_outcomes)
- Envia atualizações de trade TP/SL/CLOSE (notifier_v2.monitor_and_notify_closures)
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List

# ---- Fetchers e estratégias do seu projeto ----
from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal
from positions_manager import should_send_and_register
from signal_generator import append_signal  # salva em signals.json

# ---- Notificações (v2) ----
try:
    from notifier_v2 import notify_new_signal, monitor_and_notify_closures
except Exception:
    def notify_new_signal(_payload: dict) -> bool:
        print("⚠️ notifier_v2.notify_new_signal indisponível — pulando envio inicial.")
        return False
    def monitor_and_notify_closures() -> dict:
        print("⚠️ notifier_v2.monitor_and_notify_closures indisponível — pulando avisos TP/SL.")
        return {"checked": 0, "sent_tp": 0, "sent_sl": 0, "sent_close": 0, "skipped_dup": 0, "errors": 0}

# ---- Sentimento (opcional) ----
try:
    from sentiment_analyzer import get_sentiment_score  # retorna [-1..1]
except Exception:
    def get_sentiment_score(symbol: str) -> float:
        return 0.0

# ---- Histórico (opcional) ----
try:
    from history_manager import record_signal, evaluate_pending_outcomes
except Exception:
    def record_signal(_payload: dict) -> None:
        print("⚠️ history_manager.record_signal indisponível — pulando registro no histórico.")
    def evaluate_pending_outcomes(lookahead_hours: int = 48) -> None:
        print("⚠️ history_manager.evaluate_pending_outcomes indisponível — pulando auto-rotulagem.")

# ==============================
# Config via Environment
# ==============================
def _env(name: str, default: str) -> str:
    return os.getenv(name, default)

SYMBOLS = [s for s in _env("SYMBOLS", "").replace(" ", "").split(",") if s]  # vazio => dinâmico

TOP_SYMBOLS       = int(_env("TOP_SYMBOLS", "100"))         # universo dinâmico
SELECT_PER_CYCLE  = int(_env("SELECT_PER_CYCLE", "12"))     # quantas por ciclo
DAYS_OHLC         = int(_env("DAYS_OHLC", "30"))
MIN_BARS          = int(_env("MIN_BARS", "180"))

SCORE_THRESHOLD   = float(_env("SCORE_THRESHOLD", "0.70"))  # score técnico mínimo (0..1)
MIN_CONFIDENCE    = float(_env("MIN_CONFIDENCE", "0.60"))   # confiança final mínima (0..1)

# anti-duplicados
COOLDOWN_HOURS        = float(_env("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT  = float(_env("CHANGE_THRESHOLD_PCT", "1.0"))

# mistura técnica + sentimento
WEIGHT_TECH = float(_env("WEIGHT_TECH", "1.0"))
WEIGHT_SENT = float(_env("WEIGHT_SENT", "0.0"))  # 0 = ignora sentimento

# arquivos utilitários
DATA_RAW_FILE  = _env("DATA_RAW_FILE", "data_raw.json")
CURSOR_FILE    = _env("CURSOR_FILE", "scan_state.json")     # rotação entre ciclos
SIGNALS_FILE   = _env("SIGNALS_FILE", "signals.json")

# histórico (flags)
SAVE_HISTORY   = _env("SAVE_HISTORY", "true").lower() in ("1", "true", "yes")
AUTO_LABEL_HRS = int(_env("AUTO_LABEL_LOOKAHEAD_HOURS", "48"))

# filtro para pares estáveis (opcional)
EXCLUDE_STABLES = _env("EXCLUDE_STABLES", "true").lower() in ("1", "true", "yes")
STABLE_KEYS = ("USD", "USDT", "FDUSD", "USDC", "BUSD")  # ajuste se quiser

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
    except Exception as e:
        print(f"⚠️ Falha ao salvar {CURSOR_FILE}: {e}")

def _rotate(symbols: List[str], take: int) -> List[str]:
    """Seleciona um lote diferente a cada ciclo, sem repetir as mesmas sempre."""
    if take <= 0 or not symbols:
        return symbols
    st = _ensure_cursor()
    off = st.get("offset", 0) % len(symbols)
    batch = []
    for i in range(min(take, len(symbols))):
        batch.append(symbols[(off + i) % len(symbols)])
    st["offset"] = (off + take) % len(symbols)
    st["cycle"] = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _safe_score(ohlc) -> float:
    """
    Aceita retornos diversos de score_signal:
      - float 0..1
      - tuple(score, ...)
      - dict {score|value|confidence|prob: 0..1}
      - porcentagem (>1.0 => divide por 100)
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
    if s > 1.0:
        s /= 100.0
    return max(0.0, min(1.0, round(s, 6)))

def _mix_confidence(score_tech: float, sent: float) -> float:
    """Mistura técnico (0..1) com sentimento (-1..1) => (0..1)."""
    sent01 = (sent + 1.0) / 2.0  # -1..1 -> 0..1
    total_w = max(1e-9, WEIGHT_TECH + WEIGHT_SENT)
    mixed = (WEIGHT_TECH * score_tech + WEIGHT_SENT * sent01) / total_w
    return max(0.0, min(1.0, mixed))

def _looks_stable_pair(sym: str) -> bool:
    """Heurística simples: evita pares de stable x stable (ex.: FDUSDUSDT)."""
    if not EXCLUDE_STABLES:
        return False
    s = sym.upper()
    # se contiver dois 'USD*' no mesmo par, filtra
    count = sum(1 for k in STABLE_KEYS if k in s)
    return count >= 2

# ==============================
# Pipeline principal
# ==============================
def run_pipeline():
    print(f"🔎 NEWS ativo?: {('True' if WEIGHT_SENT > 0 else 'False')} | IA ativa?: {os.getenv('USE_AI','true')} | Histórico ativado?: {SAVE_HISTORY}")
    print(f"▶️ Runner iniciado. Intervalo = {os.getenv('RUN_INTERVAL_MIN','20')} min.")

    # 1) unidade / universo
    if SYMBOLS:
        universe = SYMBOLS[:]
    else:
        try:
            universe = fetch_top_symbols(TOP_SYMBOLS)
        except Exception as e:
            print(f"❌ fetch_top_symbols falhou: {e}")
            universe = []

    # filtro para pares estáveis redundantes
    if EXCLUDE_STABLES and universe:
        before = len(universe)
        universe = [s for s in universe if not _looks_stable_pair(s)]
        after = len(universe)
        if after != before:
            print(f"🧼 Removidos {before - after} pares estáveis redundantes (ex.: FDUSDUSDT).")

    # 2) rotação
    selected = _rotate(universe, SELECT_PER_CYCLE) if universe else []
    print(f"🧪 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected) if selected else '—'}")

    # 3) coleta OHLC
    collected: Dict[str, Any] = {}
    ok_symbols: List[str] = []

    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)  # lista [[ts,o,h,l,c], ...] ou lista de dicts
            if not raw or len(raw) < MIN_BARS:
                print(f"❌ Dados insuficientes para {sym} ({0 if not raw else len(raw)}/{MIN_BARS})")
                continue
            collected[sym] = raw
            ok_symbols.append(sym)
            print(f"   → OK | candles={len(raw)}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_symbols:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # 4) dump debug do ciclo
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_symbols, "data": collected}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # 5) pontua e gera sinais
    saved_count = 0
    for sym in ok_symbols:
        ohlc = collected.get(sym)

        # score técnico
        score = _safe_score(ohlc)
        print(f"ℹ️ Técnico {sym}: {round(score*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")
        if score < SCORE_THRESHOLD:
            continue

        # sentimento (opcional)
        try:
            sent = get_sentiment_score(sym)
        except Exception:
            sent = 0.0
        print(f"🧠 Sentimento {sym}: {round(((sent+1)/2)*100,1)}% (raw {round(sent,2)}) | pesos T:{WEIGHT_TECH} S:{WEIGHT_SENT}")

        conf = _mix_confidence(score, sent)
        print(f"📐 Confiança {sym}: {round(conf*100,2)}% (min {int(MIN_CONFIDENCE*100)}%)")
        if conf < MIN_CONFIDENCE:
            continue

        # gera plano (entry/tp/sl)
        try:
            sig = generate_signal(ohlc)
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")
            sig = None

        if not sig or not isinstance(sig, dict):
            print(f"⚠️ {sym}: sem sinal técnico.")
            continue

        # payload padronizado
        sig["symbol"]     = sym
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["confidence"] = float(conf)
        sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB+EXTRA")
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

        # envio do sinal novo (via notifier_v2)
        payload = {
            "symbol": sym,
            "entry_price": sig.get("entry"),
            "target_price": sig.get("tp"),
            "stop_loss": sig.get("sl"),
            "risk_reward": sig.get("rr", 2.0),
            "confidence_score": round(conf * 100, 2),
            "strategy": sig.get("strategy", "RSI+MACD+EMA+BB+EXTRA"),
            "created_at": sig.get("created_at"),
            "id": sig.get("id"),
        }
        try:
            pushed = notify_new_signal(payload)
            if pushed:
                print("✅ Sinal inicial notificado.")
            else:
                print("❌ Falha ao notificar sinal inicial.")
        except Exception as e:
            print(f"⚠️ Falha no envio (notifier_v2): {e}")

        # grava histórico e signals.json
        try:
            if SAVE_HISTORY:
                # inclui features se tiver no sig (opcional)
                record_signal({
                    "id": sig["id"],
                    "symbol": sym,
                    "created_at": sig["created_at"],
                    "entry": sig.get("entry"),
                    "tp": sig.get("tp"),
                    "sl": sig.get("sl"),
                    "rr": sig.get("rr", 2.0),
                    "confidence": sig.get("confidence"),
                    "strategy": sig.get("strategy"),
                    "features": sig.get("features", {})  # se apply_strategies populou
                })
        except Exception as e:
            print(f"⚠️ Erro ao registrar no histórico: {e}")

        try:
            append_signal(sig)
            saved_count += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar em {SIGNALS_FILE}: {e}")

    print(f"🗂 {saved_count} sinais salvos em {SIGNALS_FILE}")

    # 6) auto-rotulagem e avisos de fechamento
    try:
        if SAVE_HISTORY:
            evaluate_pending_outcomes(lookahead_hours=AUTO_LABEL_HRS)
    except Exception as e:
        print(f"⚠️ evaluate_pending_outcomes falhou: {e}")

    try:
        monitor_and_notify_closures()
    except Exception as e:
        print(f"⚠️ monitor_and_notify_closures falhou: {e}")

    print(f"🕒 Fim: {_ts()}")


if __name__ == "__main__":
    run_pipeline()
