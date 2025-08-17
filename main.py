# -*- coding: utf-8 -*-
"""
main.py — pipeline principal (auto-train + auto-load IA)

- Coleta OHLC (CoinGecko)
- Calcula score técnico (indicators) e sentimento (notícias)
- (Novo) Carrega modelo IA, mistura no score final e usa no filtro (opcional)
- (Novo) Salva histórico (candles/scores) para treino
- (Novo) Treina IA automaticamente a cada N ciclos quando houver dados suficientes
- Evita duplicados e envia ao Telegram
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# --- Projeto ---
from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal
from positions_manager import should_send_and_register
from notifier_telegram import send_signal_notification
from signal_generator import append_signal

# -------- Sentimento (opcional) --------
try:
    from sentiment_analyzer import get_sentiment_score  # [-1..1] ou float
    NEWS_ACTIVE = True
except Exception:
    NEWS_ACTIVE = False
    def get_sentiment_score(symbol: str) -> float: return 0.0

# -------- Histórico (opcional) --------
SAVE_HISTORY = os.getenv("SAVE_HISTORY", "False").lower() == "true"
HISTORY_DIR  = os.getenv("HISTORY_DIR", "data/history")
try:
    import history_manager as hist
    HAVE_HISTORY = True
except Exception:
    hist = None  # type: ignore
    HAVE_HISTORY = False

# -------- IA / Modelo (auto-load/auto-train) --------
USE_AI            = os.getenv("USE_AI", "true").lower() == "true"
WEIGHT_TECH       = float(os.getenv("WEIGHT_TECH", "1.5"))
WEIGHT_SENT       = float(os.getenv("WEIGHT_SENT", "1.0"))
WEIGHT_AI         = float(os.getenv("WEIGHT_AI",   "1.0"))
AI_THRESHOLD      = float(os.getenv("AI_THRESHOLD", "0.60"))  # informativo

TRAINING_ENABLED   = os.getenv("TRAINING_ENABLED", "true").lower() == "true"
TRAIN_MIN_SAMPLES  = int(os.getenv("TRAIN_MIN_SAMPLES", "200"))
TRAIN_EVERY_CYCLES = int(os.getenv("TRAIN_EVERY_CYCLES", "12"))
MODEL_FILE         = os.getenv("MODEL_FILE", "model.pkl")

_ai_predict = None
_ai_loaded  = False
def _try_load_model():
    """Tenta carregar um preditor de IA. 2 modos:
       1) ai_predictor.predict_score(ohlc_dicts)->0..1
       2) model_manager.predict_proba(features_dict)->0..1 (não usado aqui por padrão)"""
    global _ai_predict, _ai_loaded
    if not USE_AI:
        _ai_predict = None; _ai_loaded = False; return
    # prioridade: ai_predictor por OHLC
    try:
        from ai_predictor import predict_score as _predict_from_ohlc  # type: ignore
        _ai_predict = ("ohlc", _predict_from_ohlc)
        _ai_loaded  = True
        print("🤖 IA carregada (modo=ohlc).")
        return
    except Exception:
        pass
    # alternativa: model_manager (features)
    try:
        from model_manager import predict_proba as _predict_from_features  # type: ignore
        _ai_predict = ("features", _predict_from_features)
        _ai_loaded  = True
        print("🤖 IA carregada (modo=features).")
        return
    except Exception:
        _ai_predict = None
        _ai_loaded  = False
        print("ℹ️ IA não carregada (sem preditor disponível).")

def _predict_ai(ohlc_dicts, features_for_future=None) -> Optional[float]:
    """Retorna prob 0..1 da IA se preditor existir."""
    if not USE_AI or not _ai_loaded or _ai_predict is None:
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

# treino automático
try:
    from trainer import train_and_save  # type: ignore
    HAVE_TRAINER = True
except Exception:
    HAVE_TRAINER = False

# -------- Config geral --------
SYMBOLS          = [s for s in os.getenv("SYMBOLS","").replace(" ","").split(",") if s]
TOP_SYMBOLS      = int(os.getenv("TOP_SYMBOLS", "100"))
SELECT_PER_CYCLE = int(os.getenv("SELECT_PER_CYCLE", "8"))

DAYS_OHLC        = int(os.getenv("DAYS_OHLC", "30"))
MIN_BARS         = int(os.getenv("MIN_BARS", "180"))

SCORE_THRESHOLD  = float(os.getenv("SCORE_THRESHOLD", "0.70"))
MIN_CONFIDENCE   = float(os.getenv("MIN_CONFIDENCE", "0.60"))

COOLDOWN_HOURS       = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

DATA_RAW_FILE = os.getenv("DATA_RAW_FILE", "data_raw.json")
CURSOR_FILE   = os.getenv("CURSOR_FILE", "scan_state.json")
SIGNALS_FILE  = os.getenv("SIGNALS_FILE", "signals.json")

# -------- Helpers --------
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
    if take <= 0 or not symbols:
        return symbols
    st = _ensure_cursor()
    off = st.get("offset", 0) % len(symbols)
    batch = []
    for i in range(min(take, len(symbols))):
        batch.append(symbols[(off + i) % len(symbols)])
    st["offset"] = (off + take) % len(symbols)
    st["cycle"]  = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _as_dict_candles(ohlc_raw):
    fixed = []
    for row in (ohlc_raw or []):
        try:
            if isinstance(row, dict):
                o = float(row.get("open")); h = float(row.get("high"))
                l = float(row.get("low"));  c = float(row.get("close"))
            else:
                seq = list(row)
                if len(seq) == 5: _, o, h, l, c = seq
                elif len(seq) == 4: o, h, l, c = seq
                else: continue
            fixed.append({"open": o, "high": h, "low": l, "close": c})
        except Exception:
            continue
    return fixed

def _safe_score(ohlc) -> Tuple[float, Optional[Dict[str, Any]]]:
    inds = None
    try:
        res = score_signal(ohlc)
        if isinstance(res, tuple):
            s = float(res[0]); 
            if len(res) > 1 and isinstance(res[1], dict): inds = res[1]
        elif isinstance(res, dict):
            s = float(res.get("score", res.get("value", res.get("confidence", res.get("prob", 0.0)))))
            for k in ("indicators","inds","debug","features"):
                if isinstance(res.get(k), dict): inds = res.get(k); break
        else:
            s = float(res)
    except Exception as e:
        print(f"[IND] erro score_signal: {e}"); s = 0.0
    if s > 1.0: s /= 100.0
    return max(0.0, min(1.0, s)), inds

def _mix_conf(tech: float, sent: float, ai: Optional[float]) -> float:
    sent01 = (sent + 1.0) / 2.0
    wT, wS, wA = WEIGHT_TECH, WEIGHT_SENT, (WEIGHT_AI if ai is not None else 0.0)
    total = max(1e-9, wT + wS + wA)
    acc   = (wT*tech) + (wS*sent01) + (wA*(ai if ai is not None else 0.0))
    return max(0.0, min(1.0, acc/total))

def _log_inds(sym: str, inds: Optional[Dict[str, Any]], score: float) -> None:
    if not isinstance(inds, dict):
        print(f"[IND] {sym} sem dict de indicadores | score={round(score*100,1)}%"); return
    keys = ("close","rsi","macd","hist","ema20","ema50","bb_mid","bb_hi","stochK","stochD",
            "adx","pdi","mdi","atr_rel","cci","kijun","obv_slope","mfi","willr")
    parts = [f"{k}={inds[k]}" for k in keys if k in inds]
    print(f"[IND] {sym} | " + " | ".join(parts) + f" | score={round(score*100,1)}%")

def _save_history(payload: Dict[str, Any]) -> None:
    if not SAVE_HISTORY: return
    if not HAVE_HISTORY or hist is None:
        print("ℹ️ SAVE_HISTORY=true mas history_manager indisponível."); return
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        if hasattr(hist,"save_snapshot"):
            hist.save_snapshot(payload, base_dir=HISTORY_DIR)  # type: ignore
        elif hasattr(hist,"append_row"):
            hist.append_row(payload, base_dir=HISTORY_DIR)     # type: ignore
        elif hasattr(hist,"save"):
            hist.save(payload, base_dir=HISTORY_DIR)           # type: ignore
        else:
            sym = payload.get("symbol","UNKNOWN")
            with open(os.path.join(HISTORY_DIR, f"{sym}.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False)+"\n")
    except Exception as e:
        print(f"⚠️ Falha ao salvar histórico: {e}")

# -------- Pipeline --------
def run_pipeline():
    run_iv = float(os.getenv("RUN_INTERVAL_MIN","20.0"))
    print(f"▶️ Runner iniciado. Intervalo = {run_iv:.1f} min.")
    print(f"🔎 NEWS ativo?: {NEWS_ACTIVE} | IA ativa?: {USE_AI} | Histórico ativado?: {SAVE_HISTORY}")

    # IA: tenta carregar preditor (uma vez por processo)
    global _ai_loaded
    if not _ai_loaded: _try_load_model()

    # 1) universo
    universe = SYMBOLS[:] if SYMBOLS else fetch_top_symbols(TOP_SYMBOLS)
    # 2) rotação
    selected = _rotate(universe, SELECT_PER_CYCLE)
    print(f"🧪 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    # 3) coleta
    collected, ok_symbols = {}, []
    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)
            if not raw or len(raw) < MIN_BARS:
                print(f"❌ Dados insuficientes para {sym}"); continue
            collected[sym] = raw; ok_symbols.append(sym)
            print(f"   → OK | candles={len(raw)}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_symbols:
        print("❌ Nenhum ativo com OHLC suficiente."); return

    # 4) dump debug
    try:
        with open(DATA_RAW_FILE,"w",encoding="utf-8") as f:
            json.dump({"symbols": ok_symbols, "data": collected}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # 5) scoring + sinais
    saved = 0
    for sym in ok_symbols:
        raw_ohlc = collected[sym]
        ohlc = _as_dict_candles(raw_ohlc)

        tech, inds = _safe_score(ohlc)
        _log_inds(sym, inds, tech)

        # sentimento
        try:
            sres = get_sentiment_score(sym)
            sent = float(sres[0] if isinstance(sres, tuple) else sres)
        except Exception:
            sent = 0.0
        sent01_pct = round(((sent+1)/2)*100, 1)

        # IA predição
        ai = _predict_ai(ohlc_dicts=ohlc, features_for_future=None)
        ai_pct = (round(ai*100,1) if ai is not None else None)

        # mix
        mixed = _mix_conf(tech, sent, ai)
        print(f"📊 {sym} | Técnico: {round(tech*100,1)}% | Sentimento: {sent01_pct}%"
              + (f" | IA: {ai_pct}%" if ai_pct is not None else "")
              + f" | Mix(T:{WEIGHT_TECH},S:{WEIGHT_SENT},AI:{WEIGHT_AI}): {round(mixed*100,1)}% (min {int(MIN_CONFIDENCE*100)}%)")

        # histórico
        _save_history({
            "ts": _ts(),
            "symbol": sym,
            "days": DAYS_OHLC,
            "bars": len(ohlc),
            "indicators": inds or {},
            "scores": {
                "technical": tech,
                "sentiment_raw": sent,
                "sentiment_0_1": (sent+1)/2,
                "ai": (ai if ai is not None else None),
                "mixed": mixed,
            },
            "weights": {"tech": WEIGHT_TECH, "sent": WEIGHT_SENT, "ai": WEIGHT_AI},
            "thresholds": {"score_min": SCORE_THRESHOLD, "confidence_min": MIN_CONFIDENCE},
        })

        # filtros
        if tech < SCORE_THRESHOLD or mixed < MIN_CONFIDENCE:
            continue

        # gera sinal
        try:
            sig = generate_signal(ohlc)
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}"); continue
        if not isinstance(sig, dict):
            print(f"⚠️ {sym}: generate_signal não retornou dict."); continue

        sig["symbol"]     = sym
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["confidence"] = float(mixed)
        sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB" + ("+AI" if ai is not None else ""))
        sig["created_at"] = sig.get("created_at", _ts())
        if "id" not in sig: sig["id"] = f"{sym}-{int(time.time())}"

        ok_to_send, reason = should_send_and_register(
            {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
            cooldown_hours=COOLDOWN_HOURS, change_threshold_pct=CHANGE_THRESHOLD_PCT
        )
        if not ok_to_send:
            print(f"🟡 {sym} não enviado ({reason})."); continue

        pushed = False
        try:
            pushed = send_signal_notification({
                "symbol": sym,
                "entry_price": sig.get("entry"),
                "target_price": sig.get("tp"),
                "stop_loss": sig.get("sl"),
                "risk_reward": sig.get("rr", 2.0),
                "confidence_score": round(mixed*100, 2),
                "strategy": sig.get("strategy"),
                "created_at": sig.get("created_at"),
                "id": sig.get("id"),
            })
        except Exception as e:
            print(f"⚠️ Falha no envio (notifier): {e}")
        print("✅ Notificação enviada." if pushed else "❌ Falha no envio (ver notifier_telegram).")

        try:
            append_signal(sig); saved += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar em {SIGNALS_FILE}: {e}")

    print(f"🗂 {saved} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {_ts()}")

    # 6) Treino automático (no final do ciclo)
    if TRAINING_ENABLED and HAVE_TRAINER and HAVE_HISTORY:
        st = _ensure_cursor()
        cyc = int(st.get("cycle", 0))
        last_train = int(st.get("last_train_cycle", -1))
        if cyc - last_train >= TRAIN_EVERY_CYCLES:
            try:
                print(f"🤖 Treino automático: ciclo {cyc} (último={last_train}) | min_amostras={TRAIN_MIN_SAMPLES}")
                ok = train_and_save(min_samples=TRAIN_MIN_SAMPLES, model_path=MODEL_FILE)
                print(f"✅ Treino IA: {ok}")
                st["last_train_cycle"] = cyc
                _save_cursor(st)
                # re-carrega o preditor (caso modelo novo)
                _try_load_model()
            except Exception as e:
                print(f"[AI] falha treino automático: {e}")

if __name__ == "__main__":
    run_pipeline()
