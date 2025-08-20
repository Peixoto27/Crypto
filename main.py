# main.py
# Runner orquestrador: universo (CMC), OHLC (CoinGecko), técnico leve,
# sentimento (CryptoPanic + Twitter), e mix final. Salva data_raw.json.

import os, json, time, math, traceback
from typing import List, Dict, Any
from datetime import datetime, timezone

# ----- ENV -----
RUN_INTERVAL_MIN = float(os.getenv("RUN_INTERVAL_MIN", "20"))
TOP_N = int(os.getenv("TOP_SYMBOLS", "100"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "30"))

NEWS_USE = os.getenv("NEWS_USE", "true").lower() == "true"
AI_ACTIVE = os.getenv("AI_ACTIVE", "true").lower() == "true"
HISTORY_USE = os.getenv("HISTORY_USE", "true").lower() == "true"
TWITTER_USE = os.getenv("TWITTER_USE", "true").lower() == "true"

MIX_TECH_OVER_SENT = float(os.getenv("MIX_TECH_OVER_SENT", "1.5"))
MIX_SENT_OVER_TECH = float(os.getenv("MIX_SENT_OVER_TECH", "1.0"))
MIX_MIN_THRESHOLD = float(os.getenv("MIX_MIN_THRESHOLD", "70.0"))

# ----- Dependências (CG + CMC + sentimento) -----
try:
    from data_fetcher_coingecko import fetch_ohlc as cg_fetch_ohlc, fetch_top_symbols as cg_top
except Exception:
    cg_fetch_ohlc, cg_top = None, None

try:
    from cmc_client import get_top_symbols as cmc_top, get_quote_usd as cmc_quote
except Exception:
    cmc_top, cmc_quote = None, None

from sentiment_analyzer import get_sentiment_for_symbol

STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "PYUSD", "EUR", "BRL"}

def _log(msg: str):
    print(msg, flush=True)

def _now_utc_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def _remove_stables(symbols: List[str]) -> List[str]:
    out = []
    seen = set()
    for s in symbols:
        s = s.upper()
        if s in seen:
            continue
        seen.add(s)
        # remove pares redundantes tipo FDUSDUSDT etc.
        if any(stab in s[:-4] for stab in STABLES):
            continue
        if not s.endswith("USDT"):
            continue
        out.append(s)
    # remove duplicadas por base (ex: FDUSDUSDT redundante)
    return out

def _get_universe() -> List[str]:
    # Preferimos CMC
    if cmc_top:
        try:
            return _remove_stables(cmc_top(TOP_N))
        except Exception:
            pass
    # Fallback CoinGecko se houver
    if cg_top:
        try:
            return _remove_stables(cg_top(TOP_N) or [])
        except Exception:
            pass
    return []

# ---------- Técnico leve (EMA/RSI/BB) ----------
def _ema(values: List[float], n: int) -> float:
    if not values or n <= 1 or len(values) < n:
        return values[-1] if values else 0.0
    k = 2 / (n + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema

def _rsi(values: List[float], n: int = 14) -> float:
    if len(values) < n + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, n + 1):
        ch = values[-i] - values[-i-1]
        gains.append(max(0.0, ch))
        losses.append(max(0.0, -ch))
    avg_gain = sum(gains) / n
    avg_loss = sum(losses) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def _bb(values: List[float], n: int = 20):
    if len(values) < n:
        mid = sum(values)/max(1,len(values))
        return mid, mid, mid
    arr = values[-n:]
    mid = sum(arr)/n
    var = sum((x-mid)**2 for x in arr)/n
    sd = math.sqrt(max(0.0, var))
    return mid, mid - 2*sd, mid + 2*sd

