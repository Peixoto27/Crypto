# -*- coding: utf-8 -*-
import os, json, time, math, random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple, Optional

# =========================================================
# Utils
# =========================================================
UTC = lambda: datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def getenv(name: str, default: str) -> str:
    return os.getenv(name, default)

def jdump(path: str, obj: Any):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def jload(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def norm_pct(x: float) -> float:
    # aceita 0..1 ou 0..100
    return clamp01(x/100.0 if x > 1.0 else x)

# =========================================================
# Imports dos módulos do projeto (com fallbacks)
# =========================================================
try:
    from data_fetcher_coingecko import fetch_ohlc, fetch_prices, COINGECKO_IDS
except Exception:
    fetch_ohlc = None
    fetch_prices = None
    COINGECKO_IDS = {}

try:
    from apply_strategies import score_signal, generate_signal
except Exception:
    # fallbacks mínimos
    def score_signal(ohlc: List[Dict[str, float]]):
        # heurística boba só pra não quebrar
        if not ohlc: return 0.0
        closes = [b["c"] for b in ohlc[-14:]]
        up = sum(1 for i in range(1, len(closes)) if closes[i] >= closes[i-1])
        return up / max(1, len(closes)-1)
    def generate_signal(ohlc: List[Dict[str,float]]):
        if not ohlc: return None
        last = ohlc[-1]["c"]
        return {"entry": last, "tp": last*1.02, "sl": last*0.99}

try:
    from notifier_telegram import send_signal, send_info
except Exception:
    def send_signal(*args, **kwargs): pass
    def send_info(*args, **kwargs): pass

# Sentimento News (se tiver módulo próprio, usa; senão implemento simples aqui)
try:
    from sentiment_analyzer import get_sentiment_score as get_news_sentiment
except Exception:
    def get_news_sentiment(symbol: str, **kw) -> Tuple[float,int]:
        # fallback neutro
        return (0.5, 0)

# Sentimento Twitter (módulo opcional)
try:
    from twitter_sentiment import get_twitter_sentiment
except Exception:
    def get_twitter_sentiment(symbol: str, **kw) -> Tuple[float,int]:
        return (0.5, 0)

# Histórico opcional
try:
    from history_manager import save_ohlc_history
except Exception:
    def save_ohlc_history(*args, **kwargs): pass

# =========================================================
# ENV / Config
# =========================================================
INTERVAL_MIN         = int(getenv("INTERVAL_MIN", "20"))
DAYS_OHLC            = int(getenv("DAYS_OHLC", "30"))
MIN_BARS             = int(getenv("MIN_BARS", "180"))
SYMBOLS_ENV          = [s for s in getenv("SYMBOLS","").replace(" ","").split(",") if s]

BATCH_SIZE           = int(getenv("BATCH_SIZE","8"))
SCORE_THRESHOLD      = float(getenv("SCORE_THRESHOLD","0.70"))

WEIGHT_TECH          = float(getenv("WEIGHT_TECH","1.5"))
WEIGHT_SENT          = float(getenv("WEIGHT_SENT","1.0"))
NEWS_WEIGHT          = float(getenv("NEWS_WEIGHT","0.6"))
TWITTER_WEIGHT       = float(getenv("TWITTER_WEIGHT","0.4"))

# NewsData
NEWS_API_URL         = getenv("NEWS_API_URL","https://newsdata.io/api/1/news")
NEWS_API_KEY         = getenv("NEWS_API_KEY","")
NEWS_LOOKBACK_HOURS  = int(getenv("NEWS_LOOKBACK_HOURS","12"))
NEWS_MAX_PER_SOURCE  = int(getenv("NEWS_MAX_PER_SOURCE","5"))
NEWS_TIMEOUT         = int(getenv("NEWS_TIMEOUT","8"))
NEWS_LANGS           = getenv("NEWS_LANGS","en,pt")
NEWS_CATEGORY        = getenv("NEWS_CATEGORY","business,technology")
NEWS_CACHE_FILE      = getenv("NEWS_CACHE_FILE","news_cache.json")
NEWS_CACHE_TTL_MIN   = int(getenv("NEWS_CACHE_TTL_MIN","30"))

# Twitter/X
TWITTER_USE          = getenv("TWITTER_USE","false").lower() in ("1","true","yes")
TWITTER_BEARER       = getenv("TWITTER_BEARER_TOKEN","")
TWITTER_LOOKBACK_MIN = int(getenv("TWITTER_LOOKBACK_MIN","120"))
TWITTER_MAX_TWEETS   = int(getenv("TWITTER_MAX_TWEETS","80"))
TWITTER_TIMEOUT      = int(getenv("TWITTER_TIMEOUT","20"))
TWITTER_LANGS        = getenv("TWITTER_LANGS","en,pt")
TWITTER_CACHE_TTL    = int(getenv("TWITTER_CACHE_TTL","900"))
TWITTER_HOURLY_LIMIT = int(getenv("TWITTER_HOURLY_LIMIT","60"))

# IA & Histórico
IA_USE               = getenv("IA_USE","true").lower() in ("1","true","yes")
SAVE_HISTORY         = getenv("SAVE_HISTORY","true").lower() in ("1","true","yes")
HISTORY_DIR          = getenv("HISTORY_DIR","data/history")
DATA_RAW_FILE        = getenv("DATA_RAW_FILE","data_raw.json")

# =========================================================
# OHLC helpers
# =========================================================
def _norm_rows(rows: Any) -> List[Dict[str,float]]:
    out = []
    if not rows: return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                out.append({"t":float(r[0]),"o":float(r[1]),"h":float(r[2]),
                            "l":float(r[3]),"c":float(r[4])})
    elif isinstance(rows, list) and isinstance(rows[0], dict):
        for r in rows:
            o=float(r.get("o", r.get("open", 0.0)))
            h=float(r.get("h", r.get("high", 0.0)))
            l=float(r.get("l", r.get("low", 0.0)))
            c=float(r.get("c", r.get("close", 0.0)))
            t=float(r.get("t", r.get("time", 0.0)))
            out.append({"t":t,"o":o,"h":h,"l":l,"c":c})
    return out

def collect_ohlc(symbols: List[str], days: int) -> Dict[str, List[Dict[str,float]]]:
    data: Dict[str,List[Dict[str,float]]] = {}
    for s in symbols:
        try:
            raw = fetch_ohlc(s, days) if fetch_ohlc else None
            bars = _norm_rows(raw)
            if len(bars) >= MIN_BARS:
                data[s] = bars[-MIN_BARS:]
                print(f"   → OK | candles={len(bars)}")
                if SAVE_HISTORY:
                    save_ohlc_history(HISTORY_DIR, s, bars)
            else:
                print(f"   ✖ Dados insuficientes para {s}")
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                delay = 30.0 if "tentativa 1" not in msg else 75.0
                print(f"⚠️ 429: aguardando {delay:.1f}s (tentativa ?/6)")
                time.sleep(delay)
            else:
                print(f"⚠️ Erro OHLC {s}: {e}")
    return data

# =========================================================
# Sentimentos (News + Twitter) com cache
# =========================================================
def _load_cache(path: str) -> Dict[str, Any]:
    obj = jload(path) or {"ts": 0, "data": {}}
    return obj

def _save_cache(path: str, obj: Dict[str, Any]):
    jdump(path, obj)

def news_sentiment(symbol: str) -> Tuple[float,int]:
    if not NEWS_API_KEY:
        return (0.5, 0)
    cache = _load_cache(NEWS_CACHE_FILE)
    now = time.time()
    ttl = NEWS_CACHE_TTL_MIN * 60
    key = f"{symbol}:{NEWS_LOOKBACK_HOURS}"
    ent = cache["data"].get(key)
    if ent and now - ent["ts"] < ttl:
        return (float(ent["score"]), int(ent["n"]))

    # usa módulo do projeto se existir (importado acima)
    s, n = get_news_sentiment(
        symbol=symbol,
        api_url=NEWS_API_URL, api_key=NEWS_API_KEY,
        lookback_hours=NEWS_LOOKBACK_HOURS,
        max_per_source=NEWS_MAX_PER_SOURCE,
        langs=NEWS_LANGS, category=NEWS_CATEGORY,
        timeout=NEWS_TIMEOUT
    )
    s = norm_pct(s)
    cache["data"][key] = {"ts": now, "score": s, "n": int(n)}
    _save_cache(NEWS_CACHE_FILE, cache)
    return (s, int(n))

def twitter_sentiment(symbol: str) -> Tuple[float,int]:
    if not TWITTER_USE or not TWITTER_BEARER:
        return (0.5, 0)
    cache_file = os.path.join("data", "twitter_cache.json")
    cache = _load_cache(cache_file)
    now = time.time()
    ttl = TWITTER_CACHE_TTL
    key = f"{symbol}:{TWITTER_LOOKBACK_MIN}:{TWITTER_LANGS}"
    ent = cache["data"].get(key)
    if ent and now - ent["ts"] < ttl:
        return (float(ent["score"]), int(ent["n"]))

    s, n = get_twitter_sentiment(
        symbol=symbol,
        bearer=TWITTER_BEARER,
        lookback_min=TWITTER_LOOKBACK_MIN,
        max_tweets=TWITTER_MAX_TWEETS,
        langs=TWITTER_LANGS,
        timeout=TWITTER_TIMEOUT
    )
    s = norm_pct(s)
    cache["data"][key] = {"ts": now, "score": s, "n": int(n)}
    _save_cache(cache_file, cache)
    return (s, int(n))

# =========================================================
# Mistura e execução
# =========================================================
def safe_score_tech(ohlc: List[Dict[str,float]]) -> float:
    try:
        raw = score_signal(ohlc)
        if isinstance(raw, tuple) and len(raw)>=1:
            val = raw[0]
        elif isinstance(raw, dict):
            val = raw.get("score", raw.get("value", 0.0))
        else:
            val = raw
        return norm_pct(float(val))
    except Exception:
        return 0.0

def mix_scores(tech: float, s_news: float, s_tw: float) -> float:
    # normaliza pesos do sentimento (NEWS/TW) dentro de WEIGHT_SENT
    sent_w_total = max(1e-9, NEWS_WEIGHT + TWITTER_WEIGHT)
    w_news = WEIGHT_SENT * (NEWS_WEIGHT / sent_w_total)
    w_tw   = WEIGHT_SENT * (TWITTER_WEIGHT / sent_w_total)
    num = WEIGHT_TECH*tech + w_news*s_news + w_tw*s_tw
    den = WEIGHT_TECH + w_news + w_tw
    return num / max(1e-9, den)

def run_once():
    print("Starting Container")
    print(f"▶️ Runner iniciado. Intervalo = {INTERVAL_MIN:.1f} min.")
    print(f"🔎 NEWS ativo?: {bool(NEWS_API_KEY)} | IA ativa?: {IA_USE} | Histórico ativado?: {SAVE_HISTORY} | Twitter ativo?: {TWITTER_USE}")

    # universo
    symbols = SYMBOLS_ENV[:] if SYMBOLS_ENV else ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]
    print(f"🧪 Moedas deste ciclo ({min(BATCH_SIZE, len(symbols))}/{len(symbols)}): {', '.join(symbols[:BATCH_SIZE])}")

    # coleta OHLC
    print(f"📊 Coletando OHLC {symbols[0]} (days={DAYS_OHLC})…")
    data = collect_ohlc(symbols[:BATCH_SIZE], DAYS_OHLC)

    # salva data_raw.json
    to_save = {"symbols": list(data.keys()),
               "created_at": UTC(),
               "data": {s: [[b['t'],b['o'],b['h'],b['l'],b['c']] for b in bars] for s,bars in data.items()}}
    jdump(DATA_RAW_FILE, to_save)
    print(f"💾 Salvo {DATA_RAW_FILE} ({len(data)} ativos)")

    # calcula scores
    signals = []
    for sym, bars in data.items():
        past = bars
        tscore = safe_score_tech(past)

        nscore, n_n = news_sentiment(sym)
        tscore_tw, n_tw = twitter_sentiment(sym)

        mix = mix_scores(tscore, nscore, tscore_tw)

        # log detalhado
        print(f"[IND] {sym} | Técnico: {tscore*100:.1f}% | Sentimento: {nscore*100:.1f}% (news n={n_n}, tw n={n_tw}) | Mix(T:{WEIGHT_TECH:.1f},S:{WEIGHT_SENT:.1f}): {mix*100:.1f}% (min {int(SCORE_THRESHOLD*100)}%)")

        if mix >= SCORE_THRESHOLD:
            sig = generate_signal(past) or {}
            # formata preços em USD
            entry = float(sig.get("entry", past[-1]["c"]))
            tp    = float(sig.get("tp", entry*1.02))
            sl    = float(sig.get("sl", entry*0.99))
            rr    = (tp-entry)/max(1e-9, entry-sl)
            conf  = mix*100.0
            signals.append({
                "symbol": sym, "entry": entry, "tp": tp, "sl": sl,
                "rr": round(rr,2), "confidence": round(conf,2),
                "created_at": UTC()
            })

    # salva e notifica
    jdump("signals.json", signals)
    print(f"🗂 {len(signals)} sinais salvos em signals.json")

    for s in signals:
        try:
            send_signal(
                symbol=s["symbol"],
                entry=f"${s['entry']:.6f}",
                tp=f"${s['tp']:.6f}",
                sl=f"${s['sl']:.6f}",
                rr=s["rr"],
                confidence=s["confidence"],
                strategy="RSI+MACD+EMA+BB+STOCHRSI+ADX+CCI+ICHI+OBV+MFI+WILLR",
                created_at=s["created_at"]
            )
        except Exception:
            pass

    print(f"🕒 Fim: {UTC()}")

def main_loop():
    while True:
        t0 = time.time()
        run_once()
        dt = max(0.0, INTERVAL_MIN*60 - (time.time()-t0))
        # dorme em lapsos de 30s p/ logs bonitos
        while dt > 0:
            chunk = min(30.0, dt)
            print(f"⏳ aguardando {int(chunk)}s… (restante {int(dt)}s)")
            time.sleep(chunk)
            dt -= chunk

if __name__ == "__main__":
    main_loop()
