# -*- coding: utf-8 -*-
"""
main.py — pipeline principal

- Seleciona o conjunto de moedas (dinâmico via CoinGecko ou fixo via env)
- Coleta OHLC
- Normaliza candles para dict (open/high/low/close)
- Calcula score técnico
- (Opcional) mistura com sentimento (NewsData.io)
- Gera sinal (entry/tp/sl) quando houver
- Evita duplicados via positions_manager
- Envia para o Telegram e grava em signals.json
- (Novo) Salva histórico de snapshots e scores via history_manager (opcional)
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

# ---- Histórico (opcional; no-op se ausente) ----
HISTORY_ENABLED = os.getenv("HISTORY_ENABLED", "true").lower() == "true"
HISTORY_MAX_CANDLES = int(os.getenv("HISTORY_MAX_CANDLES", "200"))
try:
    # você pode implementar essas funções no history_manager:
    #   log_snapshot(symbol: str, candles: List[dict], meta: dict) -> None
    #   log_score(row: dict) -> None
    from history_manager import log_snapshot, log_score  # type: ignore
except Exception:
    def log_snapshot(symbol: str, candles: List[Dict[str, float]], meta: Dict[str, Any]) -> None:
        pass
    def log_score(row: Dict[str, Any]) -> None:
        pass

# ---- Sentimento (opcional; se não existir continua normal) ----
try:
    from sentiment_analyzer import get_sentiment_score  # pode retornar float ou (float, n)
    print("🔎 NEWS key presente?:", bool(os.getenv("NEWS_API_KEY") or os.getenv("CHAVE_API_DE_NOTÍCIAS")))
except Exception:
    def get_sentiment_score(symbol: str):
        return 0.0
    print("🔎 NEWS key presente?: False (sentimento desativado)")

# ==============================
# Config via Environment
# ==============================
# Lista fixa? (se vazio, usa dinâmica via CoinGecko Top N)
SYMBOLS = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]

TOP_SYMBOLS       = int(os.getenv("TOP_SYMBOLS", "100"))           # universo quando dinâmico
SELECT_PER_CYCLE  = int(os.getenv("SELECT_PER_CYCLE", "8"))        # quantas moedas por ciclo
DAYS_OHLC         = int(os.getenv("DAYS_OHLC", "14"))              # janela em dias no CoinGecko
MIN_BARS          = int(os.getenv("MIN_BARS", "84"))               # mínimo de candles aceitos

SCORE_THRESHOLD   = float(os.getenv("SCORE_THRESHOLD", "0.70"))    # limiar técnico (0..1)
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.60"))     # limiar confiança final (0..1)

# anti-duplicados
COOLDOWN_HOURS        = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT  = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

# mistura técnica + sentimento
WEIGHT_TECH = float(os.getenv("WEIGHT_TECH", "1.0"))
WEIGHT_SENT = float(os.getenv("WEIGHT_SENT", "0.5"))   # 0.0 = ignorar sentimento

# arquivos utilitários
DATA_RAW_FILE  = os.getenv("DATA_RAW_FILE",  os.getenv("ARQUIVO_DADOS_BRUTOS", "data_raw.json"))
CURSOR_FILE    = os.getenv("CURSOR_FILE",    os.getenv("ARQUIVO_CURSOR", "scan_state.json"))
SIGNALS_FILE   = os.getenv("SIGNALS_FILE",   os.getenv("ARQUIVO_SINAIS", "signals.json"))

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
    """Seleciona um 'lote' diferente a cada ciclo, sem repetir as mesmas sempre."""
    if take <= 0 or not symbols:
        return symbols
    st = _ensure_cursor()
    off = st.get("offset", 0) % len(symbols)
    batch = []
    for i in range(min(take, len(symbols))):
        batch.append(symbols[(off + i) % len(symbols)])
    # avança o offset
    st["offset"] = (off + take) % len(symbols)
    st["cycle"] = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _as_dict_candles(ohlc_raw):
    """
    Garante que cada candle tenha chaves 'open','high','low','close'.
    Aceita:
      - lista [ts, o, h, l, c]
      - tupla (ts, o, h, l, c)
      - dict {'open','high','low','close',...}
      - também tolera [o,h,l,c] sem timestamp
    Retorna: lista de dicts padronizados.
    """
    fixed = []
    for row in (ohlc_raw or []):
        try:
            if isinstance(row, dict):
                o = float(row.get("open"))
                h = float(row.get("high"))
                l = float(row.get("low"))
                c = float(row.get("close"))
                fixed.append({"open": o, "high": h, "low": l, "close": c})
            else:
                seq = list(row)
                if len(seq) == 5:
                    _, o, h, l, c = seq
                elif len(seq) == 4:
                    o, h, l, c = seq
                else:
                    continue
                fixed.append({
                    "open": float(o), "high": float(h),
                    "low": float(l), "close": float(c)
                })
        except Exception:
            continue
    return fixed

def _safe_score(ohlc) -> float:
    """
    Chama score_signal e tolera diferentes formatos de retorno:
      - float 0..1
      - tuple (score, ...)
      - dict {"score": 0..1, ...}
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
        print(f"[IND] erro em score_signal: {e}")
        s = 0.0
    if s > 1.0:
        s = s / 100.0
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

