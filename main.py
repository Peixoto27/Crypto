# -*- coding: utf-8 -*-
# main.py — pipeline de sinais com rotação de símbolos por ciclo

import os
import json
import time
import random
from datetime import datetime

from data_fetcher_coingecko import fetch_ohlc          # retorna lista de candles brutos
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register  # anti-duplicado

# ============================
# Config via Environment
# ============================

SYMBOLS = os.getenv(
    "SYMBOLS",
    "BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,ADAUSDT,SOLUSDT,DOGEUSDT,MATICUSDT,DOTUSDT,LTCUSDT,LINKUSDT"
).replace(" ", "").split(",")

DAYS_OHLC         = int(os.getenv("DAYS_OHLC", "14"))         # janelas de OHLC
SCORE_THRESHOLD   = float(os.getenv("SCORE_THRESHOLD", "0.70"))  # corte do score técnico (0..1)
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.60"))    # confiança mínima (0..1)
SELECT_PER_CYCLE  = int(os.getenv("SELECT_PER_CYCLE", str(len(SYMBOLS))))
EXTRA_INDICATORS_LOG = os.getenv("EXTRA_INDICATORS_LOG", "0") == "1"

# anti-duplicado
COOLDOWN_HOURS     = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

# arquivos
DATA_RAW_FILE = os.getenv("DATA_RAW_FILE", "data_raw.json")
SIGNALS_FILE  = os.getenv("SIGNALS_FILE", "signals.json")

# rotação/seleção
ROTATE_MODE = os.getenv("ROTATE_MODE", "shuffle").lower()  # 'shuffle' (padrão) ou 'round_robin'
ROTATE_SEED = os.getenv("ROTATE_SEED")                     # opcional (reprodutível p/ shuffle)

# ============================
# Utils / Helpers
# ============================

def _ts():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Falha ao salvar {path}: {e}")

def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def normalize_ohlc(ohlc_raw):
    """
    Converte candles do CoinGecko (ou lista similar) para uma lista de dicts:
    [{time, open, high, low, close}, ...]
    Aceita também formatos já normalizados (neste caso retorna como veio).
    """
    if not ohlc_raw:
        return []

    # já normalizado?
    if isinstance(ohlc_raw, list) and isinstance(ohlc_raw[0], dict) and "close" in ohlc_raw[0]:
        return ohlc_raw

    out = []
    try:
        for row in ohlc_raw:
            # CoinGecko OHLC: [timestamp(ms), open, high, low, close]
            if isinstance(row, (list, tuple)) and len(row) >= 5:
                out.append({
                    "time":  int(row[0]) // 1000,
                    "open":  float(row[1]),
                    "high":  float(row[2]),
                    "low":   float(row[3]),
                    "close": float(row[4]),
                })
    except Exception:
        return []
    return out

# ============================
# Seleção de símbolos (rotação)
# ============================

_ROTATION_STATE_FILE = "rotation_state.json"

def _load_rotation_state():
    try:
        with open(_ROTATION_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"idx": 0}

