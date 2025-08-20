# main.py
import os
import json
import time
import math
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ==============================
# Utilidade de log (ASCII only)
# ==============================
def _log(msg: str) -> None:
    print(msg, flush=True)

# ==============================
# Configuração via ENV
# ==============================
def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name, str(default)).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return float(default)
    try:
        return float(v)
    except ValueError:
        # aceita formatos como "4h", "90m"
        s = v.strip().lower()
        if s.endswith("h"):
            return float(int(s[:-1]) * 60)
        if s.endswith("m"):
            return float(int(s[:-1]))
        return float(default)

INTERVAL_MIN = _env_float("INTERVAL_MIN", _env_float("BINANCE_INTERVAL", 20))
AI_ENABLE     = _env_bool("AI_ENABLE", True)
NEWS_USE      = _env_bool("NEWS_USE", True)
TWITTER_USE   = _env_bool("TWITTER_USE", True)

MIX_TECH_OVER_SENT = _env_float("MIX_TECH_OVER_SENT", 1.5)  # peso do técnico
MIX_SENT_OVER_TECH = _env_float("MIX_SENT_OVER_TECH", 1.0)  # peso do sentimento
MIX_MIN_THRESHOLD  = _env_float("MIX_MIN_THRESHOLD", 70.0)  # limiar de sinal

OHLC_DAYS      = int(os.getenv("OHLC_DAYS", "30"))
OHLC_LIMIT     = int(os.getenv("OHLC_LIMIT", "180"))  # numero de candles a buscar
OHLC_TIMEFRAME = os.getenv("OHLC_TIMEFRAME", "1h").lower()  # 1d, 1h, 1m

CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY") or os.getenv("CRYPTOCOMPARE_APIKEY") or os.getenv("CRYPTOCOMPARE_API_KEY".upper())
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHATID = os.getenv("TELEGRAM_CHAT_ID")

# ==============================
# Universo de símbolos
# ==============================
def load_symbol_list() -> List[str]:
    csv = os.getenv("SYMBOLS", "").strip()
    if csv:
        syms = [s.strip().upper() for s in csv.split(",") if s.strip()]
        return syms

    # fallback: conjunto padrão (30)
    return [
        "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
        "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT","UNIUSDT",
        "BCHUSDT","ETCUSDT","APTUSDT","IMXUSDT","FILUSDT","NEARUSDT","OPUSDT","XLMUSDT",
        "HBARUSDT","INJUSDT","ARBUSDT","LDOUSDT","ATOMUSDT","STXUSDT",
    ]

# ==============================
# HTTP util (com backoff)
# ==============================
def http_get_json(url: str, headers: Dict[str, str], max_retries: int = 6) -> Dict:
    backoff = 5.0
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            last_err = e
            _log(f"Aviso HTTP {e.code} em {url} — aguardando {backoff:.1f}s (tentativa {attempt}/{max_retries})")
            time.sleep(backoff)
            backoff *= 2.2
        except URLError as e:
            last_err = e
            _log(f"Aviso URLError em {url}: {e} — aguardando {backoff:.1f}s (tentativa {attempt}/{max_retries})")
            time.sleep(backoff)
            backoff *= 2.2
        except Exception as e:
            last_err = e
            _log(f"Aviso geral em {url}: {e} — aguardando {backoff:.1f}s (tentativa {attempt}/{max_retries})")
            time.sleep(backoff)
            backoff *= 2.2
    raise RuntimeError(f"GET falhou após {max_retries} tentativas: {last_err}")

# ==============================
# OHLC via CryptoCompare
# ==============================
def cc_hist_endpoint(tf: str) -> Tuple[str, str]:
    tf = tf.lower()
    if tf in ("1d","d","day","1day"):
        return ("histoday", "1d")
    if tf in ("1h","h","hour","1hour","60"):
        return ("histohour", "1h")
    if tf in ("1m","m","min","minute"):
        return ("histominute", "1m")
    # padrão seguro
    return ("histohour", "1h")

def fetch_ohlc_cc(symbol: str, limit: int = OHLC_LIMIT, tf: str = OHLC_TIMEFRAME) -> List[Dict]:
    if not CRYPTOCOMPARE_API_KEY:
        raise RuntimeError("CRYPTOCOMPARE_API_KEY não definido.")

    base = symbol.upper().replace("USDT","")
    quote = "USDT"
    endpoint, _tf = cc_hist_endpoint(tf)

    params = {
        "fsym": base,
        "tsym": quote,
        "limit": max(1, min(2000, int(limit))),
        "api_key": CRYPTOCOMPARE_API_KEY,
        "aggregate": 1,
    }
    url = f"https://min-api.cryptocompare.com/data/v2/{endpoint}?{urlencode(params)}"
    data = http_get_json(url, headers={"Accept":"application/json"})

    if not data or data.get("Response") != "Success":
        raise RuntimeError(f"CryptoCompare resposta inválida para {symbol}: {data}")

    rows = data.get("Data",{}).get("Data",[])
    ohlc = []
    for r in rows:
        # alguns pontos podem vir com volume zero ou close zero; filtramos
        if r.get("close") in (None,0) or r.get("high") in (None,0) or r.get("low") in (None,0) or r.get("open") in (None,0):
            continue
        ohlc.append({
            "t": int(r["time"]),
            "o": float(r["open"]),
            "h": float(r["high"]),
            "l": float(r["low"]),
            "c": float(r["close"]),
            "v": float(r.get("volumeto", 0.0)),
        })
    return ohlc

