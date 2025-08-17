# -*- coding: utf-8 -*-
"""
main.py — pipeline principal

- Seleciona o conjunto de moedas (dinâmico via CoinGecko ou fixo via env)
- Coleta OHLC (com backoff no fetcher)
- Calcula indicadores/score técnico
- (Opcional) mistura com sentimento de notícias (NewsData)
- Gera sinal (entry/tp/sl) quando houver
- Evita duplicados via positions_manager
- Envia para o Telegram e grava em signals.json
- (Opcional) Salva histórico para treino da IA (SAVE_HISTORY=true)
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# ==== módulos do projeto ====
from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal  # score_signal pode retornar float/tuple/dict
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register
from signal_generator import append_signal  # salva no signals.json

# ---- Sentimento (opcional) ----
try:
    from sentiment_analyzer import get_sentiment_score  # [-1..1]
    NEWS_ACTIVE = True
except Exception:
    NEWS_ACTIVE = False
    def get_sentiment_score(symbol: str) -> float:
        return 0.0

# ---- Histórico (opcional) ----
HISTORY_ACTIVE = os.getenv("SAVE_HISTORY", "False").lower() == "true"
HISTORY_DIR = os.getenv("HISTORY_DIR", "data/history")
try:
    import history_manager as hist
    _HIST_OK = True
except Exception:
    _HIST_OK = False
    hist = None  # type: ignore

# ---- IA / Treinamento (somente flag informativa) ----
IA_ACTIVE = os.getenv("USE_AI", os.getenv("TRAINING_ENABLED", "False")).lower() == "true"

# ==============================
# Config via Environment
# ==============================
SYMBOLS = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]  # vazio = dinâmico
TOP_SYMBOLS       = int(os.getenv("TOP_SYMBOLS", "100"))          # quando dinâmico
SELECT_PER_CYCLE  = int(os.getenv("SELECT_PER_CYCLE", "8"))       # quantas moedas por ciclo

DAYS_OHLC         = int(os.getenv("DAYS_OHLC", "30"))
MIN_BARS          = int(os.getenv("MIN_BARS", "180"))

SCORE_THRESHOLD   = float(os.getenv("SCORE_THRESHOLD", "0.70"))   # limiar do score técnico (0..1)
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.60"))    # limiar confiança final (0..1)

# anti-duplicados
COOLDOWN_HOURS        = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT  = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

# mistura técnica + sentimento
WEIGHT_TECH = float(os.getenv("WEIGHT_TECH", "1.0"))
WEIGHT_SENT = float(os.getenv("WEIGHT_SENT", "0.0"))

# arquivos utilitários
DATA_RAW_FILE  = os.getenv("DATA_RAW_FILE", "data_raw.json")
CURSOR_FILE    = os.getenv("CURSOR_FILE", "scan_state.json")
SIGNALS_FILE   = os.getenv("SIGNALS_FILE", "signals.json")

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

def _safe_score(ohlc) -> Tuple[float, Optional[Dict[str, Any]]]:
    """
    Chama score_signal e tolera diferentes formatos de retorno.
    Aceita:
      - float
      - tuple (score, indicators_dict?)   -> indicadores no 2º elemento
      - dict {"score": 0..1, "indicators": {...}} (ou chaves similares)
    Retorna: (score_0_1, indicators_dict|None)
    """
    indicators = None
    try:
        res = score_signal(ohlc)
        if isinstance(res, tuple):
            raw_score = res[0]
            if len(res) > 1 and isinstance(res[1], dict):
                indicators = res[1]
        elif isinstance(res, dict):
            raw_score = res.get("score", res.get("value", res.get("confidence", res.get("prob", 0.0))))
            # tenta achar indicadores
            for k in ("indicators", "inds", "debug", "features"):
                if isinstance(res.get(k), dict):
                    indicators = res.get(k)
                    break
        else:
            raw_score = res

        s = float(raw_score)
    except Exception as e:
        print(f"[IND] erro em score_signal: {e}")
        s = 0.0

    if s > 1.0:
        s = s / 100.0
    s = max(0.0, min(1.0, s))
    return s, indicators

def _mix_confidence(score_tech: float, sent: float) -> float:
    """ Junta técnico (0..1) com sentimento (-1..1) => (0..1). """
    sent01 = (sent + 1.0) / 2.0
    total_w = max(1e-9, WEIGHT_TECH + WEIGHT_SENT)
    mixed = (WEIGHT_TECH * score_tech + WEIGHT_SENT * sent01) / total_w
    return max(0.0, min(1.0, mixed))

def _log_indicators(sym: str, inds: Optional[Dict[str, Any]], score: float) -> None:
    if not isinstance(inds, dict):
        # fallback de mensagem útil
        print(f"[IND] {sym} sem dict de indicadores | score={round(score*100,1)}%")
        return
    # seleciona alguns campos comuns, se existirem
    fields = []
    for k in ("close","rsi","macd","hist","ema20","ema50","bb_mid","bb_hi",
              "stochK","stochD","adx","pdi","mdi","atr_rel","cci",
              "ichiT","kijun","sa","obv_slope","mfi","willr"):
        v = inds.get(k, None)
        if v is not None:
            fields.append(f"{k}={v}")
    tail = " | ".join(fields)
    print(f"[IND] {sym} | {tail} | score={round(score*100,1)}%")

def _save_history_safe(payload: Dict[str, Any]) -> None:
    """Salva histórico se ativado e módulo disponível."""
    if not HISTORY_ACTIVE:
        return
    if not _HIST_OK or hist is None:
        print("ℹ️ SAVE_HISTORY=true mas history_manager não disponível; ignorando.")
        return
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        # Preferência por função 'save_snapshot' se existir, senão tenta 'append_row' ou 'save'
        if hasattr(hist, "save_snapshot"):
            hist.save_snapshot(payload, base_dir=HISTORY_DIR)  # type: ignore
        elif hasattr(hist, "append_row"):
            hist.append_row(payload, base_dir=HISTORY_DIR)     # type: ignore
        elif hasattr(hist, "save"):
            hist.save(payload, base_dir=HISTORY_DIR)           # type: ignore
        else:
            # dump simples por símbolo (não quebra se lib mudar)
            sym = payload.get("symbol", "UNKNOWN")
            path = os.path.join(HISTORY_DIR, f"{sym}.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ Falha ao salvar histórico: {e}")

# ==============================
# Pipeline principal
# ==============================
def run_pipeline():
    print("▶️ Runner iniciado. Intervalo = {:.1f} min.".format(float(os.getenv("RUN_INTERVAL_MIN", "20.0"))))
    print(f"🔎 NEWS ativo?: {NEWS_ACTIVE} | IA ativa?: {IA_ACTIVE} | Histórico ativado?: {HISTORY_ACTIVE}")

    collected: Dict[str, Any] = {}
    ok_symbols: List[str] = []

    # 1) universo
    if SYMBOLS:
        universe = SYMBOLS[:]
    else:
        universe = fetch_top_symbols(TOP_SYMBOLS)

    # 2) lote do ciclo
    selected = _rotate(universe, SELECT_PER_CYCLE)
    print(f"🧪 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    # 3) coleta OHLC
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

    # 4) salva dump de depuração
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

        # score técnico (+ indicadores, se vierem)
        score, inds = _safe_score(ohlc)
        _log_indicators(sym, inds, score)

        # sentimento
        sent = 0.0
        try:
            sent = float(get_sentiment_score(sym))
        except Exception:
            sent = 0.0
        # clamps
        if sent < -1.0: sent = -1.0
        if sent >  1.0: sent =  1.0

        # mistura
        conf = _mix_confidence(score, sent)

        # log amigável
        print(f"📊 {sym} | Técnico: {round(score*100,1)}% | Sentimento: {round((sent+1)*50,1)}% "
              f"(n=?) | Mix(T:{WEIGHT_TECH},S:{WEIGHT_SENT}): {round(conf*100,1)}% (min {int(MIN_CONFIDENCE*100)}%)")

        # salva histórico (se ativo)
        try:
            payload = {
                "ts": _ts(),
                "symbol": sym,
                "days": DAYS_OHLC,
                "bars": len(ohlc) if ohlc else 0,
                "indicators": inds or {},
                "scores": {
                    "technical": score,
                    "sentiment_raw": sent,      # -1..1
                    "sentiment_0_1": (sent+1)/2,
                    "confidence": conf,
                    "thresholds": {
                        "score_min": SCORE_THRESHOLD,
                        "confidence_min": MIN_CONFIDENCE,
                    },
                },
            }
            _save_history_safe(payload)
        except Exception as e:
            print(f"⚠️ Falha ao montar payload histórico: {e}")

        # checa thresholds para gerar sinal
        if score < SCORE_THRESHOLD or conf < MIN_CONFIDENCE:
            continue

        # gera sinal
        sig: Dict[str, Any]
        try:
            sig = generate_signal(ohlc)
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")
            continue

        if not isinstance(sig, dict):
            print(f"⚠️ {sym}: generate_signal não retornou dict.")
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

        # envia Telegram
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

        # salva no arquivo de sinais
        try:
            append_signal(sig)
            saved_count += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar em {SIGNALS_FILE}: {e}")

    print(f"🗂 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {_ts()}")


if __name__ == "__main__":
    run_pipeline()
