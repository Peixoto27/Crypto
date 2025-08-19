# -*- coding: utf-8 -*-
"""
main.py — pipeline robusto com:
- coleta OHLC tolerante a erros (timeout + retentativas)
- score técnico com guard (sem NoneType/float errors)
- sentimento (news/twitter) opcionais
- mix técnico+sentimento
- geração de sinais + notificação opcional
- salvamento de data_raw.json
- run_pipeline() exportado (usado pelo runner)

Requerimentos externos (opcionais, com fallback seguro se ausentes):
- data_fetcher_coingecko.fetch_ohlc
- apply_strategies.score_signal / generate_signal
- news_fetcher.SentimentRuntime (para NewsData)
- notifier_v2.notify_signal ou notifier_telegram.send_signal
- history_manager (já é usado pelo seu projeto)

Variáveis de ambiente úteis (todas opcionais):
  INTERVAL_MIN=20
  DAYS_OHLC=30
  MIN_BARS=180
  CYCLE_BATCH=8               # qtos símbolos por ciclo
  SYMBOLS=BTCUSDT,ETHUSDT,... # se vazio, usa TOP 90 do projeto
  WEIGHT_TECH=1.0
  WEIGHT_SENT=0.5
  SCORE_THRESHOLD=0.7         # para salvar sinais
  # Coleta robusta
  FETCH_TIMEOUT=25
  FETCH_MAX_RETRY=3
  FETCH_SLEEP_BETWEEN=1.5
  # News
  NEWS_API_KEY=...
  NEWS_USE=true
  # Twitter
  TWITTER_USE=false
  TWITTER_BEARER_TOKEN=...
  # Histórico
  SAVE_HISTORY=true
  HISTORY_DIR=data/history
  DATA_RAW_FILE=data_raw.json
  # Notifier
  NOTIFY_USE=true
"""

from __future__ import annotations
import os, json, time, math
from typing import Dict, Any, List, Tuple
from datetime import datetime

# =========================
# Helpers de ambiente/log
# =========================
def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _log(msg: str):
    print(msg, flush=True)

# =========================
# Imports opcionais do projeto
# =========================
# Coleta OHLC
try:
    from data_fetcher_coingecko import fetch_ohlc  # deve honrar timeout de requests
except Exception:
    fetch_ohlc = None

# Estratégias / score técnico / geração de sinal
try:
    from apply_strategies import score_signal, generate_signal
except Exception:
    score_signal = None
    generate_signal = None

# Sentimento (News)
try:
    from news_fetcher import SentimentRuntime as NewsRuntime
except Exception:
    NewsRuntime = None

# Twitter sentiment (se você tiver módulo próprio; se não, ficamos no stub)
try:
    from twitter_sentiment import TwitterRuntime
except Exception:
    TwitterRuntime = None

# Histórico (opcional)
try:
    from history_manager import HistoryManager
except Exception:
    HistoryManager = None

# Notifier (opcional)
_notify_func = None
try:
    from notifier_v2 import notify_signal as _notify_v2
    _notify_func = _notify_v2
except Exception:
    try:
        from notifier_telegram import send_signal as _notify_tg
        _notify_func = _notify_tg
    except Exception:
        _notify_func = None

# =========================
# Normalização OHLC
# =========================
def _norm_ohlc(rows: List) -> List[Dict[str, float]]:
    out = []
    if not rows:
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        # formato [ts, o, h, l, c]
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

# =========================
# Coleta robusta por símbolo
# =========================
FETCH_TIMEOUT       = float(_get_env("FETCH_TIMEOUT",       "25"))
FETCH_MAX_RETRY     = int(_get_env("FETCH_MAX_RETRY",       "3"))
FETCH_SLEEP_BETWEEN = float(_get_env("FETCH_SLEEP_BETWEEN", "1.5"))

def _try_fetch_one(symbol: str, days: int) -> Tuple[bool, List[Dict[str, float]]|None]:
    if fetch_ohlc is None:
        _log(f"⚠️ {symbol}: fetch_ohlc não disponível")
        return False, None
    attempt = 0
    while attempt < FETCH_MAX_RETRY:
        attempt += 1
        start = time.perf_counter()
        try:
            rows = fetch_ohlc(symbol, days)  # sua função já usa timeout de requests
            took = time.perf_counter() - start
            if took > (FETCH_TIMEOUT + 2):
                _log(f"⚠️ {symbol}: tempo excedido ({took:.1f}s). Tentativa {attempt}/{FETCH_MAX_RETRY}")
                raise TimeoutError("fetch timeout hard-guard")
            bars = _norm_ohlc(rows)
            if len(bars) < 3:
                _log(f"⚠️ {symbol}: OHLC vazio/curto. Tentativa {attempt}/{FETCH_MAX_RETRY}")
                raise ValueError("empty ohlc")
            return True, bars
        except Exception as e:
            _log(f"⚠️ {symbol}: erro na coleta ({type(e).__name__}: {e}). "
                 f"Tentativa {attempt}/{FETCH_MAX_RETRY}")
            if attempt < FETCH_MAX_RETRY:
                time.sleep(FETCH_SLEEP_BETWEEN)
    return False, None

