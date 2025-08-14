# -*- coding: utf-8 -*-
import os
import json
import time
from datetime import datetime

# --- imports do seu projeto (com tolerância) ---
try:
    from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols  # fetch_top_symbols é opcional
except ImportError:
    # Em alguns repos o nome do arquivo foi digitado diferente; tente um alias comum
    from data_fetcer_coingecko import fetch_ohlc  # type: ignore
    fetch_top_symbols = None

try:
    from apply_strategies import generate_signal, score_signal
except Exception:
    generate_signal = None
    score_signal = None

try:
    from notifier_telegram import send_signal_notification
except Exception:
    def send_signal_notification(*args, **kwargs):
        print("⚠️ notifier_telegram não disponível — apenas registrando sem enviar.")
        return False

try:
    from positions_manager import should_send_and_register
except Exception:
    def should_send_and_register(sig, cooldown_hours=6.0, change_threshold_pct=1.0):
        # fallback “sempre envia”
        return True, "fallback"


# ================
# Config via ENV
# ================
def _getenv_float(key, default):
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return float(default)

def _getenv_int(key, default):
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return int(default)

SYMBOLS_ENV       = os.getenv("SYMBOLS", "").replace(" ", "")
TOP_SYMBOLS       = _getenv_int("TOP_SYMBOLS", 100)
SELECT_PER_CYCLE  = _getenv_int("SELECT_PER_CYCLE", 12)

DAYS_OHLC         = _getenv_int("DAYS_OHLC", _getenv_int("OHLC_DAYS", 14))
API_DELAY_OHLC    = _getenv_float("API_DELAY_OHLC", 12.0)   # delay entre requests
BACKOFF_BASE      = _getenv_float("BACKOFF_BASE", 2.5)      # usado dentro do fetch_ohlc do seu módulo

SCORE_THRESHOLD   = _getenv_float("SCORE_THRESHOLD", 0.70)  # 0.70 = 70%
MIN_CONFIDENCE    = _getenv_float("MIN_CONFIDENCE", 0.60)

COOLDOWN_HOURS    = _getenv_float("COOLDOWN_HOURS", 6.0)
CHANGE_THRESHOLD  = _getenv_float("CHANGE_THRESHOLD_PCT", 1.0)

DATA_RAW_FILE     = os.getenv("DATA_RAW_FILE", "data_raw.json")
SIGNALS_FILE      = os.getenv("SIGNALS_FILE", "signals.json")
STATE_FILE        = os.getenv("CYCLE_STATE_FILE", "cycle_state.json")

EXTRA_INDICATORS_LOG = os.getenv("EXTRA_INDICATORS_LOG", "0") == "1"

# ---------------
# Util / Helpers
# ---------------
def _ts():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _normalize_ohlc(raw):
    """
    Espera lista de velas no formato CoinGecko:
    [[t, o, h, l, c], ...]  ou dicts compatíveis.
    Normaliza para lista de dicts: [{time, open, high, low, close}, ...]
    """
    out = []
    if not raw:
        return out
    # Caso já esteja normalizado
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw
    for row in raw:
        try:
            t, o, h, l, c = row
            out.append({"time": t, "open": o, "high": h, "low": l, "close": c})
        except Exception:
            continue
    return out

# ---------------
# Universo de símbolos com fallback
# ---------------
_DEFAULT_100 = [
    "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","SOLUSDT","MATICUSDT","DOTUSDT","LTCUSDT",
    "LINKUSDT","TRXUSDT","AVAXUSDT","ATOMUSDT","ETCUSDT","XMRUSDT","XLMUSDT","NEARUSDT","APTUSDT","ARBUSDT",
    "FILUSDT","AAVEUSDT","INJUSDT","SANDUSDT","MANAUSDT","OPUSDT","FTMUSDT","GRTUSDT","EGLDUSDT","IMXUSDT",
    "THETAUSDT","ICPUSDT","HBARUSDT","RNDRUSDT","TWTUSDT","CHZUSDT","ALGOUSDT","FLOWUSDT","AXSUSDT","CRVUSDT",
    "SUIUSDT","PEPEUSDT","SEIUSDT","TONUSDT","PYTHUSDT","JTOUSDT","TIAUSDT","WIFUSDT","KASUSDT","JUPUSDT"
]

def build_universe():
    # 1) Se SYMBOLS foi setado, usa ele
    if SYMBOLS_ENV:
        syms = [s for s in SYMBOLS_ENV.split(",") if s]
        return syms

    # 2) Se existir fetch_top_symbols() no seu módulo, usa Top N dinâmico
    if callable(fetch_top_symbols):
        try:
            syms = fetch_top_symbols(TOP_SYMBOLS)  # deve retornar lista de strings tipo XXXUSDT
            if isinstance(syms, list) and syms:
                return syms[:TOP_SYMBOLS]
        except Exception as e:
            print(f"⚠️ Falha em fetch_top_symbols(TOP={TOP_SYMBOLS}): {e}")

    # 3) Fallback estático
    return _DEFAULT_100[:TOP_SYMBOLS]

# ---------------
# Seleção rotativa por ciclo
# ---------------
def load_cycle_state():
    st = _load_json(STATE_FILE, {"next": 0, "universe": []})
    return st

def save_cycle_state(state):
    _save_json(STATE_FILE, state)

def pick_symbols_for_cycle(universe, k):
    state = load_cycle_state()
    if state.get("universe") != universe:
        # Universo mudou -> zera rotação
        state = {"next": 0, "universe": universe}
    if not universe:
        return [], state
    start = state["next"] % len(universe)
    end = start + max(1, k)
    if end <= len(universe):
        selected = universe[start:end]
    else:
        selected = universe[start:] + universe[:(end % len(universe))]
    # atualiza ponteiro
    state["next"] = (start + len(selected)) % len(universe)
    return selected, state

