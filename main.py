# -*- coding: utf-8 -*-
"""
main.py — pipeline principal (corrigido)
- Seleciona universo (fixo via ENV ou dinâmico via CoinGecko)
- Filtra apenas pares estáveis REDUNDANTES (ex.: FDUSDUSDT), preserva BTCUSDT etc.
- Rotaciona subset por ciclo
- Coleta OHLC
- Calcula score técnico (com vários indicadores) + sentimento (NewsData) opcional
- Mistura (pesos via ENV)
- Anti-duplicado e envio para Telegram
- Salva data_raw.json e, opcionalmente, histórico por símbolo

ENV relevantes (exemplos):
  SYMBOLS=BTCUSDT,ETHUSDT,...
  TOP_SYMBOLS=100
  SELECT_PER_CYCLE=8
  DAYS_OHLC=30
  MIN_BARS=180
  SCORE_THRESHOLD=0.70
  MIN_CONFIDENCE=0.60
  WEIGHT_TECH=1.0
  WEIGHT_SENT=0.5
  FILTER_STABLE_REDUNDANT=true
  COOLDOWN_HOURS=6
  CHANGE_THRESHOLD_PCT=1.0
  DATA_RAW_FILE=data_raw.json
  CURSOR_FILE=scan_state.json
  SIGNALS_FILE=signals.json
  SAVE_HISTORY=true
  HISTORY_DIR=data/history
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Tuple

# ====== Módulos do projeto ======
from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
from apply_strategies import generate_signal, score_signal
from positions_manager import should_send_and_register
from signal_generator import append_signal  # salva no signals.json

# Notificações (v2 se existir; senão, v1)
try:
    from notifier_telegram_v2 import send_signal_notification
except Exception:
    from notifier_telegram import send_signal_notification  # type: ignore

# Sentimento opcional
try:
    from sentiment_analyzer import get_sentiment_score  # retorna (-1..1) e n
except Exception:
    def get_sentiment_score(symbol: str):
        return 0.0, 0  # (sent, n)

# Histórico opcional
try:
    from history_manager import save_ohlc_to_history
except Exception:
    def save_ohlc_to_history(*args, **kwargs):
        return False

# ==============================
# Config via Environment
# ==============================
def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)

SYMBOLS = [s for s in _get_env("SYMBOLS", "").replace(" ", "").split(",") if s]
TOP_SYMBOLS       = int(_get_env("TOP_SYMBOLS", "100"))
SELECT_PER_CYCLE  = int(_get_env("SELECT_PER_CYCLE", "8"))
DAYS_OHLC         = int(_get_env("DAYS_OHLC", "30"))
MIN_BARS          = int(_get_env("MIN_BARS", "180"))

SCORE_THRESHOLD   = float(_get_env("SCORE_THRESHOLD", "0.70"))
MIN_CONFIDENCE    = float(_get_env("MIN_CONFIDENCE", "0.60"))

COOLDOWN_HOURS        = float(_get_env("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT  = float(_get_env("CHANGE_THRESHOLD_PCT", "1.0"))

WEIGHT_TECH = float(_get_env("WEIGHT_TECH", "1.0"))
WEIGHT_SENT = float(_get_env("WEIGHT_SENT", "0.0"))  # 0 = ignora sentimento

DATA_RAW_FILE  = _get_env("DATA_RAW_FILE", "data_raw.json")
CURSOR_FILE    = _get_env("CURSOR_FILE", "scan_state.json")
SIGNALS_FILE   = _get_env("SIGNALS_FILE", "signals.json")

FILTER_STABLE_REDUNDANT = _get_env("FILTER_STABLE_REDUNDANT", "true").lower() in ("1","true","yes")

SAVE_HISTORY  = _get_env("SAVE_HISTORY", "false").lower() in ("1","true","yes")
HISTORY_DIR   = _get_env("HISTORY_DIR", "data/history")

# ==============================
# Helpers simples
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
    if take <= 0 or not symbols:
        return symbols
    st = _ensure_cursor()
    off = st.get("offset", 0) % len(symbols)
    batch = []
    for i in range(min(take, len(symbols))):
        batch.append(symbols[(off + i) % len(symbols)])
    st["offset"] = (off + take) % len(symbols)
    st["cycle"] = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _safe_score(ohlc) -> float:
    """Normaliza retorno de score_signal para float 0..1."""
    try:
        res = score_signal(ohlc)
        if isinstance(res, tuple):
            s = float(res[0])
        elif isinstance(res, dict):
            s = float(res.get("score", res.get("value", res.get("confidence", res.get("prob", 0.0)))))
        else:
            s = float(res)
    except Exception:
        s = 0.0
    if s > 1.0:
        s /= 100.0
    return max(0.0, min(1.0, round(s, 6)))

def _mix_confidence(score_tech: float, sent: float) -> float:
    """Mistura técnico (0..1) com sentimento (-1..1) -> 0..1"""
    sent01 = (sent + 1.0) / 2.0
    total_w = max(1e-9, WEIGHT_TECH + WEIGHT_SENT)
    mixed = (WEIGHT_TECH * score_tech + WEIGHT_SENT * sent01) / total_w
    return max(0.0, min(1.0, mixed))

# ==============================
# Split seguro & filtro de pares estáveis
# ==============================
QUOTES_KNOWN = [
    "USDT", "FDUSD", "USDC", "BUSD", "TUSD", "DAI",
    "EUR", "BRL", "TRY", "GBP", "AUD", "RUB", "PLN", "ZAR", "BTC", "ETH"
]
STABLES = {"USDT","FDUSD","USDC","BUSD","TUSD","DAI","EUR","BRL","TRY","GBP","AUD","RUB","PLN","ZAR"}

def split_symbol_safe(sym: str) -> Tuple[str, str]:
    s = sym.upper().replace("-", "").replace("_", "")
    for q in sorted(QUOTES_KNOWN, key=len, reverse=True):
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)], q
    if len(s) > 4:
        return s[:-4], s[-4:]
    return s, ""

def filter_redundant_stables(symbols: List[str]) -> Tuple[List[str], List[str]]:
    kept, removed = [], []
    for sym in symbols:
        base, quote = split_symbol_safe(sym)
        if base in STABLES and quote in STABLES:
            removed.append(sym)
        else:
            kept.append(sym)
    return kept, removed

# ==============================
# Pipeline principal
# ==============================
def run_pipeline():
    print("Starting Container")
    print(f"▶️ Runner iniciado. Intervalo = {float(_get_env('RUN_INTERVAL_MIN','20')):.1f} min.")

    # status de features
    news_on = True  # a presença real da chave é checada dentro do sentiment_analyzer
    ia_on   = True  # IA de features já carregada no seu ambiente
    print(f"🔎 NEWS ativo?: True | IA ativa?: true | Histórico ativado?: {bool(SAVE_HISTORY)}")

    # 1) universo
    if SYMBOLS:
        universe = SYMBOLS[:]
    else:
        universe = fetch_top_symbols(TOP_SYMBOLS)

    # 1.1) filtro de pares estáveis redundantes
    if FILTER_STABLE_REDUNDANT:
        universe_filtered, removed = filter_redundant_stables(universe)
        if removed:
            print(f"🧽 Removidos {len(removed)} pares estáveis redundantes (ex.: {removed[0]}).")
    else:
        universe_filtered = universe[:]

    if not universe_filtered:
        print("🪂 Filtro zerou o universo. Restaurando lista original para este ciclo.")
        universe_filtered = universe[:]

    # 2) seleção/rotação
    selected = _rotate(universe_filtered, SELECT_PER_CYCLE)
    print(f"🧪 Moedas deste ciclo ({len(selected)}/{len(universe_filtered)}): {', '.join(selected) if selected else '—'}")
    if not selected:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # 3) coleta OHLC
    collected: Dict[str, Any] = {}
    ok_symbols: List[str] = []
    for sym in selected:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            raw = fetch_ohlc(sym, DAYS_OHLC)  # lista [[ts,o,h,l,c],...]
            if not raw or len(raw) < MIN_BARS:
                print(f"❌ Dados insuficientes para {sym}")
                continue
            collected[sym] = raw
            ok_symbols.append(sym)
            print(f"   → OK | candles={len(raw)}")

            # salva histórico por símbolo (opcional)
            if SAVE_HISTORY:
                try:
                    saved = save_ohlc_to_history(sym, raw, HISTORY_DIR)
                    if saved:
                        pass  # silencioso
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_symbols:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # 4) salva snapshot do ciclo
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_symbols, "data": collected}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # 5) pontua, mistura sentimento e envia
    saved_count = 0
    for sym in ok_symbols:
        ohlc = collected.get(sym)

        # score técnico
        s_tech = _safe_score(ohlc)

        # sentimento (valor e contagem n)
        try:
            s_sent, n_news = get_sentiment_score(sym)
        except Exception:
            s_sent, n_news = 0.0, 0

        mixed = _mix_confidence(s_tech, s_sent)

        # logs detalhados
        print(f"📊 {sym} | Técnico: {round(s_tech*100,1)}% | Sentimento: {round((s_sent+1)*50,1)}% (n={n_news}) | "
              f"Mix(T:{WEIGHT_TECH:.1f},S:{WEIGHT_SENT:.1f}): {round(mixed*100,1)}% (min {int(MIN_CONFIDENCE*100)}%)")

        if s_tech < SCORE_THRESHOLD or mixed < MIN_CONFIDENCE:
            continue

        # gera plano (entry/tp/sl)
        try:
            sig = generate_signal(ohlc)
        except Exception as e:
            print(f"⚠️ {sym}: erro em generate_signal: {e}")
            sig = None

        if not isinstance(sig, dict):
            print(f"⚠️ {sym}: sem sinal técnico.")
            continue

        # completa payload
        sig["symbol"]     = sym
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["confidence"] = float(mixed)
        sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB+STOCHRSI+ADX+CCI+ICHI+OBV+MFI+WILLR")
        sig["created_at"] = sig.get("created_at", _ts())
        if "id" not in sig:
            sig["id"] = f"sig-{int(time.time())}"

        # anti-duplicado
        ok_to_send, reason = should_send_and_register(
            {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT
        )
        if not ok_to_send:
            print(f"🟡 {sym} não enviado ({reason}).")
            continue

        # envia
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
            print("❌ Falha no envio (ver notifier).")

        # registra no arquivo de sinais
        try:
            append_signal(sig)
            saved_count += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar em {SIGNALS_FILE}: {e}")

    print(f"🗂 {saved_count} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {_ts()}")


if __name__ == "__main__":
    run_pipeline()
