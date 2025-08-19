# -*- coding: utf-8 -*-
"""
main.py — ciclo de varredura/score/sinal com status de NEWS/IA/Twitter, cache local e tolerância a faltas.

Fluxo:
  1) Banner de status (NEWS/IA/Hist/Twitter)
  2) Seleciona lote de símbolos (cursor round-robin)
  3) Coleta OHLC (CoinGecko), com backoff para 429/timeout
  4) Salva cache do ciclo: data_raw.json
  5) Calcula técnico + sentimento (News/Twitter se disponíveis)
  6) Mistura técnico×sentimento e decide sinal
  7) Envia para Telegram (notifier_telegram_v2), com valores em USD

Compatível com seus módulos existentes:
- data_fetcher_coingecko.fetch_ohlc(symbol, days)
- apply_strategies.score_signal(ohlc) -> [0..1] ou %
- apply_strategies.generate_signal(ohlc) -> dict(entry,tp,sl,confidence,strategy_name,rr)
- notifier_telegram_v2.send_signal(...)
- history_manager.append_ohlc_snapshot(symbol, ohlc, base_dir)
- sentiment_analyzer.get_sentiment_score(symbol)  (NewsData)
- twitter_sentiment.get_sentiment_score(symbol)   (Twitter opcional)
- sentiment_runtime.init_sentiment_runtime(); runtime().new_cycle(); runtime().status()

Se algum não existir, o main usa fallbacks sem travar.
"""

from __future__ import annotations
import os, json, time, math, traceback
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# ========================
# Imports do projeto (tolerantes)
# ========================
try:
    from data_fetcher_coingecko import fetch_ohlc
except Exception:
    fetch_ohlc = None

try:
    from apply_strategies import score_signal, generate_signal
except Exception:
    def score_signal(_): return 0.0
    def generate_signal(_): return None

# Telegram (preferimos a V2, se existir)
_notifier = None
try:
    import notifier_telegram_v2 as ntv2
    _notifier = ntv2
except Exception:
    try:
        import notifier_telegram as ntv1
        _notifier = ntv1
    except Exception:
        _notifier = None

# Histórico (opcional)
try:
    from history_manager import append_ohlc_snapshot
except Exception:
    def append_ohlc_snapshot(*args, **kwargs): pass

# Sentimento de notícias (opcional)
try:
    from sentiment_analyzer import get_sentiment_score as news_sentiment
except Exception:
    def news_sentiment(symbol: str) -> Tuple[float, int]:
        return (0.5, 0)  # 50% neutro, n=0

# Sentimento de Twitter (opcional)
try:
    from twitter_sentiment import get_sentiment_score as tw_sentiment
except Exception:
    def tw_sentiment(symbol: str) -> Tuple[float, int]:
        return (0.5, 0)  # 50% neutro, n=0

# Runtime agregado de sentimento (opcional)
try:
    from sentiment_runtime import init_sentiment_runtime, runtime as _sent_rt
except Exception:
    def init_sentiment_runtime(): return None
    class _DummyRT:
        def new_cycle(self): pass
        def status(self): return {}
    def _sent_rt(): return _DummyRT()

# ========================
# Utils
# ========================
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _yes(b: bool) -> str:
    return "True" if b else "False"

def _env_bool(k: str, default: str = "false") -> bool:
    return os.getenv(k, default).lower() in ("1", "true", "yes")

def _env_int(k: str, default: int) -> int:
    try:
        return int(os.getenv(k, str(default)))
    except Exception:
        return default

def _env_float(k: str, default: float) -> float:
    try:
        return float(os.getenv(k, str(default)))
    except Exception:
        return default

def _norm_rows(rows: Any) -> List[Dict[str,float]]:
    """
    Aceita [[ts,o,h,l,c], ...] ou [{'t','o','h','l','c'}...]
    """
    out: List[Dict[str,float]] = []
    if not rows: return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]),
                            "l": float(r[3]), "c": float(r[4])})
    elif isinstance(rows, list) and isinstance(rows[0], dict):
        for r in rows:
            out.append({
                "t": float(r.get("t", 0.0)),
                "o": float(r.get("o", r.get("open", 0.0))),
                "h": float(r.get("h", r.get("high", 0.0))),
                "l": float(r.get("l", r.get("low", 0.0))),
                "c": float(r.get("c", r.get("close", 0.0))),
            })
    return out

def _backoff_sleep(base: float, attempt: int):
    # 1/6 -> 30s, 2/6 -> 75s, etc (padrão que você usa)
    wait = base * (1.0 if attempt == 1 else 2.5)
    time.sleep(wait)
    return wait

# ========================
# Carrega ENV
# ========================
INTERVAL_MIN       = _env_int("INTERVAL_MIN", 20)
DAYS_OHLC          = _env_int("DAYS_OHLC", 30)
MIN_BARS           = _env_int("MIN_BARS", 180)
BATCH_PER_CYCLE    = _env_int("BATCH_PER_CYCLE", 8)
SCORE_THRESHOLD    = _env_float("SCORE_THRESHOLD", 0.70)
MIX_T_WEIGHT       = _env_float("MIX_T_WEIGHT", 1.5)
MIX_S_WEIGHT       = _env_float("MIX_S_WEIGHT", 1.0)