# =========================
# Sentimento (News + Twitter)
# =========================
def _setup_news_runtime():
    use = _get_env("NEWS_USE", "true").lower() in ("1","true","yes")
    key = _get_env("NEWS_API_KEY", "")
    if not use or not key or NewsRuntime is None:
        return None
    try:
        rt = NewsRuntime(api_key=key)
        return rt
    except Exception as e:
        _log(f"⚠️ News runtime indisponível: {e}")
        return None

def _score_news(rt, symbol: str) -> Tuple[float, int]:
    # retorna (score 0..1, n_artigos)
    if rt is None:
        return 0.5, 0
    try:
        res = rt.score_for_symbol(symbol)
        if isinstance(res, dict):
            s = float(res.get("score", 0.5)); n = int(res.get("n", 0))
        elif isinstance(res, tuple) and len(res)>=2:
            s = float(res[0]); n = int(res[1])
        else:
            s, n = 0.5, 0
        s = max(0.0, min(1.0, s))
        return s, n
    except Exception as e:
        _log(f"⚠️ News score falhou {symbol}: {e}")
        return 0.5, 0

def _setup_twitter_runtime():
    use = _get_env("TWITTER_USE", "false").lower() in ("1","true","yes")
    bearer = _get_env("TWITTER_BEARER_TOKEN", "")
    if not use or not bearer:
        return None
    if TwitterRuntime is None:
        _log("ℹ️ Twitter runtime não encontrado (TwitterRuntime ausente).")
        return None
    try:
        rt = TwitterRuntime(bearer_token=bearer)
        return rt
    except Exception as e:
        _log(f"⚠️ Twitter runtime indisponível: {e}")
        return None

def _score_twitter(rt, symbol: str) -> Tuple[float, int]:
    if rt is None:
        return 0.5, 0
    try:
        res = rt.score_for_symbol(symbol)
        if isinstance(res, dict):
            s = float(res.get("score", 0.5)); n = int(res.get("n", 0))
        elif isinstance(res, tuple) and len(res)>=2:
            s = float(res[0]); n = int(res[1])
        else:
            s, n = 0.5, 0
        s = max(0.0, min(1.0, s))
        return s, n
    except Exception as e:
        _log(f"⚠️ Twitter score falhou {symbol}: {e}")
        return 0.5, 0

# =========================
# Score técnico seguro
# =========================
def _safe_score_tech(bars: List[Dict[str, float]]) -> float:
    if not bars or len(bars) < 3:
        return 0.0
    try:
        if score_signal is None:
            return 0.0
        s = score_signal(bars)
        # pode ser float, dict, tupla…
        if isinstance(s, dict):
            s = float(s.get("score", s.get("value", 0.0)))
        elif isinstance(s, tuple):
            s = float(s[0])
        else:
            s = float(s)
        if s > 1.0:  # caso score 0..100
            s = s / 100.0
        return max(0.0, min(1.0, s))
    except Exception as e:
        _log(f"[IND] erro em score_signal: {e}")
        return 0.0

# =========================
# Mix e geração de sinal
# =========================
WEIGHT_TECH = float(_get_env("WEIGHT_TECH", "1.0"))
WEIGHT_SENT = float(_get_env("WEIGHT_SENT", "0.5"))
SCORE_THRESHOLD = float(_get_env("SCORE_THRESHOLD", "0.7"))

def _mix_score(tech: float, sent: float) -> float:
    # tech e sent em 0..1 -> retorna 0..1
    num = WEIGHT_TECH * tech + WEIGHT_SENT * sent
    den = WEIGHT_TECH + WEIGHT_SENT
    if den <= 0:
        return 0.0
    return max(0.0, min(1.0, num / den))

# =========================
# Símbolos do ciclo
# =========================
DEFAULT_UNIVERSE = [
    "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
    "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT","UNIUSDT",
    "BCHUSDT","ETCUSDT","APTUSDT","IMXUSDT","FILUSDT","NEARUSDT","OPUSDT","XLMUSDT"
]

DAYS_OHLC   = int(_get_env("DAYS_OHLC", "30"))
MIN_BARS    = int(_get_env("MIN_BARS", "180"))
CYCLE_BATCH = int(_get_env("CYCLE_BATCH", "8"))

def _pick_cycle(symbols: List[str], k: int) -> List[str]:
    # pega de forma determinística os primeiros k do universo
    return symbols[:k]

# =========================
# Save data_raw
# =========================
DATA_RAW_FILE = _get_env("DATA_RAW_FILE", "data_raw.json")
def _save_data_raw(collected: Dict[str, List[Dict[str, float]]]):
    obj = {
        "saved_at": _ts(),
        "symbols": list(collected.keys()),
        "data": {s: [[b["t"], b["o"], b["h"], b["l"], b["c"]] for b in bars] for s, bars in collected.items()}
    }
    with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)