# ---------------
# Chamadas tolerantes a assinaturas diferentes
# ---------------
def try_score_signal(ohlc):
    if score_signal is None:
        return None
    # tenta assinaturas comuns
    for call in (
        lambda: score_signal(ohlc=ohlc, min_confidence=MIN_CONFIDENCE),
        lambda: score_signal(ohlc, MIN_CONFIDENCE),
        lambda: score_signal(ohlc=ohlc),
        lambda: score_signal(ohlc),
    ):
        try:
            return call()
        except TypeError:
            continue
        except Exception as e:
            print(f"⚠️ erro em score_signal: {e}")
            return None
    return None

def try_generate_signal(ohlc, symbol):
    if generate_signal is None:
        return None
    for call in (
        lambda: generate_signal(ohlc=ohlc, symbol=symbol),
        lambda: generate_signal(ohlc, symbol),
        lambda: generate_signal(ohlc=ohlc),
        lambda: generate_signal(ohlc),
    ):
        try:
            return call()
        except TypeError:
            continue
        except Exception as e:
            print(f"⚠️ erro em generate_signal({symbol}): {e}")
            return None
    return None

# ---------------
# Pipeline principal
# ---------------
def run_pipeline():
    print("🧩 Coletando PREÇOS / OHLC…")

    universe = build_universe()
    selected, state = pick_symbols_for_cycle(universe, SELECT_PER_CYCLE)
    print(f"🔎 Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    collected = {}
    ok_symbols = []

    # coleta OHLC com delay entre símbolos
    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)  # sua função existente
            ohlc = _normalize_ohlc(raw)
            if len(ohlc) < 30:
                print(f"❌ Dados insuficientes para {sym}")
                time.sleep(API_DELAY_OHLC)
                continue
            collected[sym] = ohlc
            ok_symbols.append(sym)
            print(f"   → OK | candles={len(ohlc)}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")
        # delay entre chamadas para evitar 429
        time.sleep(API_DELAY_OHLC)

    if not ok_symbols:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # salva bruto para debug
    try:
        _save_json(DATA_RAW_FILE, {s: collected[s] for s in ok_symbols})
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # processa score + sinal
    saved_count = 0
    for sym in ok_symbols:
        ohlc = collected[sym]

        # ----- score técnico -----
        score_obj = try_score_signal(ohlc)
        score_val = None
        if isinstance(score_obj, (list, tuple)) and score_obj:
            # algumas versões retornam (score, detalhes)
            score_val = float(score_obj[0])
        elif isinstance(score_obj, (int, float)):
            score_val = float(score_obj)

        if score_val is None:
            print(f"⚠️ {sym}: erro em score_signal.")
            continue

        print(f"ℹ️ Score {sym}: {round(score_val*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")
        if score_val < SCORE_THRESHOLD:
            continue

        # ----- gera sinal completo -----
        sig = try_generate_signal(ohlc, sym)
        if not isinstance(sig, dict):
            print(f"⚠️ {sym}: sem sinal técnico.")
            continue

        # anti-duplicado / registrar
        ok_to_send, why = should_send_and_register(sig, cooldown_hours=COOLDOWN_HOURS, change_threshold_pct=CHANGE_THRESHOLD)
        if not ok_to_send:
            print(f"🟦 {sym} pulado (duplicado: {why}).")
            continue

        # envia
        pushed = False
        try:
            pushed = send_signal_notification(
                symbol=sym,
                entry_price=sig.get("entry"),
                target_price=sig.get("tp"),
                stop_loss=sig.get("sl"),
                rr=sig.get("rr", 2.0),
                confidence=sig.get("confidence", 0.0),
                strategy=sig.get("strategy", "RSI+MACD+EMA+BB"),
                created_at=sig.get("created_at", _ts()),
                signal_id=sig.get("id", f"{sym}-{int(time.time())}"),
                extra_log=EXTRA_INDICATORS_LOG,
            )
        except TypeError:
            # versão antiga do notifier sem named args
            try:
                pushed = send_signal_notification(sig)
            except Exception as e:
                print(f"❌ Falha no envio (notifier): {e}")
                pushed = False
        except Exception as e:
            print(f"❌ Falha no envio (notifier): {e}")
            pushed = False

        if pushed:
            print("✅ Notificação enviada.")
        else:
            print("❌ Falha no envio (ver notifier_telegram).")

        # salva/append do sinal emitido
        try:
            existing = _load_json(SIGNALS_FILE, [])
            existing.append({
                "symbol": sym,
                "entry": sig.get("entry"),
                "tp": sig.get("tp"),
                "sl": sig.get("sl"),
                "rr": sig.get("rr", 2.0),
                "confidence": sig.get("confidence", 0.0),
                "strategy": sig.get("strategy", "RSI+MACD+EMA+BB"),
                "created_at": sig.get("created_at", _ts()),
                "id": sig.get("id", f"{sym}-{int(time.time())}")
            })
            _save_json(SIGNALS_FILE, existing)
            saved_count += 1
        except Exception as e:
            print(f"⚠️ Falha ao registrar em {SIGNALS_FILE}: {e}")

    # fim ciclo + salva estado de rotação
    try:
        save_cycle_state(state)
    except Exception as e:
        print(f"⚠️ Falha ao salvar estado do ciclo: {e}")

    print(f"💾 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")


# Execução direta (modo sem runner)
if __name__ == "__main__":
    run_pipeline()