USE_AI             = _env_bool("USE_AI", "true")
TRAINING_ENABLED   = _env_bool("TRAINING_ENABLED", "true")

SAVE_HISTORY       = _env_bool("SAVE_HISTORY", "true")
HISTORY_DIR        = os.getenv("HISTORY_DIR", "data/history")
DATA_RAW_FILE      = os.getenv("DATA_RAW_FILE", "data_raw.json")
CURSOR_FILE        = os.getenv("CURSOR_FILE", "scan_state.json")

# News
NEWS_API_KEY             = os.getenv("NEWS_API_KEY", "")
NEWS_USE                 = bool(NEWS_API_KEY)
NEWS_MONTHLY_BUDGET      = _env_int("NEWS_MONTHLY_BUDGET", 100)
NEWS_CALLS_PER_CYCLE_MAX = _env_int("NEWS_CALLS_PER_CYCLE_MAX", 1)

# Twitter
TWITTER_USE              = _env_bool("TWITTER_USE", "false")

# Telegram
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Universo de símbolos
_symbols_env = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]

# Se existir um arquivo symbols.py com SYMBOLS, usamos como fallback
if not _symbols_env:
    try:
        from symbols import SYMBOLS as _SYMS_FILE
        _symbols_env = list(_SYMS_FILE)
    except Exception:
        # fallback mínimo (não deixe vazio)
        _symbols_env = ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]

# ========================
# Cursor round-robin
# ========================
def _read_cursor() -> int:
    try:
        with open(CURSOR_FILE, "r", encoding="utf-8") as f:
            j = json.load(f)
        return int(j.get("idx", 0))
    except Exception:
        return 0

def _write_cursor(i: int):
    try:
        with open(CURSOR_FILE, "w", encoding="utf-8") as f:
            json.dump({"idx": i}, f)
    except Exception:
        pass

def _pick_batch() -> List[str]:
    if not _symbols_env: return []
    idx = _read_cursor()
    n = len(_symbols_env)
    batch = []
    for k in range(BATCH_PER_CYCLE):
        batch.append(_symbols_env[(idx + k) % n])
    _write_cursor((idx + BATCH_PER_CYCLE) % n)
    return batch

# ========================
# Coleta OHLC (com backoff simples)
# ========================
def _collect_ohlc_symbol(sym: str, days: int, min_bars: int) -> List[Dict[str,float]]:
    print(f"📊 Coletando OHLC {sym} (days={days})…")
    if fetch_ohlc is None:
        print(f"❌ fetch_ohlc ausente. Pulei {sym}.")
        return []
    last_exc = None
    for attempt in range(1, 7):  # 1..6
        try:
            rows = fetch_ohlc(sym, days)
            bars = _norm_rows(rows)
            if len(bars) >= min_bars:
                print(f"   → OK | candles={len(bars)}")
                return bars
            else:
                print(f"❌ Dados insuficientes para {sym} ({len(bars)}/{min_bars})")
                return []
        except Exception as e:
            msg = str(e)
            last_exc = e
            if "429" in msg or "Read timed out" in msg or "Timeout" in msg:
                wait = _backoff_sleep(30.0, attempt)
                print(f"⚠️ 429/Timeout OHLC: aguardando {wait:.1f}s (tentativa {attempt}/6)")
                continue
            elif "mapear" in msg or "CoinGecko" in msg:
                print(f"⚠️ Erro OHLC {sym}: {msg}")
                return []
            else:
                print(f"⚠️ Erro de rede: {msg}. Aguardando 30.0s (tentativa {attempt}/6)")
                time.sleep(30.0)
    print(f"❌ Falha OHLC {sym}: {last_exc}")
    return []

# ========================
# Sentimentos
# ========================
def _get_news_sentiment(sym: str) -> Tuple[float, int]:
    if not NEWS_USE or NEWS_MONTHLY_BUDGET <= 0 or NEWS_CALLS_PER_CYCLE_MAX <= 0:
        return (0.5, 0)
    try:
        return news_sentiment(sym)  # retorna (score[0..1], n)
    except Exception:
        return (0.5, 0)

def _get_twitter_sentiment(sym: str) -> Tuple[float, int]:
    if not TWITTER_USE:
        return (0.5, 0)
    try:
        return tw_sentiment(sym)
    except Exception:
        return (0.5, 0)

def _mix_sentiment(sym: str) -> Tuple[float, Dict[str,Any]]:
    s_news, n_news = _get_news_sentiment(sym)
    s_twt,  n_twt  = _get_twitter_sentiment(sym)
    # média simples das fontes disponíveis
    parts = []
    if n_news >= 0: parts.append(s_news)
    if n_twt  >= 0: parts.append(s_twt)
    s = sum(parts)/len(parts) if parts else 0.5
    return (
        max(0.0, min(1.0, s)),
        {"news": {"score": s_news, "n": n_news}, "twitter": {"score": s_twt, "n": n_twt}}
    )

