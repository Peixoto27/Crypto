# main.py
# -*- coding: utf-8 -*-
"""
Pipeline principal:
- Lê símbolos (ENV SYMBOLS ou lista padrão)
- Remove pares estáveis redundantes (ex.: FDUSDUSDT)
- Busca OHLC (CoinGecko) com tratamento de 429 (backoff)
- Salva data_raw.json e, opcionalmente, HISTORY_DIR/ohlc/{SYMBOL}.json
- Calcula indicadores básicos (fallback) e chama News/Twitter (se habilitados)
- Usa apply_strategies.score_signal() para Técnico/Sent/Mix
- Exibe logs no padrão do projeto

Exige:
- apply_strategies.score_signal
- (opcionais) data_fetcher_coingecko.fetch_ohlc / resolve_cg_id
- (opcionais) sentiment_analyzer.SentimentRuntime
- (opcionais) twitter_sentiment.TwitterSentiment
"""

import os, json, math, time
from datetime import datetime
from typing import List, Dict, Any

# ========= Imports do projeto (com fallback) =========
try:
    from data_fetcher_coingecko import fetch_ohlc, resolve_cg_id  # resolve_cg_id é opcional
except Exception:
    fetch_ohlc = None
    resolve_cg_id = None

try:
    from apply_strategies import score_signal
except Exception as e:
    raise RuntimeError("apply_strategies.score_signal é obrigatório") from e

try:
    from sentiment_analyzer import SentimentRuntime
except Exception:
    SentimentRuntime = None

try:
    from twitter_sentiment import TwitterSentiment
except Exception:
    TwitterSentiment = None

# ========= Utils =========
def _now_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _env_str(k, d):  return os.getenv(k, d)
def _env_int(k, d):  return int(float(os.getenv(k, str(d))))
def _env_flt(k, d):  return float(os.getenv(k, str(d)))
def _env_bool(k, d): return os.getenv(k, str(d)).lower() in ("1","true","yes","on")

def _clip01(v: float) -> float:
    return max(0.0, min(1.0, v))

def _as_float(x, default=0.0) -> float:
    try:
        if x is None: return default
        v = float(x)
        if math.isnan(v) or math.isinf(v): return default
        return v
    except Exception:
        return default

# ========= Flags / ENV =========
DAYS_OHLC       = _env_int("DAYS_OHLC", 30)
MIN_BARS        = _env_int("MIN_BARS", 180)
SCORE_THR       = _env_flt("SCORE_THRESHOLD", 0.70)
WEIGHT_TECH     = _env_flt("WEIGHT_TECH", 1.5)
WEIGHT_SENT     = _env_flt("WEIGHT_SENT", 1.0)
SAVE_HISTORY    = _env_bool("SAVE_HISTORY", True)
HISTORY_DIR     = _env_str("HISTORY_DIR", "data/history")
DATA_RAW_FILE   = _env_str("DATA_RAW_FILE", "data_raw.json")

NEWS_USE        = _env_bool("NEWS_USE",   _env_bool("NEWS_ENABLED", True))
TWITTER_USE     = _env_bool("TWITTER_USE", False)
TWITTER_BEARER  = _env_str("TWITTER_BEARER_TOKEN", "")

# ========= Sentimento runtimes (opcionais) =========
news_rt = SentimentRuntime() if (NEWS_USE and SentimentRuntime) else None
tw_rt   = TwitterSentiment(TWITTER_BEARER) if (TWITTER_USE and TwitterSentiment and TWITTER_BEARER) else None

def _print_flags():
    print("Starting Container")
    print("▶️ Runner iniciado. Intervalo = 20.0 min.")  # só log
    print(f"🔎 NEWS ativo?: {bool(news_rt)} | IA ativa?: True | Histórico ativado?: {bool(SAVE_HISTORY)} | Twitter ativo?: {bool(tw_rt)}")

# ========= Símbolos =========
_STABLES = ("USDT","BUSD","USDC","TUSD","FDUSD","USDD")

def _split_base_quote(sym: str):
    # tenta dividir base/quote por sufixo conhecido (USDT etc.)
    for q in _STABLES:
        if sym.endswith(q):
            return sym[:-len(q)], q
    # fallback: 3 letras finais
    return sym[:-3], sym[-3:]

def _is_redundant_stable(sym: str) -> bool:
    base, quote = _split_base_quote(sym)
    return base in _STABLES and quote in _STABLES

def load_symbols() -> List[str]:
    raw = _env_str("SYMBOLS", "").replace(" ", "")
    if raw:
        syms = [s for s in raw.split(",") if s]
    else:
        # conjunto padrão enxuto (você pode manter a sua lista/arquivo)
        syms = ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]
    # remove redundâncias de estáveis
    redundant = [s for s in syms if _is_redundant_stable(s)]
    if redundant:
        print(f"🧠 Removidos {len(redundant)} pares estáveis redundantes (ex.: {redundant[0]}).")
        syms = [s for s in syms if s not in redundant]
    return syms

