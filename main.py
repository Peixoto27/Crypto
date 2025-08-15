# -*- coding: utf-8 -*-
"""
main.py — pipeline principal
- Seleciona o conjunto de moedas (dinâmico via CoinGecko ou fixo via env)
- Coleta OHLC
- Calcula score técnico
- (Opcional) mistura com sentimento (NewsData.io / RSS)
- Gera sinal (entry/tp/sl) quando houver
- Evita duplicados via positions_manager
- Envia para o Telegram e grava em signals.json
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Tuple

# ========= Módulos do projeto =========
from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register
from signal_generator import append_signal  # salva no signals.json

# ========= Sentimento (opcional) =========
# Aceita duas assinaturas:
#   get_sentiment_score("BTCUSDT") -> float   (-1..1)
#   get_sentiment_score("BTCUSDT") -> (float, int)  # score e contagem de notícias usadas
try:
    from sentiment_analyzer import get_sentiment_score as _sentiment_fn
    HAVE_SENTIMENT = True
except Exception:
    HAVE_SENTIMENT = False
    def _sentiment_fn(symbol: str):
        return 0.0

def _get_sent_and_count(symbol: str) -> Tuple[float, int | None]:
    """Retorna (score -1..1, n_noticias ou None). Nunca levanta exceção."""
    try:
        res = _sentiment_fn(symbol)
        if isinstance(res, tuple):
            score = float(res[0])
            n = None
            if len(res) > 1 and res[1] is not None:
                try:
                    n = int(res[1])
                except Exception:
                    n = None
        else:
            score = float(res)
            n = None
    except Exception as e:
        print(f"🧠 Sentimento erro {symbol}: {e}")
        return 0.0, None

    # clamp
    score = max(-1.0, min(1.0, score))
    return score, n

# ========= Config via Environment =========
SYMBOLS = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]  # vazio = dinâmico

TOP_SYMBOLS       = int(os.getenv("TOP_SYMBOLS", "100"))          # quando dinâmico
SELECT_PER_CYCLE  = int(os.getenv("SELECT_PER_CYCLE", "12"))      # quantas moedas por ciclo
DAYS_OHLC         = int(os.getenv("DAYS_OHLC", "14"))
MIN_BARS          = int(os.getenv("MIN_BARS", "40"))

SCORE_THRESHOLD   = float(os.getenv("SCORE_THRESHOLD", "0.70"))   # limiar score técnico
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.60"))    # limiar confiança final

# anti-duplicados
COOLDOWN_HOURS        = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT  = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

# mistura técnica + sentimento
WEIGHT_TECH = float(os.getenv("WEIGHT_TECH", "1.0"))
WEIGHT_SENT = float(os.getenv("WEIGHT_SENT", "0.0"))  # 0.0 = ignorar sentimento

# arquivos auxiliares
DATA_RAW_FILE  = os.getenv("DATA_RAW_FILE", "data_raw.json")
CURSOR_FILE    = os.getenv("CURSOR_FILE", "scan_state.json")   # para rotacionar as moedas
SIGNALS_FILE   = os.getenv("SIGNALS_FILE", "signals.json")

# Só para log: key de news presente?
print("🔎 NEWS key presente?:", bool(os.getenv("NEWS_API_KEY")))

# ========= Helpers =========
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
    """Seleciona um 'lote' diferente a cada ciclo, sem repetir as mesmas sempre."""
    if take <= 0 or not symbols:
        return symbols
    st = _ensure_cursor()
    off = st.get("offset", 0) % len(symbols)
    batch = []
    for i in range(min(take, len(symbols))):
        batch.append(symbols[(off + i) % len(symbols)])
    # avança o offset para o próximo ciclo
    st["offset"] = (off + take) % len(symbols)
    st["cycle"] = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _safe_score(ohlc) -> float:
    """
    Chama score_signal e tolera diferentes formatos de retorno:
      - float 0..1
      - tuple (score, ...)
      - dict {"score": 0..1, ...}
    Nunca levanta exceção; retorna 0.0 em caso de erro.
    """
    try:
        res = score_signal(ohlc)
        if isinstance(res, tuple):
            s = float(res[0])
        elif isinstance(res, dict):
            s = float(
                res.get("score", res.get("value", res.get("confidence", res.get("prob", 0.0))))
            )
        else:
            s = float(res)
    except Exception as e:
        print(f"⚠️ DEBUG score_signal quebrou: {e}")
        s = 0.0

    # normaliza se vier em %
    if s > 1.0:
        s = s / 100.0
    # clip 0..1
    return max(0.0, min(1.0, round(s, 6)))

def _mix_confidence(score_tech: float, sent: float) -> float:
    """
    Junta técnico (0..1) com sentimento (-1..1) => (0..1).
    WEIGHT_SENT = 0 mantém comportamento 100% técnico.
    """
    sent01 = (sent + 1.0) / 2.0  # -1..1 -> 0..1
    total_w = max(1e-9, WEIGHT_TECH + WEIGHT_SENT)
    mixed = (WEIGHT_TECH * score_tech + WEIGHT_SENT * sent01) / total_w
    return max(0.0, min(1.0, mixed))

# ========= Pipeline =========
def run_pipeline():
    print("🧩 Coletando PREÇOS / OHLC…")
    collected: Dict[str, Any] = {}
    ok_symbols: List[str] = []

    # 1) universo
    if SYMBOLS:
        universe = SYMBOLS[:]  # lista fixa via env
    else:
        universe = fetch_top_symbols(TOP_SYMBOLS)  # dinâmica no CoinGecko

    # 2) lote deste ciclo (rotação)
    selected = _rotate(universe, SELECT_PER_CYCLE)
    print(f"🧪 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    # 3) coleta OHLC
    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)  # list de candles já normalizada pelo fetcher
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

    # 4) salva debug
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
        print(f"ℹ️ Score {sym}: {round(score*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")
        if score < SCORE_THRESHOLD:
            continue

        # sentimento opcional
        sent_score, n_news = _get_sent_and_count(sym) if HAVE_SENTIMENT else (0.0, None)
        if WEIGHT_SENT > 0.0:
            tag = f"(n={n_news})" if n_news is not None else "(n=?)"
            print(f"🧠 Sentimento {sym}: {round(sent_score,3)} {tag}")

        conf = _mix_confidence(score, sent_score)
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

        # completa o payload do sinal
        sig["symbol"]     = sym
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["confidence"] = float(conf)
        sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB")
        sig["created_at"] = sig.get("created_at", _ts())
        if "id" not in sig:
            sig["id"] = f"{sym}-{int(time.time())}"

        # anti-duplicado / cooldown
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
                "confidence_score": round(conf * 100, 2),
                "strategy": sig.get("strategy", "RSI+MACD+EMA+BB"),
                "created_at": sig.get("created_at"),
                "id": sig.get("id"),
            })
        except Exception as e:
            print(f"⚠️ Falha no envio (notifier): {e}")

        print("✅ Notificação enviada." if pushed else "❌ Falha no envio (ver notifier_telegram).")

        # persiste no arquivo de sinais
        try:
            append_signal(sig)
            saved_count += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar em {SIGNALS_FILE}: {e}")

    print(f"🗂 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {_ts()}")

# ========= Entrypoint =========
if __name__ == "__main__":
    run_pipeline()
