# -*- coding: utf-8 -*-
import os, json, time
from datetime import datetime

from config import (
    MIN_CONFIDENCE, DEBUG_SCORE, TOP_SYMBOLS,
    BATCH_OHLC, BATCH_PAUSE_SEC, SYMBOLS,
    DATA_RAW_FILE, SIGNALS_FILE, OHLC_DAYS,
    COOLDOWN_HOURS, CHANGE_THRESHOLD_PCT,
    USE_AI, AI_THRESHOLD,
    USE_NEWS, NEWS_WEIGHT, NEWS_MAX_BOOST, NEWS_MAX_PEN, NEWS_VETO_NEG,
    DATA_SOURCE
)
from coingecko_client import fetch_bulk_prices, fetch_ohlc, SYMBOL_TO_ID
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register

# IA (técnica)
from indicators import rsi, macd, ema, bollinger
from ai_predictor import load_model, predict_proba, log_if_active

# Sentimento (opcional)
try:
    import sentiment_analyzer as SA
except Exception:
    SA = None

# Volume/CCXT (opcional)
if os.getenv("USE_VOLUME_INDICATORS","false").lower()=="true" and DATA_SOURCE == "ccxt":
    from ccxt_client import fetch_ohlcv_binance as fetch_ohlc_with_volume
else:
    fetch_ohlc_with_volume = None

def log(msg): print(msg, flush=True)
def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def build_features(closes):
    if not closes or len(closes) < 60:
        return None, None
    R = rsi(closes, 14); M, S, H = macd(closes, 12, 26, 9)
    E20 = ema(closes, 20); E50 = ema(closes, 50)
    # BB não entra como feature aqui — usamos na score
    i = len(closes) - 1
    vals = [R[i], M[i], S[i], H[i], E20[i], E50[i]]
    if any(v is None for v in vals): return None, None
    sc = score_signal(closes)
    sc_val = sc[0] if isinstance(sc, tuple) else sc
    if sc_val is None: return None, None
    feats = [float(v) for v in vals] + [float(sc_val)]
    return feats, sc_val

