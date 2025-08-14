# -*- coding: utf-8 -*-
import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List

from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register

# ==========================
# Config via Environment
# ==========================
SYMBOLS_ENV       = os.getenv("SYMBOLS", "").replace(" ", "")
TOP_SYMBOLS       = int(os.getenv("TOP_SYMBOLS", "50"))
SELECT_PER_CYCLE  = int(os.getenv("SELECT_PER_CYCLE", "12"))
DAYS_OHLC         = int(os.getenv("DAYS_OHLC", "14"))
SCORE_THRESHOLD   = float(os.getenv("SCORE_THRESHOLD", "0.70"))
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.60"))
EXTRA_SCORE_WEIGHT= float(os.getenv("EXTRA_SCORE_WEIGHT", "0.0"))
EXTRA_INDICATORS_LOG = os.getenv("EXTRA_INDICATORS_LOG", "0") == "1"

# anti-duplicado
COOLDOWN_HOURS      = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT= float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

DATA_RAW_FILE = "data_raw.json"
SIGNALS_FILE  = "signals.json"

# round-robin state
_LAST_INDEX_FILE = ".last_index.txt"


# ==========================
# Utils
# ==========================
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def _ensure_file(path: str, default):
    try:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)
    except:
        pass


def save_json(path: str, data: Any):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Falha ao salvar {path}: {e}")


def normalize_ohlc(ohlc_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Garante formato de dicts [{time, open, high, low, close}, ...]
    """
    out: List[Dict[str, Any]] = []
    for row in ohlc_raw or []:
        try:
            out.append({
                "time": int(row["time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low":  float(row["low"]),
                "close":float(row["close"]),
            })
        except Exception:
            continue
    return out


def _load_last_index() -> int:
    try:
        if os.path.exists(_LAST_INDEX_FILE):
            with open(_LAST_INDEX_FILE, "r", encoding="utf-8") as f:
                return int(f.read().strip())
    except:
        pass
    return 0


def _save_last_index(i: int) -> None:
    try:
        with open(_LAST_INDEX_FILE, "w", encoding="utf-8") as f:
            f.write(str(int(i)))
    except:
        pass


def append_signal(sig: Dict[str, Any]) -> None:
    _ensure_file(SIGNALS_FILE, [])
    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = []
    data.append(sig)
    save_json(SIGNALS_FILE, data)


# ==========================
# Símbolos
# ==========================
def resolve_symbols() -> List[str]:
    """Usa SYMBOLS (fixo) ou TOP_SYMBOLS dinâmico via CoinGecko."""
    if SYMBOLS_ENV:
        syms = [s for s in SYMBOLS_ENV.split(",") if s]
        return syms
    syms = fetch_top_symbols(TOP_SYMBOLS)
    if not syms:
        syms = ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","ADAUSDT","SOLUSDT","DOGEUSDT","DOTUSDT","MATICUSDT","LTCUSDT","LINKUSDT"]
    return syms


# ==========================
# Pipeline principal
# ==========================
def run_pipeline():
    print("🧩 Coletando PREÇOS / OHLC…")

    # round-robin: seleciona subconjunto por ciclo
    all_symbols = resolve_symbols()
    if not all_symbols:
        print("⚠️ Nenhum símbolo disponível.")
        return

    last_i = _load_last_index()
    N = len(all_symbols)
    k = max(1, min(SELECT_PER_CYCLE, N))

    start = last_i % N
    selected = []
    for ofs in range(k):
        selected.append(all_symbols[(start + ofs) % N])
    _save_last_index((start + k) % N)

    print(f"🔎 Moedas deste ciclo ({len(selected)}/{N}): {', '.join(selected)}")

    collected: Dict[str, List[Dict[str, Any]]] = {}
    ok_symbols: List[str] = []

    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)
            ohlc = normalize_ohlc(raw)
            if len(ohlc) < 30:
                print(f"  ❌ Dados insuficientes para {sym}")
                continue
            collected[sym] = ohlc
            ok_symbols.append(sym)
            print(f"   → OK | candles={len(ohlc)}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_symbols:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # salvar snapshot cru
    save_json(DATA_RAW_FILE, {s: collected.get(s, []) for s in ok_symbols})
    print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")

    # Scoring + geração de sinais
    saved_count = 0
    for sym in ok_symbols:
        try:
            conf, details = score_signal(
                collected[sym],
                min_confidence=MIN_CONFIDENCE,
                extra_weight=EXTRA_SCORE_WEIGHT,
                extra_log=EXTRA_INDICATORS_LOG,  # tolerado em apply_strategies
            )
            print(f"ℹ️ Score {sym}: {round(conf*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")

            if conf < SCORE_THRESHOLD:
                continue

            sig_plan = generate_signal(collected[sym])
            if not sig_plan:
                print(f"⚠️ {sym}: sem sinal técnico.")
                continue

            # formata payload do sinal
            sig = {
                "symbol": sym,
                "entry": sig_plan.get("entry"),
                "tp": sig_plan.get("tp"),
                "sl": sig_plan.get("sl"),
                "rr": sig_plan.get("rr", 2.0),
                "confidence": float(conf),
                "strategy": sig_plan.get("strategy", "RSI+MA"),
                "created_at": sig_plan.get("created_at", _ts()),
                "id": f"{sym}-{int(time.time())}",
            }

            ok_to_send, why = should_send_and_register(
                sig,
                cooldown_hours=COOLDOWN_HOURS,
                change_threshold_pct=CHANGE_THRESHOLD_PCT,
            )
            if not ok_to_send:
                print(f"🟡 {sym} pulado (duplicado: {why}).")
                continue

            pushed = send_signal_notification(
                symbol=sym,
                entry=sig["entry"],
                tp=sig["tp"],
                sl=sig["sl"],
                rr=sig["rr"],
                confidence=sig["confidence"],
                strategy=sig["strategy"],
                created_at=sig["created_at"],
                signal_id=sig["id"],
            )

            if pushed:
                print("✅ Notificação enviada.")
            else:
                print("❌ Falha no envio (ver notifier_telegram).")

            append_signal(sig)
            saved_count += 1

        except Exception as e:
            print(f"⚠️ {sym}: erro em fluxo de sinal: {e}")

    print(f"💾 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
