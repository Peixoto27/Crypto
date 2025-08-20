# -*- coding: utf-8 -*-
"""
main.py — pipeline estável (CoinGecko-only) + sentimento + notifier

Requisitos:
- cg_ids.json com mapeamentos { "BTCUSDT": "bitcoin", ... }
- data_fetcher_coingecko.py (fornecido)
- (opcional) apply_strategies.py   -> score_signal(past_ohlc_dicts) OU score_from_indicators(...)
- (opcional) sentiment_analyzer.py -> get_sentiment_for_symbol(symbol)
- (opcional) notifier_v2.py        -> send_signal_notification(signal_dict)

Saídas:
- data_raw.json com OHLCs do ciclo
- logs de indicadores/sentimento/mix
"""

from __future__ import annotations
import os, json, time, sys
from datetime import datetime
from typing import Dict, Any, List, Tuple

# ---------- Utils ----------
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _get(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    return v if v is not None else default

def _to_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")

def _to_float(v: str, d: float) -> float:
    try:
        return float(str(v).strip()) if str(v).strip() != "" else d
    except Exception:
        return d

def _norm_rows_to_dicts(rows: List[List[float]]) -> List[Dict[str, float]]:
    out = []
    for r in rows or []:
        if len(r) >= 5:
            out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])})
    return out

def _print_flags():
    print(f"🔎 NEWS ativo?: {_to_bool(_get('NEWS_USE','true'))} | IA ativa?: {str(_to_bool(_get('AI_USE','true'))).lower()} | Histórico ativado?: {_to_bool(_get('SAVE_HISTORY','true'))} | Twitter ativo?: {_to_bool(_get('TWITTER_USE','true'))}")

# ---------- Imports opcionais ----------
try:
    from data_fetcher_coingecko import fetch_ohlc, norm_rows
except Exception as e:
    print("❌ data_fetcher_coingecko não disponível:", e)
    fetch_ohlc = None
    norm_rows = lambda x: []

try:
    # seu módulo de estratégias/indicadores
    from apply_strategies import score_signal, score_from_indicators, compute_indicators  # type: ignore
except Exception:
    score_signal = None
    score_from_indicators = None
    compute_indicators = None

try:
    # seu módulo de sentimento (news/twitter)
    from sentiment_analyzer import get_sentiment_for_symbol  # type: ignore
except Exception:
    get_sentiment_for_symbol = None  # type: ignore

try:
    # seu notifier (Telegram)
    from notifier_v2 import send_signal_notification  # type: ignore
except Exception:
    send_signal_notification = None  # type: ignore

# ---------- Coleta OHLC (CoinGecko only) ----------
def collect_ohlc_for(symbols: List[str], days: int = 30, min_bars: int = 60) -> Dict[str, List[List[float]]]:
    collected: Dict[str, List[List[float]]] = {}
    for sym in symbols:
        print(f"📊 Coletando OHLC {sym} (days={days})…")
        if fetch_ohlc is None:
            print(f"⚠️ {sym}: fetch_ohlc indisponível")
            continue
        try:
            rows = fetch_ohlc(sym, days=days)
            bars = norm_rows(rows)
            if len(bars) < min_bars:
                print(f"⚠️ {sym}: OHLC insuficiente ({len(bars)}/{min_bars})")
                continue
            collected[sym] = bars[-min_bars:]  # mantém janela mínima
            print(f"   → OK | candles= {len(bars)}")
        except Exception as e:
            print(f"⚠️ CoinGecko falhou {sym}: {e}")
            print(f"⚠️ {sym}: OHLC insuficiente (0/{min_bars})")
    return collected

# ---------- Sentimento (robusto a variações de retorno) ----------
def safe_get_sentiment(symbol: str) -> Dict[str, Any]:
    """
    Normaliza o retorno do seu sentiment_analyzer:
    aceita dict, tuple, float/int, None.
    """
    if get_sentiment_for_symbol is None:
        return {"score": 0.5, "news_n": 0, "tw_n": 0}

    try:
        raw = get_sentiment_for_symbol(symbol)  # NÃO passar kwargs (para evitar erros de assinatura)
        # normalizações
        if isinstance(raw, dict):
            score = raw.get("score", raw.get("value", raw.get("sentiment", 0.5)))
            news_n = raw.get("news_n", raw.get("news_count", 0))
            tw_n   = raw.get("tw_n", raw.get("twitter_count", 0))
            return {
                "score": float(score) if score is not None else 0.5,
                "news_n": int(news_n) if news_n is not None else 0,
                "tw_n": int(tw_n) if tw_n is not None else 0
            }
        if isinstance(raw, tuple) and len(raw) >= 1:
            # (score, news_n?, tw_n?)
            score = raw[0]
            news_n = raw[1] if len(raw) > 1 else 0
            tw_n   = raw[2] if len(raw) > 2 else 0
            return {"score": float(score), "news_n": int(news_n), "tw_n": int(tw_n)}
        if isinstance(raw, (int, float)):
            return {"score": float(raw), "news_n": 0, "tw_n": 0}
        # fallback
        return {"score": 0.5, "news_n": 0, "tw_n": 0}
    except Exception as e:
        print(f"[SENT] erro {symbol}: {e}")
        return {"score": 0.5, "news_n": 0, "tw_n": 0}