def _technical_score_from_closes(closes: List[float]) -> float:
    if not closes:
        return 0.0
    close = closes[-1]
    ema20 = _ema(closes[-60:], 20)
    ema50 = _ema(closes[-120:], 50) if len(closes) >= 120 else _ema(closes, max(2, len(closes)//2))
    rsi14 = _rsi(closes, 14)
    bb_mid, bb_lo, bb_hi = _bb(closes, 20)

    score = 0.0
    # EMA alinhadas
    if close > ema20: score += 15
    if ema20 > ema50: score += 15
    # RSI na zona "ok"
    if 45 <= rsi14 <= 60: score += 15
    elif rsi14 > 60: score += 10
    elif rsi14 < 40: score += 5
    # Bollinger: mais perto do meio/alta
    if bb_hi != bb_lo:
        pos = (close - bb_lo) / (bb_hi - bb_lo)  # 0..1
        score += max(0.0, min(1.0, pos)) * 20
    # momentum simples
    if len(closes) >= 10 and close > closes[-10]:
        score += 20
    # clamp 0..100
    return max(0.0, min(100.0, score))

# ---------- OHLC via CoinGecko ----------
def _fetch_ohlc(symbol: str, days: int = 30) -> List[Dict[str, Any]]:
    """
    Espera que data_fetcher_coingecko.fetch_ohlc(sym, days) exista.
    Converte para [{'t':..., 'o':...,'h':...,'l':...,'c':...}, ...]
    """
    if not cg_fetch_ohlc:
        return []
    try:
        candles = cg_fetch_ohlc(symbol, days=days)  # formato das tuplas esperado (t,o,h,l,c) ou similar
    except Exception:
        return []
    out = []
    # Aceita lista de listas/tuplas [t,o,h,l,c] ou dicts; normaliza:
    for it in candles or []:
        if isinstance(it, dict):
            t = it.get("t") or it.get("time") or it.get("ts")
            o = it.get("o"); h = it.get("h"); l = it.get("l"); c = it.get("c")
        else:
            # tenta [t,o,h,l,c]
            try:
                t, o, h, l, c = it[0], it[1], it[2], it[3], it[4]
            except Exception:
                continue
        try:
            out.append({"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
        except Exception:
            continue
    return out

def _closes(ohlc: List[Dict[str,Any]]) -> List[float]:
    return [x["c"] for x in ohlc if "c" in x]

# ---------- RUN ----------
def run_pipeline():
    _log(f"▶️ Runner iniciado. Intervalo = {RUN_INTERVAL_MIN:.1f} min.")
    _log(f"🔎 NEWS ativo?: {NEWS_USE} | IA ativa?: {AI_ACTIVE} | Histórico ativado?: {HISTORY_USE} | Twitter ativo?: {TWITTER_USE}")

    symbols = _get_universe()
    symbols = symbols[:TOP_N]
    if not symbols:
        _log("❌ Sem universo de moedas (CMC/CG indisponíveis).")
        return

    _log(f"🧪 Moedas deste ciclo ({min(len(symbols), TOP_N)}/{TOP_N}): " + ", ".join(symbols[:min(30, len(symbols))]) + ("..." if len(symbols) > 30 else ""))

    collected = {}
    ok_count = 0

    for sym in symbols[:BATCH_SIZE]:
        _log(f"📊 Coletando OHLC {sym} (days=30)…")
        ohlc = _fetch_ohlc(sym, days=30)
        closes = _closes(ohlc)
        if len(closes) < 60:
            _log(f"⚠️ {sym}: OHLC insuficiente ({len(closes)}/60)")
        else:
            _log("   → OK | candles= 180" if len(ohlc) >= 180 else f"   → OK | candles= {len(ohlc)}")
            ok_count += 1
        collected[sym] = {"ohlc": ohlc}

    # Salva bruto
    try:
        with open("data_raw.json", "w", encoding="utf-8") as f:
            json.dump({"symbols": list(collected.keys()), "data": collected}, f)
        _log(f"💾 Salvo data_raw.json ({len(collected)} ativos)")
    except Exception:
        _log("⚠️ Não foi possível salvar data_raw.json")

    # Processa técnico + sentimento
    for sym, d in collected.items():
        closes = _closes(d["ohlc"])
        last_price = closes[-1] if closes else None
        if last_price is None and cmc_quote:
            try:
                last_price = cmc_quote(sym)
            except Exception:
                last_price = None

        tech = _technical_score_from_closes(closes) if closes else 0.0

        sent = {"score": 50.0, "news_n": 0, "tw_n": 0}
        try:
            sent = get_sentiment_for_symbol(sym, last_price=last_price)
        except TypeError:
            # compat de assinaturas antigas
            sent = get_sentiment_for_symbol(sym)
        except Exception as e:
            _log(f"[SENT] erro {sym}: {e}")

        sent_score = float(sent.get("score", 50.0))
        news_n = int(sent.get("news_n", 0))
        tw_n = int(sent.get("tw_n", 0))

        mix = (tech * MIX_TECH_OVER_SENT + sent_score * MIX_SENT_OVER_TECH) / (MIX_TECH_OVER_SENT + MIX_SENT_OVER_TECH)

        _log(f"[IND] {sym} | Técnico: {tech:.1f}% | Sentimento: {sent_score:.1f}% (news n={news_n}, tw n={tw_n}) | Mix(T:{MIX_TECH_OVER_SENT:.1f},S:{MIX_SENT_OVER_TECH:.1f}): {mix:.1f}% (min {MIX_MIN_THRESHOLD:.0f}%)")

    _log(f"🕒 Fim: {_now_utc_str()}")
    _log("✅ Ciclo concluído. Próxima execução")
    # (O runner externo já reexecuta no intervalo; aqui só finalizamos.)

if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception:
        traceback.print_exc()
