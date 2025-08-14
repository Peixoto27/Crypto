# -*- coding: utf-8 -*-
import os
import json
import time
from datetime import datetime
from typing import Dict, List, Any

# ---- módulos do projeto
from data_fetcher_coingecko import get_all_coins, fetch_top_symbols, fetch_ohlc
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register
from signal_generator import append_signal  # salva no signals.json

# ==========================
# Config via Environment
# ==========================
def _as_bool(v: str, default=False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

SYMBOLS_ENV      = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]  # opcional
TOP_SYMBOLS      = int(os.getenv("TOP_SYMBOLS", "50"))          # tamanho do universo dinâmico
SELECT_PER_CYCLE = int(os.getenv("SELECT_PER_CYCLE", "12"))     # quantas por ciclo (round-robin)
DAYS_OHLC        = int(os.getenv("DAYS_OHLC", "14"))
SCORE_THRESHOLD  = float(os.getenv("SCORE_THRESHOLD", "0.70"))  # corte do score (0..1)
MIN_CONFIDENCE   = float(os.getenv("MIN_CONFIDENCE", "0.45"))   # corte da confiança (0..1)

COOLDOWN_HOURS       = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

DATA_RAW_FILE = os.getenv("DATA_RAW_FILE", "data_raw.json")
SIGNALS_FILE  = os.getenv("SIGNALS_FILE", "signals.json")

# estado simples p/ round-robin
RR_STATE_FILE = os.getenv("RR_STATE_FILE", ".rr_state.json")

# ==========================
# Utils
# ==========================
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _save_json(path: str, data: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_json(path: str, default: Any):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _load_rr_state() -> Dict[str, int]:
    return _load_json(RR_STATE_FILE, {"idx": 0})

def _save_rr_state(state: Dict[str, int]):
    _save_json(RR_STATE_FILE, state)

def _normalize_ohlc(raw: List[List[float]]) -> List[Dict[str, float]]:
    """
    raw CoinGecko OHLC = [[timestamp(ms), open, high, low, close], ...]
    normaliza p/ lista de dicts
    """
    out = []
    for r in raw or []:
        if len(r) < 5:
            continue
        out.append({
            "time": int(r[0]) // 1000,
            "open": float(r[1]),
            "high": float(r[2]),
            "low":  float(r[3]),
            "close":float(r[4]),
        })
    return out

# ==========================
# Pipeline principal
# ==========================
def run_pipeline():
    print("🧩 Coletando PREÇOS / OHLC…")

    # ----- universo de símbolos
    if SYMBOLS_ENV:
        all_symbols = SYMBOLS_ENV[:]  # lista fixa definida no env
    else:
        # top-N dinâmico via CoinGecko
        top = fetch_top_symbols(TOP_SYMBOLS)
        # Garante sufixo USDT e caixa alta
        all_symbols = [s.upper() if s.endswith("USDT") else (s.upper()+"USDT") for s in top]

    if not all_symbols:
        print("⚠️ Nenhum símbolo disponível neste ciclo.")
        return

    # round-robin: pega janelinha de SELECT_PER_CYCLE
    rr = _load_rr_state()
    start = rr.get("idx", 0)
    end = start + max(1, min(SELECT_PER_CYCLE, len(all_symbols)))
    # wrap
    selected = (all_symbols + all_symbols)[start:end]
    new_idx = end % len(all_symbols)
    _save_rr_state({"idx": new_idx})

    print(f"🔎 Moedas deste ciclo ({len(selected)}/{len(all_symbols)}): " + ", ".join(selected))

    # mapa symbol->id do CoinGecko (evita 404 coins//ohlc)
    symbol_map = get_all_coins()  # { 'BTC': 'bitcoin', ... }

    collected: Dict[str, List[Dict[str, float]]] = {}
    ok_symbols: List[str] = []

    # ----- coleta OHLC
    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        raw = fetch_ohlc(sym, DAYS_OHLC, symbol_map=symbol_map)
        if not raw:
            print(f"⚠️ Falha/indisponível OHLC: {sym}")
            continue
        ohlc = _normalize_ohlc(raw)
        if len(ohlc) < 30:
            print(f"❌ Dados insuficientes para {sym} (candles={len(ohlc)})")
            continue
        collected[sym] = ohlc
        ok_symbols.append(sym)
        print(f"   → OK | candles={len(ohlc)}")

    if not ok_symbols:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # salva raw (inspecionar depois, útil p/ debug/treino)
    _save_json(DATA_RAW_FILE, {"collected_at": _ts(), "series": list(ok_symbols)})

    saved_count = 0

    # ----- pontuação e geração de sinal
    for sym in ok_symbols:
        ohlc = collected[sym]

        try:
            score = float(score_signal(ohlc))  # espera 0..1
        except Exception as e:
            print(f"⚠️ {sym}: erro em score_signal: {e}")
            continue

        print(f"ℹ️ Score {sym}: {round(score*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")

        if score < SCORE_THRESHOLD:
            continue

        # gera sinal técnico (entry/tp/sl)
        sig = None
        try:
            sig = generate_signal(ohlc)
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")

        if not sig or not all(k in sig for k in ("entry","tp","sl")):
            print(f"⚠️ {sym}: sem sinal técnico.")
            continue

        # monta payload padrão
        sig_payload = {
            "symbol": sym,
            "entry": float(sig["entry"]),
            "tp":    float(sig["tp"]),
            "sl":    float(sig["sl"]),
            "rr":    float(sig.get("rr", 2.0)),
            "confidence": float(max(score, MIN_CONFIDENCE)),  # 0..1
            "strategy": sig.get("strategy", "RSI+MACD+EMA+BB+EXTRA"),
            "created_at": _ts(),
            "id": f"{sym}-{int(time.time())}"
        }

        # deduplicação / cooldown
        ok_to_send, why = should_send_and_register(
            sig_payload,
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT
        )

        if not ok_to_send:
            print(f"🔁 {sym}: não enviado ({why}).")
            continue

        # envia ao Telegram
        pushed = send_signal_notification(
            symbol=sig_payload["symbol"],
            entry_price=sig_payload["entry"],
            target_price=sig_payload["tp"],
            stop_loss=sig_payload["sl"],
            risk_reward=sig_payload["rr"],
            confidence_score=sig_payload["confidence"],
            strategy=sig_payload["strategy"],
            created_at=sig_payload["created_at"],
            signal_id=sig_payload["id"]
        )

        if pushed:
            print("✅ Notificação enviada.")
        else:
            print("❌ Falha no envio (ver notifier_telegram).")

        # persiste no signals.json
        append_signal(sig_payload)
        saved_count += 1

    print(f"💾 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")

# exec local/manual
if __name__ == "__main__":
    run_pipeline()