# ========================
# Cache do ciclo
# ========================
def _save_cycle_cache(data_map: Dict[str, List[Dict[str,float]]]):
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": list(data_map.keys()), "data": data_map}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(data_map)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

# ========================
# Telegram
# ========================
def _notify_signal(sym: str, sig: Dict[str,Any], tech_score: float, sent_score: float, mix: float):
    if _notifier is None:
        return
    try:
        # Garantir valores em USD bonitos
        entry = float(sig.get("entry", 0.0))
        tp    = float(sig.get("tp", 0.0))
        sl    = float(sig.get("sl", 0.0))
        rr    = sig.get("rr", None)
        confidence = float(sig.get("confidence", mix*100.0))
        strategy   = sig.get("strategy_name", "modelo misto")

        _notifier.send_signal(
            symbol=sym,
            entry=entry,
            tp=tp,
            sl=sl,
            rr=rr,
            confidence=confidence,
            strategy=strategy,
            created_at=_ts(),
            extra={
                "tech_score": round(tech_score*100,1),
                "sent_score": round(sent_score*100,1),
                "mix_score":  round(mix*100,1),
            }
        )
    except Exception:
        print("⚠️ Falha ao notificar Telegram:")
        traceback.print_exc()

# ========================
# RUN
# ========================
def run_pipeline():
    print(f"▶️ Runner iniciado. Intervalo = {INTERVAL_MIN:.1f} min.")

    # Banner de status
    news_on = NEWS_USE and NEWS_MONTHLY_BUDGET > 0 and NEWS_CALLS_PER_CYCLE_MAX > 0
    print("🔎 NEWS ativo?: {0} | IA ativa?: {1} | Histórico ativado?: {2} | Twitter ativo?: {3}"
          .format(_yes(news_on), _yes(USE_AI), _yes(SAVE_HISTORY), _yes(TWITTER_USE)))

    # Runtime sentimento (se existir) + orçamento News
    try:
        init_sentiment_runtime()
        rt = _sent_rt()
        rt.new_cycle()
        st = rt.status() or {}
        used  = st.get("month_used")
        bud   = st.get("month_budget")
        calls = st.get("cycle_calls")
        if used is not None and bud is not None:
            print(f"🧮 News orçamento: {used}/{bud} usadas no mês | chamadas neste ciclo: {calls if calls is not None else 0}")
    except Exception:
        pass

    # Seleciona lote
    batch = _pick_batch()
    if not batch:
        print("❌ Nenhum símbolo definido.")
        return

    print(f"🧪 Moedas deste ciclo ({len(batch)}/{len(_symbols_env)}): {', '.join(batch)}")

    collected: Dict[str, List[Dict[str,float]]] = {}
    for sym in batch:
        try:
            bars = _collect_ohlc_symbol(sym, DAYS_OHLC, MIN_BARS)
            if bars:
                collected[sym] = bars
                if SAVE_HISTORY:
                    append_ohlc_snapshot(sym, bars, HISTORY_DIR)
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not collected:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    _save_cycle_cache(collected)

    # Scoring + Sinal
    signals_saved = 0
    for sym, bars in collected.items():
        # Técnico
        tscore = 0.0
        try:
            tscore_raw = score_signal(bars)
            if isinstance(tscore_raw, dict):
                tscore = float(tscore_raw.get("score", tscore_raw.get("value", 0.0)))
            else:
                tscore = float(tscore_raw)
            if tscore > 1.0: tscore /= 100.0
            tscore = max(0.0, min(1.0, tscore))
        except Exception:
            tscore = 0.0

        # Sentimento combinado
        sscore, sdetail = _mix_sentiment(sym)

        # Mistura
        mix = (MIX_T_WEIGHT * tscore + MIX_S_WEIGHT * sscore) / (MIX_T_WEIGHT + MIX_S_WEIGHT)

        # Log detalhado dos indicadores técnicos (se veio no score_signal em dict)
        try:
            # Alguns dos seus módulos imprimem os indicadores no próprio score;
            # aqui mantemos uma linha compacta:
            print(f"[IND] {sym} | Técnico: {round(tscore*100,1)}% | Sentimento: {round(sscore*100,1)}% (news n={sdetail['news']['n']}, tw n={sdetail['twitter']['n']}) | Mix(T:{MIX_T_WEIGHT},S:{MIX_S_WEIGHT}): {round(mix*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")
        except Exception:
            pass

        # Decisão de sinal
        try:
            if mix >= SCORE_THRESHOLD:
                sig = generate_signal(bars)
                if isinstance(sig, dict):
                    _notify_signal(sym, sig, tscore, sscore, mix)
                    signals_saved += 1
        except Exception:
            traceback.print_exc()

    print(f"🗂 {signals_saved} sinais salvos em signals.json")
    print(f"🕒 Fim: {_ts()}")


if __name__ == "__main__":
    try:
        run_pipeline()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