# ========= Normalização OHLC =========
def _norm_rows(rows) -> List[List[float]]:
    out = []
    if not rows:
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                out.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])])
    elif isinstance(rows, list) and isinstance(rows[0], dict):
        for r in rows:
            t = _as_float(r.get("t") or r.get("time"))
            o = _as_float(r.get("o") or r.get("open"))
            h = _as_float(r.get("h") or r.get("high"))
            l = _as_float(r.get("l") or r.get("low"))
            c = _as_float(r.get("c") or r.get("close"))
            out.append([t,o,h,l,c])
    return out

# ========= Fetch OHLC com 429 backoff =========
def fetch_ohlc_with_retry(symbol: str, days: int) -> List[List[float]]:
    if fetch_ohlc is None:
        raise RuntimeError("data_fetcher_coingecko.fetch_ohlc não disponível")
    backoffs = [30.0, 75.0, 187.5, 300.0, 420.0, 600.0]  # 6 tentativas
    tries = 0
    last_exc = None
    while tries <= len(backoffs):
        try:
            rows = fetch_ohlc(symbol, days)
            return _norm_rows(rows)
        except Exception as e:
            msg = str(e).lower()
            last_exc = e
            if "429" in msg or "rate" in msg:
                if tries < len(backoffs):
                    wait = backoffs[tries]
                    print(f"⚠️ 429: aguardando {wait:.1f}s (tentativa {tries+1}/6)")
                    time.sleep(wait)
                    tries += 1
                    continue
            # outros erros (timeout etc.)
            if "timed out" in msg or "timeout" in msg or "read timed out" in msg:
                if tries < len(backoffs):
                    wait = backoffs[tries]
                    print(f"⚠️ Erro de rede: {e}. Aguardando {wait:.1f}s (tentativa {tries+1}/6)")
                    time.sleep(wait)
                    tries += 1
                    continue
            break
    raise last_exc if last_exc else RuntimeError(f"Falha OHLC {symbol}")

# ========= Histórico local =========
def save_ohlc_cache(symbol: str, bars: List[List[float]]):
    if not SAVE_HISTORY:
        return
    try:
        path = os.path.join(HISTORY_DIR, "ohlc")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, f"{symbol}.json"), "w", encoding="utf-8") as f:
            json.dump({"symbol": symbol, "bars": bars}, f, ensure_ascii=False)
    except Exception:
        pass

# ========= Indicadores básicos (fallback local) =========
def _ema(prev, price, alpha):
    return alpha*price + (1-alpha)*prev

def _ema_series(closes: List[float], period: int) -> List[float]:
    if not closes: return []
    alpha = 2.0/(period+1.0)
    out = [closes[0]]
    for p in closes[1:]:
        out.append(_ema(out[-1], p, alpha))
    return out

def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period+1: return 50.0
    gains, losses = [], []
    for i in range(-period, 0):
        d = closes[i] - closes[i-1]
        gains.append(max(0.0, d)); losses.append(max(0.0, -d))
    ag = sum(gains)/period; al = sum(losses)/period
    if al == 0: return 100.0
    rs = ag/al
    return 100.0 - (100.0/(1.0+rs))

def _stoch(closes, highs, lows, period: int = 14):
    if len(closes) < period: return 0.5, 0.5
    c = closes[-1]; hh = max(highs[-period:]); ll = min(lows[-period:])
    if hh-ll <= 0: return 0.5, 0.5
    k = (c-ll)/(hh-ll)
    # média simples dos últimos K como D
    ks = []
    for i in range(len(closes)-period, len(closes)):
        hh_i = max(highs[i-period+1:i+1]); ll_i = min(lows[i-period+1:i+1])
        ks.append((closes[i]-ll_i)/((hh_i-ll_i) + 1e-9))
    d = sum(ks)/len(ks)
    return _clip01(k), _clip01(d)

def _bb(closes: List[float], period: int = 20):
    if len(closes) < period:
        c = closes[-1] if closes else 0.0
        return c, c, c
    win = closes[-period:]
    mid = sum(win)/period
    var = sum((x-mid)**2 for x in win)/period
    std = math.sqrt(var)
    return mid, mid+2*std, mid-2*std

def _adx_dummy() -> (float,float,float):
    return 20.0, 25.0, 20.0  # placeholder estável

def _cci(closes: List[float], period: int = 20):
    if len(closes) < period: return 0.0
    win = closes[-period:]
    sma = sum(win)/period
    mean_dev = (sum(abs(x-sma) for x in win)/period) + 1e-9
    return (closes[-1]-sma)/(0.015*period*mean_dev)