# ==============================
# Pipeline principal
# ==============================
def run_pipeline():
    print("▶️ Runner iniciado. Intervalo = {} min.".format(float(os.getenv("RUN_INTERVAL_MIN", "20.0"))))
    print("🧩 Coletando PREÇOS / OHLC…")

    # 1) escolhe universo
    if SYMBOLS:
        universe = SYMBOLS[:]  # lista fixa via env
    else:
        universe = fetch_top_symbols(TOP_SYMBOLS)  # dinâmica no CG

    # 2) rotaciona para este ciclo
    selected = _rotate(universe, SELECT_PER_CYCLE)
    print(f"🧪 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    # 3) coleta OHLC
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

    # 4) salva debug bruto (como veio do fetcher)
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_symbols, "data": collected}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # 5) pontua e gera sinais
    saved_count = 0
    for sym in ok_symbols:
        raw_ohlc = collected.get(sym)
        ohlc = _as_dict_candles(raw_ohlc)  # <<< padroniza para dict

        if not ohlc:
            print(f"[IND] {sym}: OHLC vazio após normalização.")
            continue

        # (novo) snapshot de histórico
        if HISTORY_ENABLED:
            try:
                snap = ohlc[-HISTORY_MAX_CANDLES:] if HISTORY_MAX_CANDLES > 0 else ohlc
                log_snapshot(sym, snap, {"ts": _ts(), "days": DAYS_OHLC})
            except Exception as e:
                print(f"[HIST] falha snapshot {sym}: {e}")

        # score técnico
        score = _safe_score(ohlc)

        # sentimento (aceita float ou (float, n))
        sent_val = 0.0
        sent_n = None
        try:
            sres = get_sentiment_score(sym)
            if isinstance(sres, tuple) and len(sres) >= 1:
                sent_val = float(sres[0])
                if len(sres) >= 2:
                    sent_n = sres[1]
            else:
                sent_val = float(sres)
        except Exception:
            sent_val = 0.0

        mixed = _mix_confidence(score, sent_val)

        # logs detalhados
        sent_pct = round(((sent_val + 1.0) / 2.0) * 100.0, 1)  # mostra o sentimento em 0..100
        tech_pct = round(score * 100.0, 1)
        mix_pct  = round(mixed * 100.0, 1)
        n_str = f"(n={sent_n})" if sent_n is not None else "(n=?)"
        print(f"📊 {sym} | Técnico: {tech_pct}% | Sentimento: {sent_pct}% {n_str} | "
              f"Mix(T:{WEIGHT_TECH},S:{WEIGHT_SENT}): {mix_pct}% (min {int(MIN_CONFIDENCE*100)}%)")

        # (novo) trilha de scores no histórico
        if HISTORY_ENABLED:
            try:
                log_score({
                    "ts": _ts(),
                    "symbol": sym,
                    "score_tech": float(score),
                    "sentiment": float(sent_val),
                    "mixed": float(mixed),
                    "weights": {"tech": WEIGHT_TECH, "sent": WEIGHT_SENT},
                })
            except Exception as e:
                print(f"[HIST] falha log_score {sym}: {e}")

        # filtros de limiar
        if score < SCORE_THRESHOLD:
            continue
        if mixed < MIN_CONFIDENCE:
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

        # completa payload
        sig["symbol"]     = sym
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["confidence"] = float(mixed)
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
                "confidence_score": round(mixed * 100, 2),
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