# ==============================
# Indicadores técnicos básicos
# ==============================
def ema(series: List[float], period: int) -> List[float]:
    k = 2 / (period + 1)
    out = []
    ema_prev = None
    for x in series:
        if ema_prev is None:
            ema_prev = x
        else:
            ema_prev = x * k + ema_prev * (1 - k)
        out.append(ema_prev)
    return out

def rsi(series: List[float], period: int = 14) -> List[float]:
    gains = []
    losses = []
    rsis = []
    prev = None
    avg_gain = avg_loss = None
    for x in series:
        if prev is None:
            gains.append(0.0); losses.append(0.0)
            rsis.append(50.0)
            prev = x
            continue
        ch = x - prev
        g = max(ch, 0.0)
        l = max(-ch, 0.0)
        gains.append(g); losses.append(l)
        prev = x
        if len(gains) < period + 1:
            rsis.append(50.0)
            continue
        if avg_gain is None:
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
        else:
            avg_gain = (avg_gain * (period - 1) + g) / period
            avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            rs = float("inf")
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))
    return rsis

def macd(series: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[List[float], List[float]]:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = [f - s for f,s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line

def technical_score(ohlc: List[Dict]) -> Tuple[float, Dict[str,float]]:
    """Retorna (score em % 0-100, detalhes)"""
    closes = [x["c"] for x in ohlc]
    if len(closes) < 60:
        return 0.0, {"note": 0.0}

    rsi14 = rsi(closes, 14)
    r_now = rsi14[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    e20 = ema20[-1]; e50 = ema50[-1]

    macd_line, sig_line = macd(closes)
    m_now = macd_line[-1]; s_now = sig_line[-1]
    hist = m_now - s_now

    # normalizações simples para 0..100
    # 1) RSI: 50 -> 50%, 30 -> 0%, 70 -> 100%
    rsi_score = max(0.0, min(100.0, (r_now - 30) * (100/40)))  # 30..70

    # 2) EMA: se e20>e50, bônus; distância relativa limitada
    dist = (e20 - e50) / e50 if e50 != 0 else 0.0
    ema_score = max(0.0, min(100.0, 50.0 + 500.0 * dist))  # +-10% vira +-50 pontos

    # 3) MACD hist: normaliza por desvio das últimas 100 barras
    last = macd_line[-100:] if len(macd_line) >= 100 else macd_line
    mean = sum(last)/len(last) if last else 0.0
    var = sum((x-mean)**2 for x in last)/len(last) if last else 0.0
    sd = math.sqrt(var)
    z = (hist - mean)/sd if sd > 1e-9 else 0.0
    macd_score = max(0.0, min(100.0, 50.0 + 10.0 * z))  # z-score comprimido

    score = 0.4*rsi_score + 0.35*ema_score + 0.25*macd_score
    details = {
        "rsi": round(r_now,2),
        "ema20": round(e20,2),
        "ema50": round(e50,2),
        "macd": round(m_now,4),
        "signal": round(s_now,4),
        "hist": round(hist,4),
        "score_raw": round(score,2),
    }
    return float(round(score,2)), details

# ==============================
# Sentimento (opcional)
# ==============================
def load_sentiment_fn():
    try:
        # seu arquivo pode expor função com esse nome
        from sentiment_analyzer import get_sentiment_for_symbol as fn  # type: ignore
        return fn
    except Exception:
        return None

SENTIMENT_FN = load_sentiment_fn()

def safe_sentiment(symbol: str) -> Tuple[float,int,int]:
    """Tenta obter sentimento. Aceita dict ou tuple no retorno.
    Retorna (score_pct, news_n, tw_n)."""
    if not SENTIMENT_FN or not AI_ENABLE:
        return (50.0, 0, 0)
    try:
        out = SENTIMENT_FN(symbol)
        # formatos aceitos:
        # 1) dict: {"score": 62.3, "news_n": 4, "tw_n": 12}
        # 2) tuple: (62.3, 4, 12)
        if isinstance(out, dict):
            sc = float(out.get("score", 50.0))
            nn = int(out.get("news_n", 0))
            tn = int(out.get("tw_n", 0))
            return (sc, nn, tn)
        if isinstance(out, (list, tuple)):
            if len(out) == 3:
                sc, nn, tn = out
                return (float(sc), int(nn), int(tn))
            if len(out) == 1:
                return (float(out[0]), 0, 0)
        # fallback
        return (float(out), 0, 0)  # se for numérico solto
    except Exception as e:
        _log(f"[SENT] erro {symbol}: {e}")
        return (50.0, 0, 0)

# ==============================
# Telegram (opcional)
# ==============================
def telegram_send(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHATID:
        return
    try:
        from urllib.parse import quote_plus
        api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHATID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        url = api + "?" + urlencode(payload, quote_via=quote_plus)
        # não precisa de retries aqui; logs já garantem ciclo
        req = Request(url, headers={"Accept":"application/json"})
        with urlopen(req, timeout=15) as _:
            pass
    except Exception as e:
        _log(f"[TG] falha ao enviar mensagem: {e}")

# ==============================
# Pipeline
# ==============================
def run_pipeline():
    start_dt = datetime.now(timezone.utc)
    _log(f"NEWS ativo?: {NEWS_USE} | IA ativa?: {AI_ENABLE} | Historico ativado?: True | Twitter ativo?: {TWITTER_USE}")

    symbols = load_symbol_list()
    if not symbols:
        _log("Sem universo de moedas (nenhum símbolo definido).")
        return

    _log(f"Moedas deste ciclo ({min(100,len(symbols))}/{len(symbols)}): {', '.join(symbols[:30])}{'...' if len(symbols)>30 else ''}")

    # Coleta OHLC
    collected: Dict[str, List[Dict]] = {}
    for sym in symbols:
        _log(f"Coletando OHLC {sym} (tf={OHLC_TIMEFRAME}, limit={OHLC_LIMIT})...")
        try:
            ohlc = fetch_ohlc_cc(sym, limit=OHLC_LIMIT, tf=OHLC_TIMEFRAME)
            if len(ohlc) < 60:
                _log(f"Aviso {sym}: OHLC insuficiente ({len(ohlc)}/60)")
            else:
                _log(f"  -> OK | candles={len(ohlc)}")
            collected[sym] = ohlc
        except Exception as e:
            _log(f"Aviso OHLC {sym}: {e}")

    # Salva raw
    try:
        with open("data_raw.json","w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "symbols": list(collected.keys())}, f, ensure_ascii=True)
        _log(f"Salvo data_raw.json ({len(collected)} ativos)")
    except Exception as e:
        _log(f"Aviso ao salvar data_raw.json: {e}")

    # Calcula indicadores + sentimento + mistura
    signals = []
    for sym, ohlc in collected.items():
        if len(ohlc) < 60:
            continue
        tech, det = technical_score(ohlc)
        sent_score, news_n, tw_n = safe_sentiment(sym)

        mix = (tech * MIX_TECH_OVER_SENT + sent_score * MIX_SENT_OVER_TECH) / (MIX_TECH_OVER_SENT + MIX_SENT_OVER_TECH)

        _log(
            f"[IND] {sym} | Tecnico: {tech:.1f}% | Sentimento: {sent_score:.1f}% "
            f"(news n={news_n}, tw n={tw_n}) | Mix(T:{MIX_TECH_OVER_SENT:.1f},S:{MIX_SENT_OVER_TECH:.1f}): "
            f"{mix:.1f}% (min {MIX_MIN_THRESHOLD:.0f}%)"
        )

        if mix >= MIX_MIN_THRESHOLD:
            last_price = ohlc[-1]["c"]
            sig = {
                "symbol": sym,
                "price": last_price,
                "mix": round(mix,2),
                "tech": round(tech,2),
                "sentiment": round(sent_score,2),
                "news_n": news_n,
                "tw_n": tw_n,
                "time": int(time.time()),
            }
            signals.append(sig)

    # Salva e notifica
    try:
        with open("signals.json","w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "signals": signals}, f, ensure_ascii=True, indent=2)
        _log(f"{len(signals)} sinais salvos em signals.json")
    except Exception as e:
        _log(f"Aviso ao salvar signals.json: {e}")

    if signals:
        lines = ["Sinais encontrados:"]
        for s in signals[:10]:
            lines.append(
                f"{s['symbol']}: mix {s['mix']}% (tech {s['tech']}% / sent {s['sentiment']}%), price {s['price']}"
            )
        telegram_send("\n".join(lines))

    end_dt = datetime.now(timezone.utc)
    _log(f"Fim: {end_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")


if __name__ == "__main__":
    _log("Starting Container")
    _log(f"Runner iniciado. Intervalo = {INTERVAL_MIN:.1f} min.")
    while True:
        try:
            t0 = time.time()
            run_pipeline()
            elapsed = time.time() - t0
            wait_s = max(0, int(INTERVAL_MIN * 60 - elapsed))
            _log(f"Ciclo concluido em {int(elapsed)}s. Proxima execucao em ~{wait_s}s.")
            for _ in range(wait_s // 30):
                time.sleep(30)
                rem = wait_s - (_+1)*30
                _log(f"aguardando 30s... (restante {rem}s)")
            rest = wait_s % 30
            if rest > 0:
                time.sleep(rest)
        except Exception as e:
            traceback.print_exc()
            _log(f"Erro inesperado no ciclo: {e}")
            _log("Ciclo concluido em 0s. Proxima execucao em ~1199s.")
            # espera 20 minutos em caso de erro bruto
            for _ in range(40):
                time.sleep(30)
                _log(f"aguardando 30s... (restante {((40-(_+1))*30)}s)")
