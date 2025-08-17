# -*- coding: utf-8 -*-
"""
main.py — pipeline principal (com IA + histórico opcionais)

- Seleciona o conjunto de moedas (dinâmico via CoinGecko ou fixo via env)
- Coleta OHLC e normaliza (open/high/low/close)
- Calcula score técnico
- (Opcional) mistura com sentimento (NewsData.io ou sua fonte)
- (Novo) mistura com IA se houver modelo carregado
- Gera sinal (entry/tp/sl) quando houver
- Evita duplicados via positions_manager
- Envia para o Telegram e grava em signals.json
- (Novo) Salva snapshots e trilha de scores via history_manager
- (Novo) Avalia e fecha sinais antigos automaticamente (auto-rotulagem)
- (Novo) Treina IA automaticamente se habilitado e houver dados suficientes
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List

# ---- Módulos do projeto ----
from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register
from signal_generator import append_signal  # salva no signals.json

# ==============================
# Histórico (opcional; no-op se ausente)
# ==============================
HISTORY_ENABLED = os.getenv("HISTORY_ENABLED", "true").lower() == "true"
HISTORY_MAX_CANDLES = int(os.getenv("HISTORY_MAX_CANDLES", "200"))
try:
    # recomenda-se implementar no seu history_manager:
    #   log_snapshot(symbol, candles_dicts, meta)
    #   log_score(row_dict)
    #   record_signal(signal_dict_com_features_e_result_placeholder)
    #   evaluate_pending_outcomes()
    from history_manager import log_snapshot, log_score, record_signal, evaluate_pending_outcomes  # type: ignore
    HAVE_HISTORY = True
except Exception:
    def log_snapshot(symbol: str, candles, meta): pass
    def log_score(row: Dict[str, Any]): pass
    def record_signal(sig: Dict[str, Any]): pass
    def evaluate_pending_outcomes(): return {"evaluated": 0, "closed": 0}
    HAVE_HISTORY = False

# ==============================
# Sentimento (opcional)
# ==============================
try:
    from sentiment_analyzer import get_sentiment_score  # pode retornar float ou (float, n)
    SENT_OK = True
except Exception:
    def get_sentiment_score(symbol: str): return 0.0
    SENT_OK = False

# ==============================
# IA (opcional; tolerante a ausências)
# ==============================
USE_AI = os.getenv("USE_AI", "true").lower() == "true"
AI_THRESHOLD = float(os.getenv("AI_THRESHOLD", "0.60"))  # se quiser usar um corte mínimo só da IA
WEIGHT_TECH = float(os.getenv("WEIGHT_TECH", "1.5"))
WEIGHT_SENT = float(os.getenv("WEIGHT_SENT", "1.0"))
WEIGHT_AI   = float(os.getenv("WEIGHT_AI",   "1.0"))     # <<< novo peso IA no mix

# tentativa 1: ai_predictor.predict_score(ohlc_dicts) -> 0..1
# tentativa 2: model_manager.predict_proba(features_dict) -> 0..1 (você pode mudar p/ usar features depois)
_ai_predict = None
try:
    from ai_predictor import predict_score as _predict_from_ohlc  # type: ignore
    _ai_predict = ("ohlc", _predict_from_ohlc)
except Exception:
    try:
        from model_manager import predict_proba as _predict_from_features  # type: ignore
        _ai_predict = ("features", _predict_from_features)
    except Exception:
        _ai_predict = None

# treino automático (opcional)
TRAINING_ENABLED   = os.getenv("TRAINING_ENABLED", "true").lower() == "true"
TRAIN_MIN_SAMPLES  = int(os.getenv("TRAIN_MIN_SAMPLES", "200"))
TRAIN_EVERY_CYCLES = int(os.getenv("TRAIN_EVERY_CYCLES", "12"))  # a cada N ciclos tenta treinar
try:
    from trainer import train_and_save  # type: ignore
    HAVE_TRAINER = True
except Exception:
    HAVE_TRAINER = False

# ==============================
# Config via Environment (geral)
# ==============================
SYMBOLS = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]  # lista fixa?
TOP_SYMBOLS       = int(os.getenv("TOP_SYMBOLS", "100"))           # universo quando dinâmico
SELECT_PER_CYCLE  = int(os.getenv("SELECT_PER_CYCLE", "8"))        # quantas moedas por ciclo
DAYS_OHLC         = int(os.getenv("DAYS_OHLC", "14"))              # janela em dias no CoinGecko
MIN_BARS          = int(os.getenv("MIN_BARS", "84"))               # mínimo de candles aceitos

SCORE_THRESHOLD   = float(os.getenv("SCORE_THRESHOLD", "0.70"))    # limiar técnico (0..1)
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.60"))     # limiar confiança final (0..1)

# anti-duplicados
COOLDOWN_HOURS        = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT  = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

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
        return {"offset": 0, "cycle": 0, "last_train_cycle": -1}

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
    # avança o offset e ciclo
    st["offset"] = (off + take) % len(symbols)
    st["cycle"] = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _as_dict_candles(ohlc_raw):
    """
    Normaliza OHLC:
      aceita [ts, o, h, l, c], (ts, o, h, l, c), [o,h,l,c] ou dict {open,high,low,close}
      retorna: lista de dicts {'open','high','low','close'}
    """
    fixed = []
    for row in (ohlc_raw or []):
        try:
            if isinstance(row, dict):
                o = float(row.get("open")); h = float(row.get("high"))
                l = float(row.get("low"));  c = float(row.get("close"))
            else:
                seq = list(row)
                if len(seq) == 5:
                    _, o, h, l, c = seq
                elif len(seq) == 4:
                    o, h, l, c = seq
                else:
                    continue
            fixed.append({"open": float(o), "high": float(h), "low": float(l), "close": float(c)})
        except Exception:
            continue
    return fixed

def _safe_score(ohlc) -> float:
    """
    Aceita retorno de score_signal como:
      - float 0..1
      - tuple (score, ...)
      - dict {"score": 0..1, "value":..., "confidence":..., "prob":...}
    """
    try:
        res = score_signal(ohlc)
        if isinstance(res, tuple):
            s = float(res[0])
        elif isinstance(res, dict):
            s = float(res.get("score", res.get("value", res.get("confidence", res.get("prob", 0.0)))))
        else:
            s = float(res)
    except Exception as e:
        print(f"[IND] erro em score_signal: {e}")
        s = 0.0
    if s > 1.0:
        s = s / 100.0
    return max(0.0, min(1.0, round(s, 6)))

def _mix_confidence(score_tech: float, sent: float, ai: float | None) -> float:
    """
    Junta técnico (0..1), sentimento (-1..1) e IA (0..1) => (0..1).
    WEIGHT_SENT = 0 ignora sentimento; WEIGHT_AI = 0 ignora IA.
    """
    sent01 = (sent + 1.0) / 2.0  # -1..1 -> 0..1
    wT, wS, wA = WEIGHT_TECH, WEIGHT_SENT, WEIGHT_AI
    total = max(1e-9, wT + wS + (wA if ai is not None else 0.0))
    acc = (wT * score_tech) + (wS * sent01) + ((wA * ai) if ai is not None else 0.0)
    mixed = acc / total
    return max(0.0, min(1.0, mixed))

def _predict_ai(ohlc_dicts, features_for_future=None) -> float | None:
    """
    Retorna probabilidade 0..1 da IA, se houver preditor disponível.
    - modo "ohlc": ai_predictor.predict_score(ohlc_dicts)
    - modo "features": model_manager.predict_proba(features_for_future)
    """
    if not USE_AI or _ai_predict is None:
        return None
    mode, fn = _ai_predict
    try:
        if mode == "ohlc":
            return float(fn(ohlc_dicts))
        elif mode == "features" and features_for_future is not None:
            return float(fn(features_for_future))
    except Exception as e:
        print(f"[AI] falha predição: {e}")
    return None

# ==============================
# Pipeline principal
# ==============================
def run_pipeline():
    run_interval = float(os.getenv("RUN_INTERVAL_MIN", "20.0"))
    print(f"▶️ Runner iniciado. Intervalo = {run_interval} min.")
    print("🔎 NEWS ativo?:", SENT_OK, "| IA ativa?:", USE_AI, "| Histórico ativado?:", HISTORY_ENABLED and HAVE_HISTORY)

    # 0) avaliar e fechar sinais antigos (auto-rotulagem)
    if HAVE_HISTORY and HISTORY_ENABLED:
        try:
            res = evaluate_pending_outcomes()
            print(f"📘 history: avaliados={res.get('evaluated', 0)}, fechados={res.get('closed', 0)}")
        except Exception as e:
            print(f"[HIST] falha evaluate_pending_outcomes: {e}")

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

    # 4) salva debug bruto
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
        ohlc = _as_dict_candles(raw_ohlc)
        if not ohlc:
            print(f"[IND] {sym}: OHLC vazio após normalização.")
            continue

        # snapshot (histórico)
        if HISTORY_ENABLED:
            try:
                snap = ohlc[-HISTORY_MAX_CANDLES:] if HISTORY_MAX_CANDLES > 0 else ohlc
                log_snapshot(sym, snap, {"ts": _ts(), "days": DAYS_OHLC})
            except Exception as e:
                print(f"[HIST] falha snapshot {sym}: {e}")

        # score técnico
        score = _safe_score(ohlc)

        # sentimento
        sent_val = 0.0; sent_n = None
        try:
            sres = get_sentiment_score(sym)
            if isinstance(sres, tuple) and len(sres) >= 1:
                sent_val = float(sres[0])
                if len(sres) >= 2: sent_n = sres[1]
            else:
                sent_val = float(sres)
        except Exception:
            sent_val = 0.0

        # IA (predição)
        ai_val = _predict_ai(ohlc_dicts=ohlc, features_for_future=None)  # se for usar features, forneça aqui
        # mix
        mixed = _mix_confidence(score, sent_val, ai_val)

        # logs
        sent_pct = round(((sent_val + 1.0) / 2.0) * 100.0, 1)
        tech_pct = round(score * 100.0, 1)
        ai_pct   = (round(ai_val * 100.0, 1) if ai_val is not None else None)
        mix_pct  = round(mixed * 100.0, 1)
        n_str = f"(n={sent_n})" if sent_n is not None else "(n=?)"
        ai_str = f" | IA: {ai_pct}%" if ai_pct is not None else ""
        print(f"📊 {sym} | Técnico: {tech_pct}% | Sentimento: {sent_pct}% {n_str}{ai_str} | "
              f"Mix(T:{WEIGHT_TECH},S:{WEIGHT_SENT},AI:{WEIGHT_AI}): {mix_pct}% (min {int(MIN_CONFIDENCE*100)}%)")

        # trilha de scores (histórico)
        if HISTORY_ENABLED:
            try:
                log_score({
                    "ts": _ts(),
                    "symbol": sym,
                    "score_tech": float(score),
                    "sentiment": float(sent_val),
                    "ai": (float(ai_val) if ai_val is not None else None),
                    "mixed": float(mixed),
                    "weights": {"tech": WEIGHT_TECH, "sent": WEIGHT_SENT, "ai": WEIGHT_AI},
                })
            except Exception as e:
                print(f"[HIST] falha log_score {sym}: {e}")

        # filtros
        if score < SCORE_THRESHOLD:
            continue
        if mixed < MIN_CONFIDENCE:
            continue
        if ai_val is not None and ai_val < AI_THRESHOLD:
            # se quiser que a IA seja obrigatória, mantenha esse gate; senão, remova
            pass  # não bloqueio por padrão — comente/desc omente se for usar hard-gate de IA

        # gera plano (entry/tp/sl)
        try:
            sig = generate_signal(ohlc)
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")
            sig = None

        if not sig or not isinstance(sig, dict):
            print(f"⚠️ {sym}: sem sinal técnico.")
            continue

        sig["symbol"]     = sym
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["confidence"] = float(mixed)
        sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB" + ("+AI" if ai_val is not None else ""))
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
                "strategy": sig.get("strategy"),
                "created_at": sig.get("created_at"),
                "id": sig.get("id"),
            })
        except Exception as e:
            print(f"⚠️ Falha no envio (notifier): {e}")

        if pushed:
            print("✅ Notificação enviada.")
        else:
            print("❌ Falha no envio (ver notifier_telegram).")

        # registra em signals.json
        try:
            append_signal(sig)
        except Exception as e:
            print(f"⚠️ Erro ao salvar em {SIGNALS_FILE}: {e}")

        # registra no histórico (completo) para treino futuro
        if HAVE_HISTORY and HISTORY_ENABLED:
            try:
                record_signal({
                    **sig,
                    "scores": {
                        "tech": float(score),
                        "sent": float(sent_val),
                        "ai": (float(ai_val) if ai_val is not None else None),
                        "mixed": float(mixed),
                    },
                    # se o seu history_manager espera "features", você pode
                    # calcular e anexar aqui; no mínimo salvamos OHLC recente
                    "context": {
                        "ohlc_tail": ohlc[-60:],  # amostra para referência
                        "days": DAYS_OHLC,
                    },
                })
            except Exception as e:
                print(f"[HIST] falha record_signal {sym}: {e}")

        # contagem
        saved_count += 1

    print(f"🗂 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {_ts()}")

    # 6) treino automático da IA (no final do ciclo)
    if TRAINING_ENABLED and HAVE_TRAINER and HAVE_HISTORY:
        st = _ensure_cursor()
        cyc = int(st.get("cycle", 0))
        last_train = int(st.get("last_train_cycle", -1))
        if cyc - last_train >= TRAIN_EVERY_CYCLES:
            try:
                print(f"🤖 Treino automático: ciclo {cyc} (último={last_train}) | min_amostras={TRAIN_MIN_SAMPLES}")
                ok = train_and_save(min_samples=TRAIN_MIN_SAMPLES)
                print(f"✅ Treino IA: {ok}")
                st["last_train_cycle"] = cyc
                _save_cursor(st)
            except Exception as e:
                print(f"[AI] falha treino automático: {e}")


if __name__ == "__main__":
    run_pipeline()
