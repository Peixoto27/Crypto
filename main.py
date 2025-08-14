# -*- coding: utf-8 -*-
import os, json, time
from datetime import datetime

# --- imports do projeto (existentes no seu repo)
from data_fetcher_coingecko import fetch_ohlc          # OK: só esta função existe
from apply_strategies import generate_signal, score_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register
from signal_generator import append_signal

# =========================
# Config via Environment
# =========================
DAYS_OHLC        = int(os.getenv("DAYS_OHLC", "14"))
SCORE_THRESHOLD  = float(os.getenv("SCORE_THRESHOLD", "0.70"))
MIN_CONFIDENCE   = float(os.getenv("MIN_CONFIDENCE", "0.60"))
SELECT_PER_CYCLE = int(os.getenv("SELECT_PER_CYCLE", "12"))
COOLDOWN_HOURS   = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

# lista de símbolos vinda do env (opcional). Se vazia, usa fallback de 50.
_raw_symbols = os.getenv("SYMBOLS", "").replace(" ", "")
SYMBOLS = [s for s in _raw_symbols.split(",") if s] if _raw_symbols else []

# Fallback de 50 pares mais líquidos (spot USDT)
FALLBACK50 = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","TRXUSDT","TONUSDT","LINKUSDT",
    "MATICUSDT","DOTUSDT","LTCUSDT","BCHUSDT","AVAXUSDT","UNIUSDT","ATOMUSDT","ETCUSDT","XLMUSDT","ICPUSDT",
    "APTUSDT","NEARUSDT","OPUSDT","ARBUSDT","FILUSDT","SUIUSDT","INJUSDT","ALGOUSDT","VETUSDT","AAVEUSDT",
    "FLOWUSDT","FTMUSDT","GRTUSDT","SNXUSDT","RUNEUSDT","SEIUSDT","RNDRUSDT","EGLDUSDT","MKRUSDT","KASUSDT",
    "TAOUSDT","IMXUSDT","HBARUSDT","SANDUSDT","MANAUSDT","AXSUSDT","PEPEUSDT","SHIBUSDT","JUPUSDT","PYTHUSDT"
]

ROTATE_FILE = "rotate_state.json"
DATA_RAW_FILE = "data_raw.json"
SIGNALS_FILE = "signals.json"

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _load_rotate_idx() -> int:
    try:
        with open(ROTATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return int(d.get("idx", 0))
    except Exception:
        return 0

def _save_rotate_idx(idx: int) -> None:
    try:
        with open(ROTATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"idx": idx}, f)
    except Exception:
        pass

def _save_json(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️  Falha ao salvar {path}: {e}")

def _pick_cycle_symbols(all_symbols, k: int):
    """rotação simples em arquivo para não repetir sempre as mesmas"""
    if not all_symbols:
        all_symbols = FALLBACK50
    k = max(1, min(k, len(all_symbols)))

    idx = _load_rotate_idx()
    selected = []
    for i in range(k):
        selected.append(all_symbols[(idx + i) % len(all_symbols)])
    _save_rotate_idx((idx + k) % len(all_symbols))
    return selected

# =========================
# Pipeline principal
# =========================
def run_pipeline():
    print("🧩 Coletando PREÇOS / OHLC…")
    universe = SYMBOLS if SYMBOLS else FALLBACK50
    selected = _pick_cycle_symbols(universe, SELECT_PER_CYCLE)
    print(f"🔎 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    collected = {}
    ok_syms = []
    for sym in selected:
        try:
            print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
            raw = fetch_ohlc(sym, DAYS_OHLC)   # sua função já retorna candles brutos
            if not raw or len(raw) < 30:
                print(f"❌ Dados insuficientes para {sym}.")
                continue
            # normalização já é feita dentro do apply_strategies (ele espera lista de dicts time/open/high/low/close)
            collected[sym] = raw
            ok_syms.append(sym)
            print(f"   → OK | candles={len(raw)}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_syms:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    _save_json(DATA_RAW_FILE, collected)
    print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_syms)} ativos)")

    saved = 0
    for sym in ok_syms:
        ohlc = collected.get(sym, [])
        try:
            # 1) pontuação/checagens técnicas
            score, tech_ok = score_signal(ohlc, min_confidence=MIN_CONFIDENCE)
            print(f"ℹ️ Score {sym}: {round(score*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")
            if not tech_ok:
                print(f"⚠️ {sym}: sem sinal técnico.")
                continue
            if score < SCORE_THRESHOLD:
                continue

            # 2) gerar sinal (entry/tp/sl/rr)
            sig = generate_signal(ohlc)
            if not sig or not sig.get("entry") or not sig.get("tp") or not sig.get("sl"):
                print(f"⚠️ {sym}: generate_signal não retornou campos suficientes.")
                continue

            sig["symbol"]     = sym
            sig["confidence"] = round(score, 4)
            sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB+EXTRA")
            sig["created_at"] = _ts()
            sig["id"]         = f"{sym}-{int(time.time())}"

            # 3) anti-duplicado
            ok_to_send, why = should_send_and_register(
                {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
                cooldown_hours=COOLDOWN_HOURS,
                change_threshold_pct=CHANGE_THRESHOLD
            )
            if not ok_to_send:
                print(f"🟦 {sym} pulado (anti-duplicado: {why}).")
                continue

            # 4) notificar
            pushed = send_signal_notification({
                "symbol": sym,
                "entry_price": sig.get("entry"),
                "target_price": sig.get("tp"),
                "stop_loss": sig.get("sl"),
                "risk_reward": sig.get("rr", 2.0),
                "confidence_score": round(score*100, 2),
                "strategy": sig.get("strategy"),
                "created_at": sig.get("created_at"),
                "id": sig.get("id"),
            })

            if pushed:
                print("✅ Notificação enviada.")
            else:
                print("❌ Falha no envio (ver notifier_telegram).")

            # 5) salvar no signals.json
            append_signal(sig)
            saved += 1

        except Exception as e:
            print(f"⚠️ {sym}: erro em score/generate/notificar: {e}")

    print(f"💾 {saved} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")

if __name__ == "__main__":
    run_pipeline()