def _save_rotation_state(state: dict):
    try:
        with open(_ROTATION_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass

def select_symbols_for_cycle(symbols, k, mode="shuffle", seed=None):
    """
    Retorna os símbolos que serão analisados neste ciclo.
    - shuffle: escolhe amostra aleatória sem repetição a cada ciclo
    - round_robin: percorre a lista inteira ao longo dos ciclos
    """
    n = len(symbols)
    if k >= n:
        return list(symbols)

    mode = (mode or "shuffle").lower()

    if mode == "round_robin":
        st = _load_rotation_state()
        i = st.get("idx", 0) % n
        end = i + k
        if end <= n:
            batch = symbols[i:end]
        else:
            batch = symbols[i:] + symbols[:(end % n)]
        st["idx"] = (i + k) % n
        _save_rotation_state(st)
        return batch

    # shuffle padrão
    if seed is not None:
        try:
            random.seed(int(seed))
        except Exception:
            pass
    return random.sample(symbols, k)

# ============================
# Pipeline principal
# ============================

def run_pipeline():
    print("🧩 Coletando PREÇOS / OHLC…")
    collected = {}
    ok_symbols = []

    # Seleciona subset por ciclo com rotação
    selected = select_symbols_for_cycle(
        SYMBOLS,
        max(1, SELECT_PER_CYCLE),
        mode=ROTATE_MODE,
        seed=ROTATE_SEED
    )
    print(f"🔎 Moedas deste ciclo ({len(selected)}/{len(SYMBOLS)}): {', '.join(selected)}")

    # coleta OHLC para os símbolos escolhidos
    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)      # pode retornar lista raw
            ohlc = normalize_ohlc(raw)
            if len(ohlc) < 30:
                print(f"❌ Dados insuficientes para {sym} (candles={len(ohlc)})")
                continue
            collected[sym] = ohlc
            ok_symbols.append(sym)
            print(f"   → OK | candles={len(ohlc)}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_symbols:
        print("⛔ Nenhum ativo com OHLC suficiente.")
        return

    # salva snapshot bruto (opcional)
    save_json(DATA_RAW_FILE, {s: len(collected.get(s, [])) for s in ok_symbols})
    print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")

    saved_count = 0

    # scoring + geração de sinal
    for sym in ok_symbols:
        try:
            sc, detail = score_signal(collected[sym], extra_log=EXTRA_INDICATORS_LOG)
        except Exception as e:
            print(f"⚠️ {sym}: erro em score_signal: {e}")
            continue

        print(f"ℹ️ Score {sym}: {round(sc*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")
        if sc < SCORE_THRESHOLD:
            print(f"🧪 {sym}: sem sinal técnico.")
            continue

        # gera um possível sinal (entry/tp/sl/rr/conf/strategy)
        try:
            sig = generate_signal(collected[sym], detail)
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")
            continue

        if not sig:
            print(f"🧪 {sym}: generate_signal não retornou setup.")
            continue

        conf = float(sig.get("confidence", 0.0))
        if conf < MIN_CONFIDENCE:
            print(f"🟡 {sym}: confiança {round(conf*100,1)}% < min {int(MIN_CONFIDENCE*100)}%")
            continue

        # anti-duplicado: respeita cooldown e mudança relevante
        ok_to_send, why = should_send_and_register(
            {
                "symbol": sym,
                "entry": sig.get("entry"),
                "tp":    sig.get("tp"),
                "sl":    sig.get("sl")
            },
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT
        )

        if not ok_to_send:
            print(f"🧱 {sym} pulado ({why}).")
            continue

        # notificação
        notif_payload = {
            "symbol": sym,
            "entry_price": sig.get("entry"),
            "target_price": sig.get("tp"),
            "stop_loss": sig.get("sl"),
            "risk_reward": sig.get("rr", 2.0),
            "confidence_score": round(conf*100, 2),
            "strategy": sig.get("strategy", "RSI+MACD+EMA+BB+EXTRA"),
            "created_at": sig.get("created_at", _ts()),
            "id": f"{sym}-{int(time.time())}",
        }

        pushed = False
        try:
            pushed = send_signal_notification(notif_payload)
        except Exception as e:
            print(f"⚠️ Falha no envio (ver notifier_telegram.py): {e}")

        if pushed:
            print("✅ Notificação enviada.")
        else:
            print("❌ Falha no envio (ver notifier_telegram.py).")

        # registra no arquivo de sinais
        try:
            existing = load_json(SIGNALS_FILE, [])
            existing.append(notif_payload)
            save_json(SIGNALS_FILE, existing)
            saved_count += 1
        except Exception as e:
            print(f"⚠️ Falha ao registrar em {SIGNALS_FILE}: {e}")

    print(f"💾 {saved_count} sinais salvos em {SIGNALS_FILE}.")
    print(f"🕒 Fim: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")

if __name__ == "__main__":
    run_pipeline()
