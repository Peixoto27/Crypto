# -*- coding: utf-8 -*-
"""
main.py
- Orquestra coleta de OHLC, cálculo técnico, sentimento (news/twitter) e mistura.
- Usa data_fetcher_binance.fetch_ohlc (com fallback interno p/ CoinGecko).
- Salva data_raw.json.
- Expõe run_pipeline() (chamado pelo runner.py).

Requer módulos existentes no seu projeto:
  - apply_strategies.py  -> score_signal(ohlc_slice)  (retorna float ou dict)
  - sentiment_analyzer.py -> get_sentiment_for_symbol(symbol) (dict ou tuple)
  - news_fetcher.py (opcional dentro do sentiment_analyzer)
  - notifier_* (opcional)

Formatação de OHLC esperada (lista): [[ts_ms,o,h,l,c], ...]
"""

import os
import json
from datetime import datetime
from typing import Dict, Any, List, Tuple

from data_fetcher_binance import fetch_ohlc

# módulos do projeto
try:
    from apply_strategies import score_signal
except Exception:
    score_signal = None

try:
    from sentiment_analyzer import get_sentiment_for_symbol
except Exception:
    get_sentiment_for_symbol = None


# ============== Utils/ENV ==============

def _bool_env(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return str(v).lower() in ("1", "true", "yes", "on")

def _get_env(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or v == "" else v

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _log(msg: str):
    print(msg, flush=True)


# ============== Scoring helpers ==============

def _safe_score_tech(ohlc_rows: List[List[float]]) -> float:
    if not score_signal or not ohlc_rows:
        return 0.0
    try:
        s = score_signal(ohlc_rows)
        # pode vir dict, tuple ou float
        if isinstance(s, dict):
            s = float(s.get("score", s.get("value", 0.0)))
        elif isinstance(s, tuple):
            s = float(s[0])
        else:
            s = float(s)
        if s > 1.0:  # caso % 0..100
            s /= 100.0
        return max(0.0, min(1.0, s))
    except Exception as e:
        _log(f"[IND] erro em score_signal: {e}")
        return 0.0

def _safe_sent(symbol: str) -> Tuple[float, int, int]:
    """
    Retorna (score_sent (0..1), news_n, tw_n)
    Aceita dict {'score':..., 'news_n':..., 'tw_n':...} ou tuple.
    """
    use_news   = _bool_env("NEWS_USE", True)
    use_tweet  = _bool_env("TWITTER_USE", False)

    if not get_sentiment_for_symbol or (not use_news and not use_tweet):
        return (0.5, 0, 0)  # neutro

    try:
        r = get_sentiment_for_symbol(symbol)
        # Formatos suportados
        if isinstance(r, dict):
            s  = float(r.get("score", 0.5))
            nn = int(r.get("news_n", 0))
            tn = int(r.get("tw_n", 0))
        elif isinstance(r, tuple):
            # tenta (score, news_n, tw_n)
            if len(r) >= 3:
                s, nn, tn = r[0], r[1], r[2]
            elif len(r) == 2:
                s, nn = r[0], r[1]
                tn = 0
            else:
                s, nn, tn = r[0], 0, 0
            s = float(s)
            nn = int(nn)
            tn = int(tn)
        else:
            s, nn, tn = 0.5, 0, 0

        if s > 1.0:
            s /= 100.0
        s = max(0.0, min(1.0, s))
        return (s, nn, tn)
    except TypeError as te:
        # caso alguém tenha adicionado argumentos não aceitos
        _log(f"[SENT] erro {symbol}: {te}")
        return (0.5, 0, 0)
    except Exception as e:
        _log(f"[SENT] erro {symbol}: {e}")
        return (0.5, 0, 0)


# ============== Universo de símbolos ==============

def _stable_pairs() -> List[str]:
    # Pode ampliar conforme necessário
    return ["FDUSDUSDT", "BUSDUSDT", "USDCUSDT", "USDPUSDT", "TUSDUSDT"]

def _universe() -> List[str]:
    syms = [s for s in _get_env("SYMBOLS", "").replace(" ", "").split(",") if s]
    if syms:
        return syms
    # Padrão (top 30 comuns)
    return [
        "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
        "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT","UNIUSDT",
        "BCHUSDT","ETCUSDT","APTUSDT","IMXUSDT","FILUSDT","NEARUSDT","OPUSDT","XLMUSDT",
        "HBARUSDT","INJUSDT","ARBUSDT","LDOUSDT","ATOMUSDT","STXUSDT"
    ][:int(_get_env("TOP_SYMBOLS","30"))]


# ============== RUN ==============

def run_pipeline():
    interval_min = float(_get_env("RUN_EVERY_MIN", "20"))
    news_on      = _bool_env("NEWS_USE", True)
    ai_on        = _bool_env("AI_USE", True)  # placeholder p/ seu modelo
    hist_on      = _bool_env("SAVE_HISTORY", True)
    tw_on        = _bool_env("TWITTER_USE", False)

    _log("Starting Container")
    _log(f"▶️ Runner iniciado. Intervalo = {interval_min:.1f} min.")
    _log(f"🔎 NEWS ativo?: {news_on} | IA ativa?: {ai_on} | Histórico ativado?: {hist_on} | Twitter ativo?: {tw_on}")

    # Universo e remoção de estáveis redundantes
    universe = _universe()
    stables = _stable_pairs()
    removed = [s for s in universe if s in stables]
    universe = [s for s in universe if s not in stables]
    if removed:
        _log(f"🧠 Removidos {len(removed)} pares estáveis redundantes (ex.: {removed[0]}).")

    top_n = min(len(universe), int(_get_env("BATCH_SYMBOLS","30")))
    work = universe[:top_n]
    _log(f"🧪 Moedas deste ciclo ({len(work)}/{len(universe)}): {', '.join(work)}")

    # Parâmetros OHLC
    days     = int(_get_env("DAYS_OHLC", "30"))
    min_bars = int(_get_env("MIN_BARS", "60"))

    # Coleta
    all_data: Dict[str, List[List[float]]] = {}
    for s in work:
        rows = fetch_ohlc(s, days=days, min_bars=min_bars, interval="1h")
        if len(rows) >= min_bars:
            _log(f"   → OK | candles= {len(rows)}")
        else:
            _log(f"⚠️ {s}: OHLC insuficiente ({len(rows)}/{min_bars})")
        all_data[s] = rows

    # Salvar data_raw.json
    saved = [k for k,v in all_data.items() if len(v) >= min_bars]
    try:
        with open("data_raw.json", "w", encoding="utf-8") as f:
            json.dump({"created_at": _ts(), "data": all_data}, f, ensure_ascii=False, indent=2)
        _log(f"💾 Salvo data_raw.json ({len(saved)} ativos)")
    except Exception as e:
        _log(f"⚠️ Erro ao salvar data_raw.json: {e}")

    # Pesos de mistura
    wT = float(_get_env("WEIGHT_TECH", "1.5"))
    wS = float(_get_env("WEIGHT_SENT", "1.0"))
    thr = float(_get_env("SCORE_THRESHOLD","0.70"))

    # Scoring por símbolo
    for s in work:
        ohlc = all_data.get(s, [])
        tscore = _safe_score_tech(ohlc) if len(ohlc) >= min_bars else 0.0
        sent_s, news_n, tw_n = _safe_sent(s)  # neutral 0.5 se desativado
        mix = (wT*tscore + wS*sent_s) / (wT + wS)
        # prints
        t_pct = int(round(tscore*100))
        s_pct = int(round(sent_s*100))
        mix_pct = int(round(mix*100))
        _log(f"[IND] {s} | Técnico: {t_pct:.1f}% | Sentimento: {s_pct:.1f}% (news n={news_n}, tw n={tw_n}) | Mix(T:{wT},S:{wS}): {mix_pct:.1f}% (min {int(thr*100)}%)")

    _log(f"🕒 Fim: {_ts()}")


if __name__ == "__main__":
    run_pipeline()