# ---------- Técnico (robusto a variações) ----------
def safe_score_tech(past_ohlc_dicts: List[Dict[str, float]]) -> float:
    """
    Tenta usar score_signal(past) ou score_from_indicators/compute_indicators se existir.
    Garante retorno 0.0..1.0
    """
    # prioridade: score_signal
    try:
        if score_signal:
            s = score_signal(past_ohlc_dicts)
            if isinstance(s, dict):
                val = s.get("score", s.get("value", 0.0))
            elif isinstance(s, (tuple, list)):
                val = s[0]
            else:
                val = s
            val = float(val)
            if val > 1.0:  # alguns retornam 0..100
                val /= 100.0
            return max(0.0, min(1.0, val))
    except Exception as e:
        print(f"[TECH] erro em score_signal: {e}")

    # alternativa: compute_indicators -> score_from_indicators
    try:
        if compute_indicators and score_from_indicators:
            ind = compute_indicators(past_ohlc_dicts)
            val = score_from_indicators(ind)
            if isinstance(val, dict):
                val = val.get("score", 0.0)
            if isinstance(val, (tuple, list)):
                val = val[0]
            val = float(val)
            if val > 1.0:
                val /= 100.0
            return max(0.0, min(1.0, val))
    except Exception as e:
        print(f"[TECH] erro em compute_indicators/score_from_indicators: {e}")

    return 0.0

# ---------- Mix de scores ----------
def mix_scores(tech: float, sent: float, wt: float, ws: float) -> float:
    try:
        return (tech * wt + sent * ws) / (wt + ws)
    except Exception:
        return 0.0

# ---------- Sinais + Notifier ----------
def maybe_emit_signal(symbol: str, mix_score: float, last_price: float, thr: float) -> None:
    if mix_score < thr:
        return
    signal = {
        "id": f"sig-{int(time.time())}",
        "symbol": symbol,
        "entry": last_price,
        # TP/SL básicos (ajuste conforme seu generate_signal se quiser)
        "tp": round(last_price * 1.02, 8),
        "sl": round(last_price * 0.99, 8),
        "rr": 2.0,
        "confidence": round(mix_score * 100.0, 2),
        "strategy": "TECH+NEWS/TW MIX",
        "created_at": _ts()
    }
    msg = (
        f"📢 **Novo sinal** para **{symbol}**\n"
        f"🎯 **Entrada:** {signal['entry']}\n"
        f"🎯 **Alvo:**   {signal['tp']}\n"
        f"🛑 **Stop:**   {signal['sl']}\n"
        f"📊 **R:R:** {signal['rr']}\n"
        f"📈 **Confiança:** {signal['confidence']}%\n"
        f"🧠 **Estratégia:** {signal['strategy']}\n"
        f"📅 **Criado:** {signal['created_at']}\n"
        f"🆔 **ID:** {signal['id']}"
    )
    print(msg)

    if send_signal_notification:
        try:
            send_signal_notification(signal)  # seu notifier_v2
        except Exception as e:
            print(f"⚠️ Notifier falhou: {e}")

# ---------- Pipeline ----------
def run_pipeline():
    interval_min = int(_get("INTERVAL_MIN", "20"))
    days = int(_get("DAYS_OHLC", "30"))
    min_bars = int(_get("MIN_BARS", "180"))
    thr = _to_float(_get("SCORE_THRESHOLD", "0.70"), 0.70)

    WEIGHT_TECH = _to_float(_get("WEIGHT_TECH", "1.5"), 1.5)
    WEIGHT_SENT = _to_float(_get("WEIGHT_SENT", "1.0"), 1.0)

    # lista de símbolos (ENV) — se vazio, usa um conjunto básico
    syms_env = [s.strip().upper() for s in _get("SYMBOLS", "").split(",") if s.strip()]
    if not syms_env:
        syms_env = ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]

    # remove estáveis duplicadas/ruins (ex.: FDUSDUSDT etc.)
    stable_redundant = {"FDUSDUSDT","USDCUSDT","TUSDUSDT","USDPUSDT","DAIUSDT"}
    before = len(syms_env)
    syms = [s for s in syms_env if s not in stable_redundant]
    removed = before - len(syms)
    if removed > 0:
        print(f"🧠 Removidos {removed} pares estáveis redundantes (ex.: FDUSDUSDT).")

    _print_flags()
    print(f"🧪 Moedas deste ciclo ({min(len(syms), len(syms))}/{len(syms)}): {', '.join(syms[:30])}{'…' if len(syms)>30 else ''}")

    start = time.time()

    # 1) Coleta
    data = collect_ohlc_for(syms, days=days, min_bars=min_bars)

    # 2) Persistência data_raw.json
    try:
        with open(_get("DATA_RAW_FILE","data_raw.json"), "w", encoding="utf-8") as f:
            json.dump({"symbols": list(data.keys()), "data": data}, f, ensure_ascii=False)
        print(f"💾 Salvo data_raw.json ({len(data)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar data_raw.json: {e}")

    # 3) Avaliação técnica + sentimento + mix
    for sym, bars in data.items():
        dicts = _norm_rows_to_dicts(bars)
        last_price = dicts[-1]["c"] if dicts else 0.0

        tech = safe_score_tech(dicts)
        sent = safe_get_sentiment(sym)

        sent_score = float(sent.get("score", 0.5))
        news_n = int(sent.get("news_n", 0))
        tw_n   = int(sent.get("tw_n", 0))

        mix = mix_scores(tech, sent_score, WEIGHT_TECH, WEIGHT_SENT)

        # Logs estilo que você usa
        print(f"[IND] {sym} | Técnico: {round(tech*100,1)}% | Sentimento: {round(sent_score*100,1)}% (news n={news_n}, tw n={tw_n}) | Mix(T:{WEIGHT_TECH},S:{WEIGHT_SENT}): {round(mix*100,1)}% (min {int(thr*100)}%)")

        # 4) Emite sinal se bater threshold
        maybe_emit_signal(sym, mix, last_price, thr)

    end = time.time()
    print(f"🕒 Fim: {_ts()}")
    print(f"✅ Ciclo concluído em {int(end-start)}s. Próxima execução")

# ---------- Entry ----------
if __name__ == "__main__":
    run_pipeline()
