# -*- coding: utf-8 -*-
"""
main.py — Pipeline estável (CoinGecko only) com técnico EMBUTIDO + sentimento + notifier

- Coleta OHLC via CoinGecko (data_fetcher_coingecko.py)
- Se apply_strategies existir, usa score_signal/compute_indicators.
- Se NÃO existir, usa um técnico básico embutido (EMA/RSI/MACD/BB/Stoch) -> score 0..1.
- Integra sentimento (sentiment_analyzer.get_sentiment_for_symbol se existir).
- Emite sinal via notifier_v2.send_signal_notification se existir.
- Salva data_raw.json

ENV úteis:
  SYMBOLS (csv) | DAYS_OHLC=30 | MIN_BARS=120 | SCORE_THRESHOLD=0.70
  WEIGHT_TECH=1.5 | WEIGHT_SENT=1.0
  NEWS_USE=true | AI_USE=true | SAVE_HISTORY=true | TWITTER_USE=true
  DATA_RAW_FILE=data_raw.json
"""

from __future__ import annotations
import os, json, time, math
from datetime import datetime
from typing import Dict, Any, List, Tuple

# ========================= Utils =========================
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _get(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    return v if v is not None else default

def _to_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1","true","yes","on")

def _to_float(v: str, d: float) -> float:
    try:
        s = str(v).strip()
        return float(s) if s != "" else d
    except Exception:
        return d

def _norm_rows_to_dicts(rows: List[List[float]]) -> List[Dict[str,float]]:
    out = []
    for r in rows or []:
        if len(r) >= 5:
            out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])})
    return out

def _print_flags():
    print(f"🔎 NEWS ativo?: {_to_bool(_get('NEWS_USE','true'))} | IA ativa?: {str(_to_bool(_get('AI_USE','true'))).lower()} | Histórico ativado?: {_to_bool(_get('SAVE_HISTORY','true'))} | Twitter ativo?: {_to_bool(_get('TWITTER_USE','true'))}")

# ========================= Imports opcionais =========================
try:
    from data_fetcher_coingecko import fetch_ohlc, norm_rows
except Exception as e:
    print("❌ data_fetcher_coingecko indisponível:", e)
    fetch_ohlc = None
    norm_rows = lambda x: []

# Estratégias externas (se existirem, têm prioridade)
try:
    from apply_strategies import score_signal, score_from_indicators, compute_indicators  # type: ignore
except Exception:
    score_signal = None
    score_from_indicators = None
    compute_indicators = None

# Sentimento
try:
    from sentiment_analyzer import get_sentiment_for_symbol  # type: ignore
except Exception:
    get_sentiment_for_symbol = None  # type: ignore

# Notifier
try:
    from notifier_v2 import send_signal_notification  # type: ignore
except Exception:
    send_signal_notification = None  # type: ignore

# ========================= Coleta OHLC =========================
def collect_ohlc_for(symbols: List[str], days: int = 30, min_bars: int = 120) -> Dict[str, List[List[float]]]:
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
            collected[sym] = bars[-min_bars:]  # janela mínima
            print(f"   → OK | candles= {len(bars)}")
        except Exception as e:
            print(f"⚠️ CoinGecko falhou {sym}: {e}")
            print(f"⚠️ {sym}: OHLC insuficiente (0/{min_bars})")
    return collected

# ========================= Técnico EMBUTIDO =========================
def _ema(values: List[float], period: int) -> List[float]:
    if not values or period <= 1 or len(values) < period:
        return [values[-1]] if values else []
    k = 2.0 / (period + 1.0)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append(v * k + ema[-1] * (1.0 - k))
    # alinhar tamanho ao len(values)
    pad = len(values) - len(ema)
    return [ema[0]] * pad + ema