# =========================
# Pipeline
# =========================
def run_pipeline():
    _log("Starting Container")
    interval_min = float(_get_env("INTERVAL_MIN", "20"))
    _log(f"▶️ Runner iniciado. Intervalo = {interval_min:.1f} min.")

    # toggles
    news_rt   = _setup_news_runtime()
    tw_rt     = _setup_twitter_runtime()
    ia_active = True  # seu gerenciador de IA/feature pode trocar isso
    save_hist = _get_env("SAVE_HISTORY", "true").lower() in ("1","true","yes")

    _log(f"🔎 NEWS ativo?: {bool(news_rt)} | IA ativa?: {ia_active} | "
         f"Histórico ativado?: {save_hist} | Twitter ativo?: {bool(tw_rt)}")

    # universo
    symbols_env = [s for s in _get_env("SYMBOLS", "").replace(" ","").split(",") if s]
    universe = symbols_env if symbols_env else DEFAULT_UNIVERSE[:]
    # remove pares estáveis redundantes
    stable = {"FDUSDUSDT","USDCUSDT","USDTBRL","BUSDUSDT"}
    before = len(universe)
    universe = [s for s in universe if s not in stable]
    removed = before - len(universe)
    if removed > 0:
        _log(f"🧠 Removidos {removed} pares estáveis redundantes (ex.: FDUSDUSDT).")

    cycle = _pick_cycle(universe, min(CYCLE_BATCH, len(universe)))
    if not cycle:
        _log("❌ Nenhum ativo para varredura.")
        return

    _log(f"🧪 Moedas deste ciclo ({len(cycle)}/{len(universe)}): {', '.join(cycle)}")

    # coleta OHLC
    collected: Dict[str, List[Dict[str, float]]] = {}
    ok_syms = []
    for idx, sym in enumerate(cycle, 1):
        _log(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        ok, bars = _try_fetch_one(sym, DAYS_OHLC)
        if not ok or not bars:
            _log(f"❌ Erro OHLC {sym}: dados indisponíveis")
            continue
        if len(bars) < MIN_BARS:
            _log(f"❌ {sym}: dados insuficientes ({len(bars)}/{MIN_BARS})")
            continue
        collected[sym] = bars
        ok_syms.append(sym)
        _log(f"   → OK | candles={len(bars)}")

    if not ok_syms:
        _log("❌ Nenhum ativo com OHLC suficiente.")
        return

    # salva data_raw
    try:
        _save_data_raw(collected)
        _log(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_syms)} ativos)")
    except Exception as e:
        _log(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # Histórico (opcional)
    hm = None
    if save_hist and HistoryManager is not None:
        try:
            hist_dir = _get_env("HISTORY_DIR", "data/history")
            hm = HistoryManager(hist_dir)
        except Exception as e:
            _log(f"⚠️ HistoryManager indisponível: {e}")

    # cálculo de scores e sinais
    signals = []
    for sym in ok_syms:
        bars = collected[sym]
        close = bars[-1]["c"]
        tech = _safe_score_tech(bars)
        news_s, n_news = _score_news(news_rt, sym)
        tw_s, n_tw = _score_twitter(tw_rt, sym)
        sent = (news_s + tw_s) / 2.0
        mix = _mix_score(tech, sent)

        # log detalhado
        _log(f"[IND] {sym} | Técnico: {tech*100:.1f}% | Sentimento: {sent*100:.1f}% "
             f"(news n={n_news}, tw n={n_tw}) | Mix(T:{WEIGHT_TECH:.1f},S:{WEIGHT_SENT:.1f}): "
             f"{mix*100:.1f}% (min {SCORE_THRESHOLD*100:.0f}%)")

        # history save
        if hm is not None:
            try:
                hm.append_score(sym, {
                    "time": _ts(),
                    "close": close,
                    "tech": tech,
                    "sent": sent,
                    "mix": mix,
                    "n_news": n_news,
                    "n_tw": n_tw
                })
            except Exception as e:
                _log(f"⚠️ erro ao salvar histórico {sym}: {e}")

        # gerar sinal se mix >= threshold
        if mix >= SCORE_THRESHOLD and generate_signal is not None:
            try:
                sig = generate_signal(bars)
                if isinstance(sig, dict):
                    sig["symbol"] = sym
                    sig["score_mix"] = round(mix, 4)
                    sig["created_at"] = _ts()
                    signals.append(sig)
            except Exception as e:
                _log(f"⚠️ erro ao gerar sinal {sym}: {e}")

    # salvar signals.json
    try:
        with open("signals.json", "w", encoding="utf-8") as f:
            json.dump({"created_at": _ts(), "signals": signals}, f, ensure_ascii=False, indent=2)
        _log(f"🗂 {len(signals)} sinais salvos em signals.json")
    except Exception as e:
        _log(f"⚠️ Falha ao salvar signals.json: {e}")

    # Notificação (opcional)
    if signals and _notify_func and _get_env("NOTIFY_USE", "true").lower() in ("1","true","yes"):
        for s in signals:
            try:
                _notify_func(s)  # notifier_v2 ou telegram
            except Exception as e:
                _log(f"⚠️ erro ao notificar {s.get('symbol')}: {e}")

    _log(f"🕒 Fim: {_ts()}")

# =========================
# Execução direta
# =========================
if __name__ == "__main__":
    run_pipeline()
