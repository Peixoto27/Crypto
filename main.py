# -*- coding: utf-8 -*-
# main.py — pipeline principal (com normalização de OHLC)

import os
import json
import time
from datetime import datetime

from coingecko_client import fetch_ohlc
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register

# =======================
# Config via Environment
# =======================
SYMBOLS = os.getenv(
    "SYMBOLS",
    "BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,ADAUSDT,SOLUSDT,DOGEUSDT,DOTUSDT,MATICUSDT,LTCUSDT,LINKUSDT"
).replace(" ", "").split(",")

DAYS_OHLC        = int(os.getenv("DAYS_OHLC", "14"))    # janelas de OHLC por símbolo
SCORE_THRESHOLD  = float(os.getenv("SCORE_THRESHOLD", "0.70"))
MIN_CONFIDENCE   = float(os.getenv("MIN_CONFIDENCE", "0.60"))   # filtro final de “confiança” do sinal
SELECT_PER_CYCLE = int(os.getenv("SELECT_PER_CYCLE", str(len(SYMBOLS))))  # quantos por ciclo
EXTRA_INDICATORS_LOG = os.getenv("EXTRA_INDICATORS_LOG", "0") == "1"

# anti-duplicado
COOLDOWN_HOURS     = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

DATA_RAW_FILE = "data_raw.json"
SIGNALS_FILE  = "signals.json"

# ===============
# Util / Helpers
# ===============
def _ts():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def normalize_ohlc(ohlc_raw):
    """
    Padroniza OHLC em lista de dicts:
    [{time, open, high, low, close}, ...]
    Aceita:
      - lista de listas [t, o, h, l, c]
      - lista de dicts com chaves open/high/low/close (opcionalmente t/time)
    """
    if not ohlc_raw:
        return []

    first = ohlc_raw[0]

    # Caso CoinGecko: lista de listas [time, open, high, low, close]
    if isinstance(first, (list, tuple)) and len(first) >= 5:
        out = []
        for c in ohlc_raw:
            try:
                out.append({
                    "time": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low":  float(c[3]),
                    "close":float(c[4]),
                })
            except Exception:
                # se alguma linha vier quebrada, ignora
                continue
        return out

    # Já no formato dict
    if isinstance(first, dict):
        out = []
        for c in ohlc_raw:
            try:
                # aceita variações de chave de tempo
                t = c.get("time", c.get("t", 0))
                out.append({
                    "time": int(t) if t is not None else 0,
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low":  float(c["low"]),
                    "close":float(c["close"]),
                })
            except Exception:
                continue
        return out

    # formato inesperado
    return []


def save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Falha ao salvar {path}: {e}")


def append_signal(sig: dict):
    try:
        existing = []
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing.append(sig)
        save_json(SIGNALS_FILE, existing)
    except Exception as e:
        print(f"⚠️ Falha ao registrar em {SIGNALS_FILE}: {e}")


# =================
# Pipeline principal
# =================
def run_pipeline():
    print("🧩 Coletando PREÇOS / OHLC…")
    collected = {}
    ok_symbols = []

    # Seleciona subset por ciclo, se configurado
    selected = SYMBOLS[:max(1, SELECT_PER_CYCLE)]

    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)  # pode retornar lista de listas ou de dicts
            ohlc = normalize_ohlc(raw)
            if len(ohlc) < 30:
                print(f"   → ❌ Dados insuficientes para {sym}")
                continue
            collected[sym] = ohlc
            ok_symbols.append(sym)
            print(f"   → OK | candles={len(ohlc)}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_symbols:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # salva crú (debug/telemetria)
    try:
        save_json(DATA_RAW_FILE, {k: v[-120:] for k, v in collected.items()})
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(collected)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    saved_count = 0

    for sym in ok_symbols:
        ohlc = collected[sym]
        closes = [c["close"] for c in ohlc]

        # score técnico (0..1) vindo do apply_strategies.score_signal
        try:
            sc = score_signal(closes)  # pode retornar None
        except Exception as e:
            print(f"⚠️ {sym}: erro em score_signal: {e}")
            sc = None

        shown = "None" if sc is None else f"{round(sc*100,1)}%"
        print(f"ℹ️ Score {sym}: {shown} (min {int(SCORE_THRESHOLD*100)}%)")

        if sc is None or sc < SCORE_THRESHOLD:
            continue

        # gera plano de trade com os mesmos candles (NORMALIZADOS)
        try:
            sig = generate_signal(sym, ohlc)  # deve devolver dict com entry/tp/sl/confidence/strategy...
        except TypeError:
            # se sua implementação antiga esperava apenas closes, tenta fallback
            try:
                sig = generate_signal(sym, [{"close": c} for c in closes])
            except Exception as e:
                print(f"⚠️ {sym}: erro em generate_signal (fallback): {e}")
                sig = None
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")
            sig = None

        if not sig:
            print(f"⚠️ {sym}: sem sinal técnico.")
            continue

        # normaliza campos esperados
        sig["symbol"] = sym
        sig["created_at"] = _ts()
        # se confiança vier 0..1, converte para 0..1; se vier em %, normaliza
        conf = sig.get("confidence", sc)
        try:
            conf = float(conf)
            if conf > 1.0:  # chegou em porcentagem
                conf = conf / 100.0
        except Exception:
            conf = sc
        sig["confidence"] = conf

        if conf < MIN_CONFIDENCE:
            print(f"⛔ {sym} descartado (<{int(MIN_CONFIDENCE*100)}%)")
            continue

        # anti-duplicado / cooldown
        ok_to_send, why = should_send_and_register(
            {
                "symbol": sym,
                "entry": sig.get("entry"),
                "tp":    sig.get("tp"),
                "sl":    sig.get("sl"),
            },
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT,
        )

        if not ok_to_send:
            print(f"🟡 {sym} pulado (duplicado: {why}).")
            continue

        # envia para Telegram
        pushed = send_signal_notification({
            "symbol": sym,
            "entry_price": sig.get("entry"),
            "target_price": sig.get("tp"),
            "stop_loss": sig.get("sl"),
            "risk_reward": sig.get("rr", 2.0),
            "confidence_score": round(conf*100, 2),
            "strategy": sig.get("strategy", "RSI+MACD+EMA+BB"),
            "created_at": sig.get("created_at", _ts()),
            "id": f"{sym}-{int(time.time())}",
        })

        if pushed:
            print("✅ Notificação enviada.")
        else:
            print("❌ Falha no envio (ver notifier_telegram).")

        append_signal(sig)
        saved_count += 1

    print(f"💾 {saved_count} sinais salvos em {SIGNALS_FILE}")
    # ... seu pipeline normal acima

# salvar data_raw.json já existe

# === depois de gerar/enviar sinais ===
try:
    from auto_labeler import auto_close_by_ohlc
    auto_close_by_ohlc()
except Exception as e:
    print(f"⚠️ AUTO_LABEL falhou: {e}")
    print(f"🕒 Fim: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    run_pipeline()