def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, period + 1):
        ch = closes[-(period+1)+i] - closes[-(period+1)+i-1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def _macd(closes: List[float], fast=12, slow=26, sig=9) -> Tuple[float,float,float]:
    if len(closes) < slow + sig:
        return 0.0, 0.0, 0.0
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [a - b for a, b in zip(ema_fast, ema_slow)]
    signal = _ema(macd_line, sig)
    hist = macd_line[-1] - signal[-1]
    return macd_line[-1], signal[-1], hist

def _bb(closes: List[float], period=20, mult=2.0) -> Tuple[float,float,float]:
    if len(closes) < period:
        c = closes[-1] if closes else 0.0
        return c, c, c
    window = closes[-period:]
    ma = sum(window) / period
    var = sum((x - ma) ** 2 for x in window) / period
    sd = math.sqrt(var)
    return ma, ma + mult * sd, ma - mult * sd

def _stoch(highs: List[float], lows: List[float], closes: List[float], period=14) -> float:
    if len(closes) < period:
        return 50.0
    hh = max(highs[-period:])
    ll = min(lows[-period:])
    if hh == ll:
        return 50.0
    return 100.0 * (closes[-1] - ll) / (hh - ll)

def basic_tech_score(ohlc: List[Dict[str,float]]) -> float:
    """
    Score 0..1 combinando sinais simples:
    - EMA12 vs EMA26 (tendência)
    - RSI(14) (sobrevenda/compra)
    - MACD hist (momentum)
    - Bollinger (posicionamento)
    - Stoch(14)
    """
    if len(ohlc) < 30:
        return 0.0
    closes = [x["c"] for x in ohlc]
    highs  = [x["h"] for x in ohlc]
    lows   = [x["l"] for x in ohlc]

    ema12 = _ema(closes, 12)[-1]
    ema26 = _ema(closes, 26)[-1]
    ema_trend = 1.0 if ema12 > ema26 else 0.0

    rsi = _rsi(closes, 14)             # 0..100
    rsi_sig = 1.0 if rsi < 35 else (0.5 if 35 <= rsi <= 65 else 0.0)

    macd, sig, hist = _macd(closes)
    macd_sig = 1.0 if hist > 0 else 0.0

    bb_mid, bb_hi, bb_lo = _bb(closes, 20, 2.0)
    pos = 0.5
    if bb_hi != bb_lo:
        pos = (closes[-1] - bb_lo) / (bb_hi - bb_lo)  # 0..1
    bb_sig = 1.0 if pos <= 0.25 else (0.5 if pos <= 0.5 else 0.0)

    st = _stoch(highs, lows, closes, 14)  # 0..100
    st_sig = 1.0 if st < 30 else (0.5 if st <= 70 else 0.0)

    # pesos
    w = {"ema":1.0, "rsi":1.0, "macd":1.0, "bb":0.7, "st":0.7}
    raw = (ema_trend*w["ema"] + rsi_sig*w["rsi"] + macd_sig*w["macd"] + bb_sig*w["bb"] + st_sig*w["st"])
    maxw = sum(w.values())
    score = raw / maxw  # 0..1
    return max(0.0, min(1.0, score))

# usa módulo externo se existir, senão embutido
def safe_score_tech(past_ohlc_dicts: List[Dict[str,float]]) -> float:
    # prioridade: score_signal (módulo do usuário)
    try:
        if score_signal:
            s = score_signal(past_ohlc_dicts)
            if isinstance(s, dict):   val = s.get("score", s.get("value", 0.0))
            elif isinstance(s,(tuple,list)): val = s[0]
            else:                     val = s
            val = float(val)
            if val > 1.0: val /= 100.0
            return max(0.0, min(1.0, val))
    except Exception as e:
        print(f"[TECH] erro em score_signal: {e}")

    # alternativa: compute_indicators + score_from_indicators
    try:
        if compute_indicators and score_from_indicators:
            ind = compute_indicators(past_ohlc_dicts)
            val = score_from_indicators(ind)
            if isinstance(val, dict):   val = val.get("score", 0.0)
            elif isinstance(val,(tuple,list)): val = val[0]
            val = float(val)
            if val > 1.0: val /= 100.0
            return max(0.0, min(1.0, val))
    except Exception as e:
        print(f"[TECH] erro em compute_indicators/score_from_indicators: {e}")

    # fallback: técnico embutido
    try:
        return basic_tech_score(past_ohlc_dicts)
    except Exception as e:
        print(f"[TECH] erro em basic_tech_score: {e}")
        return 0.0

# ========================= Sentimento =========================
def safe_get_sentiment(symbol: str) -> Dict[str, Any]:
    if get_sentiment_for_symbol is None:
        return {"score": 0.5, "news_n": 0, "tw_n": 0}
    try:
        raw = get_sentiment_for_symbol(symbol)
        if isinstance(raw, dict):
            score = raw.get("score", raw.get("value", raw.get("sentiment", 0.5)))
            return {
                "score": float(score) if score is not None else 0.5,
                "news_n": int(raw.get("news_n", raw.get("news_count", 0)) or 0),
                "tw_n":   int(raw.get("tw_n",   raw.get("twitter_count", 0)) or 0),
            }
        if isinstance(raw, (tuple, list)):
            score = float(raw[0]) if len(raw)>=1 else 0.5
            news_n = int(raw[1]) if len(raw)>=2 else 0
            tw_n   = int(raw[2]) if len(raw)>=3 else 0
            return {"score": score, "news_n": news_n, "tw_n": tw_n}
        if isinstance(raw, (int, float)):
            return {"score": float(raw), "news_n": 0, "tw_n": 0}
        return {"score": 0.5, "news_n": 0, "tw_n": 0}
    except Exception as e:
        print(f"[SENT] erro {symbol}: {e}")
        return {"score": 0.5, "news_n": 0, "tw_n": 0}

# ========================= Mix & Notifier =========================
def mix_scores(tech: float, sent: float, wt: float, ws: float) -> float:
    try:
        return (tech * wt + sent * ws) / (wt + ws)
    except Exception:
        return 0.0

def maybe_emit_signal(symbol: str, mix_score: float, last_price: float, thr: float) -> None:
    if mix_score < thr:
        return
    signal = {
        "id": f"sig-{int(time.time())}",
        "symbol": symbol,
        "entry": last_price,
        "tp": round(last_price * 1.02, 8),
        "sl": round(last_price * 0.99, 8),
        "rr": 2.0,
        "confidence": round(mix_score * 100.0, 2),
        "strategy": "TECH(EMA/RSI/MACD/BB/Stoch)+NEWS/TW",
        "created_at": _ts()
    }
    # log rico
    print(
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
    if send_signal_notification:
        try:
            send_signal_notification(signal)
        except Exception as e:
            print(f"⚠️ Notifier falhou: {e}")

# ========================= Pipeline =========================
def run_pipeline():
    interval_min = int(_get("INTERVAL_MIN", "20"))
    days     = int(_get("DAYS_OHLC", "30"))
    min_bars = int(_get("MIN_BARS", "120"))
    thr      = _to_float(_get("SCORE_THRESHOLD", "0.70"), 0.70)

    WEIGHT_TECH = _to_float(_get("WEIGHT_TECH", "1.5"), 1.5)
    WEIGHT_SENT = _to_float(_get("WEIGHT_SENT", "1.0"), 1.0)

    syms_env = [s.strip().upper() for s in _get("SYMBOLS","").split(",") if s.strip()]
    if not syms_env:
        syms_env = ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]

    # filtra estáveis redundantes
    stable_redundant = {"FDUSDUSDT","USDCUSDT","TUSDUSDT","USDPUSDT","DAIUSDT"}
    before = len(syms_env)
    syms = [s for s in syms_env if s not in stable_redundant]
    removed = before - len(syms)
    if removed > 0:
        print(f"🧠 Removidos {removed} pares estáveis redundantes (ex.: FDUSDUSDT).")

    _print_flags()
    preview = ", ".join(syms[:30]) + ("…" if len(syms)>30 else "")
    print(f"🧪 Moedas deste ciclo ({len(syms)}/{len(syms)}): {preview}")

    t0 = time.time()

    # 1) Coleta
    data = collect_ohlc_for(syms, days=days, min_bars=min_bars)

    # 2) Persistência
    try:
        with open(_get("DATA_RAW_FILE","data_raw.json"), "w", encoding="utf-8") as f:
            json.dump({"symbols": list(data.keys()), "data": data}, f, ensure_ascii=False)
        print(f"💾 Salvo data_raw.json ({len(data)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar data_raw.json: {e}")

    # 3) Avaliação
    for sym, rows in data.items():
        ohlc = _norm_rows_to_dicts(rows)
        last_price = ohlc[-1]["c"] if ohlc else 0.0

        tech = safe_score_tech(ohlc)
        sent = safe_get_sentiment(sym)
        sent_score = float(sent.get("score", 0.5))
        news_n = int(sent.get("news_n", 0))
        tw_n   = int(sent.get("tw_n", 0))

        mix = mix_scores(tech, sent_score, WEIGHT_TECH, WEIGHT_SENT)

        print(f"[IND] {sym} | Técnico: {round(tech*100,1)}% | Sentimento: {round(sent_score*100,1)}% (news n={news_n}, tw n={tw_n}) | Mix(T:{WEIGHT_TECH},S:{WEIGHT_SENT}): {round(mix*100,1)}% (min {int(thr*100)}%)")

        # 4) Sinal se >= threshold
        maybe_emit_signal(sym, mix, last_price, thr)

    t1 = time.time()
    print(f"🕒 Fim: {_ts()}")
    print(f"✅ Ciclo concluído em {int(t1-t0)}s. Próxima execução")

# ========================= Entry =========================
if __name__ == "__main__":
    run_pipeline()