def run_pipeline():
    # IA técnica
    model = load_model() if USE_AI else None
    if model is not None:
        log_if_active(AI_THRESHOLD)

    # Seleção por variação 24h
    log("🧩 Coletando PREÇOS em lote (bulk)…")
    bulk = fetch_bulk_prices(SYMBOLS)

    ranked = []
    for s in SYMBOLS:
        info = bulk.get(s)
        if not info: continue
        ranked.append((s, abs(float(info.get("usd_24h_change", 0.0)))))
    ranked.sort(key=lambda t: t[1], reverse=True)
    selected = [sym for sym, _ in ranked[:max(1, int(TOP_SYMBOLS))]]
    log(f"✅ Selecionados para OHLC: {', '.join(selected)}")

    # OHLC / coleta por blocos
    all_data = []
    for idx, block in enumerate(chunks(selected, max(1, int(BATCH_OHLC)))):
        if idx > 0:
            log(f"⏸️ Pausa de {BATCH_PAUSE_SEC}s entre blocos…"); time.sleep(BATCH_PAUSE_SEC)
        for s in block:
            if fetch_ohlc_with_volume:
                log(f"📊 (ccxt) Coletando OHLCV {s} (days={OHLC_DAYS})…")
                try:
                    data = fetch_ohlc_with_volume(symbol=s, days=OHLC_DAYS, timeframe="1h")
                    candles = []
                    for d in data:
                        candles.append({
                            "timestamp": int(datetime.strptime(d["timestamp"], "%Y-%m-%d %H:%M:%S UTC").timestamp()),
                            "open": d["open"], "high": d["high"], "low": d["low"], "close": d["close"], "volume": d["volume"]
                        })
                except Exception as e:
                    log(f"   → ❌ {s} erro ccxt: {e}")
                    continue
            else:
                cid = SYMBOL_TO_ID.get(s, s.replace("USDT","").lower())
                log(f"📊 Coletando OHLC {s} (days={OHLC_DAYS})…")
                data = fetch_ohlc(cid, days=OHLC_DAYS)
                if not data:
                    log(f"   → ❌ Dados insuficientes para {s}"); continue
                candles = [{"timestamp": int(ts/1000), "open": float(o), "high": float(h),
                            "low": float(l), "close": float(c)} for ts,o,h,l,c in data]

            all_data.append({"symbol": s, "ohlc": candles})
            log(f"   → OK | candles={len(candles)}")

    with open(DATA_RAW_FILE, "w") as f: json.dump(all_data, f, indent=2)
    log(f"💾 Salvo {DATA_RAW_FILE} ({len(all_data)} ativos)")

    thr = MIN_CONFIDENCE if MIN_CONFIDENCE <= 1 else MIN_CONFIDENCE/100.0
    approved = []

    for item in all_data:
        s = item["symbol"]; ohlc = item["ohlc"]
        closes = [c["close"] for c in ohlc]

        # 1) Camada técnica base/extra
        sig = generate_signal(s, ohlc)
        if not sig:
            if DEBUG_SCORE:
                sc = score_signal(closes)
                shown = "None" if sc is None else f"{round((sc[0] if isinstance(sc, tuple) else sc)*100,1)}%"
                log(f"ℹ️ Score {s}: {shown} (min {int(thr*100)}%)")
            else:
                log(f"⛔ {s} descartado (<{int(thr*100)}%)")
            continue

        # 2) Sentimento (opcional) — usa seu sentiment_analyzer
        if USE_NEWS and SA is not None:
            try:
                senti = float(SA.get_sentiment_score(s))  # -1..1
                if senti <= NEWS_VETO_NEG:
                    log(f"⛔ {s} vetado por sentimento ({round(senti,2)})")
                    sig = None
                else:
                    old = sig["confidence"]
                    bonus = senti * NEWS_WEIGHT
                    if bonus > NEWS_MAX_BOOST: bonus = NEWS_MAX_BOOST
                    if bonus < -NEWS_MAX_PEN:  bonus = -NEWS_MAX_PEN
                    sig["confidence"] = max(0.0, min(1.0, old + bonus))
                    log(f"📰 {s} sentimento={round(senti,2)} bonus={round(bonus,3)} conf {round(old,3)}→{round(sig['confidence'],3)}")
            except Exception as e:
                log(f"⚠️ sentimento skip: {e}")

        if not sig:
            continue

        # 3) IA técnica (opcional)
        ai_ok, ai_proba = True, None
        if model is not None:
            feats, _sc = build_features(closes)
            if feats is not None:
                ai_proba = predict_proba(model, feats)
                if ai_proba is not None:
                    ai_ok = (ai_proba >= AI_THRESHOLD)

        # 4) Decisão final + anti-duplicado
        if sig["confidence"] >= thr and ai_ok:
            ok_to_send, why = should_send_and_register(
                sig,
                cooldown_hours=COOLDOWN_HOURS,
                change_threshold_pct=CHANGE_THRESHOLD_PCT
            )
            if not ok_to_send:
                log(f"⏭️ {s} pulado (duplicado: {why}).")
                continue

            approved.append(sig)
            proba_txt = f" | IA={round(ai_proba*100,1)}%" if ai_proba is not None else ""
            prefix = "🧠 [AI] " if ai_proba is not None else ""
            log(f"✅ {prefix}{s} aprovado ({int(sig['confidence']*100)}%{proba_txt})")

            wire = {
                "symbol": s,
                "entry_price": sig["entry"],
                "target_price": sig["tp"],
                "stop_loss": sig["sl"],
                "risk_reward": sig.get("risk_reward"),
                "confidence_score": round(sig["confidence"]*100, 2),
                "strategy": sig.get("strategy","RSI+MACD+EMA+BB") + ("+AI" if ai_proba is not None else ""),
                "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                "id": f"{s}-{int(time.time())}",
                "ai": True if ai_proba is not None else False,
                "ai_proba": round(ai_proba*100,1) if ai_proba is not None else None
            }
            send_signal_notification(wire)
        else:
            why = []
            if sig["confidence"] < thr: why.append("técnico")
            if model is not None and ai_proba is not None and not ai_ok: why.append(f"IA<{int(AI_THRESHOLD*100)}% ({round(ai_proba*100,1)}%)")
            log(f"⛔ {s} reprovado: {', '.join(why) if why else 'score baixo'}")

    with open(SIGNALS_FILE, "w") as f: json.dump(approved, f, indent=2)
    log(f"💾 {len(approved)} sinais salvos em {SIGNALS_FILE}")
    log(f"🕒 Fim: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

if __name__ == "__main__":
    run_pipeline()