def compute_indicators_basic(bars_ll: List[List[float]]) -> Dict[str, float]:
    # bars_ll: [[t,o,h,l,c], ...]
    if not bars_ll:
        return {"close": 0.0}
    closes = [b[4] for b in bars_ll]
    highs  = [b[2] for b in bars_ll]
    lows   = [b[3] for b in bars_ll]
    close  = closes[-1]

    rsi   = _rsi(closes, 14)
    ema20 = _ema_series(closes, 20)[-1]
    ema50 = _ema_series(closes, 50)[-1]
    bb_mid, bb_hi, _bb_lo = _bb(closes, 20)
    stochK, stochD = _stoch(closes, highs, lows, 14)
    adx, pdi, mdi  = _adx_dummy()
    cci = _cci(closes, 20)

    rng = [h-l for h,l in zip(highs[-14:], lows[-14:])] if len(highs) >= 14 else [0.0]
    atr = sum(rng)/len(rng) if rng else 0.0
    atr_rel = atr / (abs(close) + 1e-9)

    return {
        "close": close,
        "rsi": rsi,
        "macd": 0.0,     # se tiver MACD real no projeto, você pode plugar aqui
        "hist": 0.0,
        "ema20": ema20,
        "ema50": ema50,
        "bb_mid": bb_mid,
        "bb_hi": bb_hi,
        "stochK": stochK,
        "stochD": stochD,
        "adx": adx,
        "pdi": pdi,
        "mdi": mdi,
        "atr_rel": atr_rel,
        "cci": cci,
    }

# ========= Sentimentos =========
def get_sentiments(symbol: str):
    sent_news, n_news = 0.5, 0
    sent_tw,   n_tw   = 0.5, 0
    try:
        if news_rt:
            sn = news_rt.score_from_news(symbol)  # espera {"score":0..1,"n":int}
            if isinstance(sn, dict):
                sent_news = _clip01(_as_float(sn.get("score"), 0.5))
                n_news    = int(sn.get("n", 0))
    except Exception:
        sent_news, n_news = 0.5, 0
    try:
        if tw_rt:
            st = tw_rt.score_for_symbol(symbol)   # espera {"score":0..1,"n":int}
            if isinstance(st, dict):
                sent_tw = _clip01(_as_float(st.get("score"), 0.5))
                n_tw    = int(st.get("n", 0))
    except Exception:
        sent_tw, n_tw = 0.5, 0
    return sent_news, n_news, sent_tw, n_tw

# ========= Pipeline =========
def run_pipeline():
    _print_flags()
    symbols = load_symbols()
    print(f"🧪 Moedas deste ciclo ({min(8,len(symbols))}/{len(symbols)}): {', '.join(symbols[:8])}")

    data_for_file: Dict[str, List[List[float]]] = {}

    # coleta OHLC
    for s in symbols:
        print(f"📊 Coletando OHLC {s} (days={DAYS_OHLC})…")
        # tenta descobrir e logar o CG id (se disponível)
        try:
            if resolve_cg_id:
                cg_id = resolve_cg_id(s)
                if cg_id:
                    print(f"🟦 CG_IDS atualizado: {s} -> {cg_id}")
        except Exception:
            pass

        try:
            rows = fetch_ohlc_with_retry(s, DAYS_OHLC)
            if len(rows) < MIN_BARS:
                print(f"❌ Dados insuficientes para {s} ({len(rows)}/{MIN_BARS})")
                continue
            data_for_file[s] = rows
            print(f"   → OK | candles={len(rows)}")
            save_ohlc_cache(s, rows)
        except Exception as e:
            print(f"⚠️ Erro OHLC {s}: {e}")

    # salva data_raw.json para uso de outros módulos
    if data_for_file:
        try:
            with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
                json.dump({"symbols": list(data_for_file.keys()), "data": data_for_file}, f, ensure_ascii=False)
            print(f"💾 Salvo {DATA_RAW_FILE} ({len(data_for_file)} ativos)")
        except Exception as e:
            print(f"⚠️ Falha salvando {DATA_RAW_FILE}: {e}")
    else:
        print("❌ Nenhum ativo com OHLC suficiente.")
        print(f"🕒 Fim: {_now_utc()}")
        return

    # cálculo de scores / logs
    for s, rows in data_for_file.items():
        try:
            ind = compute_indicators_basic(rows)
            sent_news, n_news, sent_tw, n_tw = get_sentiments(s)
            ind["sent_news"]    = sent_news
            ind["sent_twitter"] = sent_tw

            # empacota no último candle para o score_signal ler
            last = {"t": rows[-1][0], "o": rows[-1][1], "h": rows[-1][2], "l": rows[-1][3], "c": rows[-1][4], "ind": ind}
            ohlc_for_score = [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4]} for r in rows[:-1]] + [last]

            sc = score_signal(ohlc_for_score)
            tech = _clip01(float(sc.get("tech", 0.0)))
            sent = _clip01(float(sc.get("sent", 0.5)))
            mix  = _clip01(float(sc.get("mix",  (tech*WEIGHT_TECH + sent*WEIGHT_SENT)/(WEIGHT_TECH+WEIGHT_SENT))))

            # linha técnica detalhada (compacta)
            print(f"[IND] close={ind['close']:.2f} | score={tech*100:.1f}%")
            print(f"[IND] {s} | Técnico: {tech*100:.1f}% | Sentimento: {sent*100:.1f}% (news n={n_news}, tw n={n_tw}) | "
                  f"Mix(T:{WEIGHT_TECH},S:{WEIGHT_SENT}): {mix*100:.1f}% (min {int(SCORE_THR*100)}%)")
        except Exception as e:
            print(f"[IND] erro em score_signal: {e}")

    print(f"🕒 Fim: {_now_utc()}")

# ========= Entry-point =========
if __name__ == "__main__":
    run_pipeline()
