# -*- coding: utf-8 -*-
"""
Main runner – ciclo de coleta OHLC + score técnico + sentimento + geração de sinais.

Recursos:
- Delay entre chamadas para evitar 429 (SLEEP_BETWEEN_CALLS, default 5s)
- Fallback de OHLC (fonte primária -> secundária)
- Limite de moedas por ciclo (MAX_SYMBOLS_PER_CYCLE, default 30)
- Mistura técnica x sentimento com pesos (TECH_WEIGHT, SENT_WEIGHT)
- Salvamento de snapshot: data_raw.json

Env úteis:
  INTERVAL_MIN=20
  DAYS_OHLC=30
  MIN_BARS=60
  SLEEP_BETWEEN_CALLS=5
  MAX_SYMBOLS_PER_CYCLE=30
  SCORE_THRESHOLD=0.70
  TECH_WEIGHT=1.5
  SENT_WEIGHT=1.0
  SYMBOLS=BTCUSDT,ETHUSDT,...
  TOP_SYMBOLS=93

  USE_NEWS=true|false
  USE_TWITTER=true|false
"""

import os
import time
import json
from datetime import datetime

# ========= helpers =========

def _getenv(name: str, default: str):
    v = os.getenv(name, default)
    return v if v is not None and v != "" else default

def _to_bool(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _sleep_sec(sec: float):
    try:
        time.sleep(float(sec))
    except Exception:
        pass

def _norm_score(value) -> float:
    """
    Converte score que pode vir como float, dict {'score':x}, ou tuple (x, ...)
    para float em 0..1
    """
    try:
        if isinstance(value, dict):
            value = float(value.get("score", value.get("value", 0.0)))
        elif isinstance(value, (list, tuple)):
            value = float(value[0]) if value else 0.0
        else:
            value = float(value)
        # se vier em 0..100, normaliza
        if value > 1.0:
            value = value / 100.0
        if value < 0.0: value = 0.0
        if value > 1.0: value = 1.0
        return value
    except Exception:
        return 0.0

def _norm_ohlc_rows(rows):
    """Aceita [[ts,o,h,l,c], ...] ou [{'t':..,'o':..,'h':..,'l':..,'c':..}, ...]"""
    out = []
    if not rows:
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                out.append({
                    "t": float(r[0]), "o": float(r[1]), "h": float(r[2]),
                    "l": float(r[3]), "c": float(r[4])
                })
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

# ========= imports opcionais (modo degradado se ausente) =========

try:
    from apply_strategies import score_signal as _score_signal
except Exception:
    _score_signal = None

try:
    # sua fonte principal atual – se você usa Binance em outro módulo,
    # substitua aqui pelo import correspondente
    from data_fetcher_coingecko import fetch_ohlc as _fetch_ohlc_primary
except Exception:
    _fetch_ohlc_primary = None

# fonte secundária (fallback). Se você tiver outra, importe aqui.
_fetch_ohlc_secondary = None  # deixe None se não tiver segunda fonte disponível

try:
    from sentiment_analyzer import get_sentiment_for_symbol as _get_sentiment
except Exception:
    _get_sentiment = None

# ========= OHLC com backoff + fallback =========

def fetch_ohlc_with_retry(symbol: str, days: int, min_bars: int, sleep_between: float):
    """
    Tenta fonte primária; se falhar/insuficiente, tenta secundária.
    Aplica backoff leve (5 tentativas, atraso crescente).
    """
    attempts = 5
    # 1) fonte primária
    for i in range(1, attempts + 1):
        try:
            if _fetch_ohlc_primary is None:
                raise RuntimeError("fonte primária indisponível")
            rows = _fetch_ohlc_primary(symbol, days)
            ohlc = _norm_ohlc_rows(rows)
            if len(ohlc) >= min_bars:
                print(f"   → OK | candles= {len(ohlc)}  | fonte=primary")
                return ohlc
            else:
                print(f"   ⚠️ {symbol}: OHLC insuficiente da fonte primária ({len(ohlc)}/{min_bars})")
        except Exception as e:
            print(f"   ⚠️ {symbol}: erro fonte primária: {e}")
        _sleep_sec(sleep_between if i == 1 else sleep_between * i)

    # 2) fallback (secundária)
    if _fetch_ohlc_secondary:
        for i in range(1, attempts + 1):
            try:
                rows = _fetch_ohlc_secondary(symbol, days)
                ohlc = _norm_ohlc_rows(rows)
                if len(ohlc) >= min_bars:
                    print(f"   → OK | candles= {len(ohlc)}  | fonte=secondary")
                    return ohlc
                else:
                    print(f"   ⚠️ {symbol}: OHLC insuficiente da fonte secundária ({len(ohlc)}/{min_bars})")
            except Exception as e:
                print(f"   ⚠️ {symbol}: erro fonte secundária: {e}")
            _sleep_sec(sleep_between if i == 1 else sleep_between * i)

    # 3) falhou
    print(f"   ❌ {symbol}: OHLC insuficiente (0/{min_bars})")
    return []

# ========= lista de símbolos =========

def resolve_symbols():
    syms_env = [s.strip() for s in _getenv("SYMBOLS", "").split(",") if s.strip()]
    if syms_env:
        return syms_env
    # fallback: universo “padrão” se não houver SYMBOLS
    top = int(_getenv("TOP_SYMBOLS", "93"))
    base = [
        "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
        "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT","UNIUSDT",
        "ATOMUSDT","STXUSDT","RNDRUSDT","ICPUSDT","PEPEUSDT","CROUSDT","MKRUSDT","TAOUSDT"
    ]
    return base[:top]

# ========= sentimento =========

def compute_sentiment(symbol: str, use_news: bool, use_twitter: bool):
    """
    Retorna dict: {'score':0..1, 'news_n':int, 'tw_n':int}
    Se módulo não existir, retorna 0.5 neutro.
    """
    if _get_sentiment is None or (not use_news and not use_twitter):
        return {"score": 0.5, "news_n": 0, "tw_n": 0}

    try:
        # chamamos sem last_price para evitar erros de assinatura
        res = _get_sentiment(symbol)
        # normaliza forma de retorno
        if isinstance(res, dict):
            score = _norm_score(res.get("score", 0.5))
            news_n = int(res.get("news_n", res.get("news_count", 0)) or 0)
            tw_n   = int(res.get("tw_n",   res.get("twitter_count", 0)) or 0)
        elif isinstance(res, (list, tuple)):
            score = _norm_score(res[0] if res else 0.5)
            news_n = int(res[1] if len(res) > 1 else 0)
            tw_n = int(res[2] if len(res) > 2 else 0)
        else:
            score = _norm_score(res)
            news_n = tw_n = 0
        return {"score": score, "news_n": news_n, "tw_n": tw_n}
    except TypeError as e:
        # assinatura inesperada (ex.: last_price) – tenta sem kwargs
        try:
            res = _get_sentiment(symbol)  # retry “puro”
            score = _norm_score(res)
            return {"score": score, "news_n": 0, "tw_n": 0}
        except Exception as e2:
            print(f"[SENT] erro {symbol}: {e2}")
            return {"score": 0.5, "news_n": 0, "tw_n": 0}
    except Exception as e:
        print(f"[SENT] erro {symbol}: {e}")
        return {"score": 0.5, "news_n": 0, "tw_n": 0}

# ========= técnico =========

def compute_tech_score(ohlc):
    if not ohlc or _score_signal is None:
        return 0.0
    try:
        return _norm_score(_score_signal(ohlc))
    except Exception as e:
        print(f"[IND] erro em score_signal: {e}")
        return 0.0

# ========= loop principal =========

def run_pipeline():
    interval_min = float(_getenv("INTERVAL_MIN", "20"))
    days         = int(_getenv("DAYS_OHLC", "30"))
    min_bars     = int(_getenv("MIN_BARS", "60"))
    sleep_between= float(_getenv("SLEEP_BETWEEN_CALLS", "5"))
    max_per_cycle= int(_getenv("MAX_SYMBOLS_PER_CYCLE", "30"))
    thr          = _norm_score(float(_getenv("SCORE_THRESHOLD", "0.70")))
    wT           = float(_getenv("TECH_WEIGHT", "1.5"))
    wS           = float(_getenv("SENT_WEIGHT", "1.0"))
    use_news     = _to_bool(_getenv("USE_NEWS", "true"))
    use_twitter  = _to_bool(_getenv("USE_TWITTER", "true"))

    symbols = resolve_symbols()
    # remove pares estáveis (queda de ruído)
    stable_bases = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDD")
    filtered = [s for s in symbols if not any(s.startswith(x) or s.endswith(x) for x in stable_bases)]
    removed = len(symbols) - len(filtered)
    symbols = filtered[:max_per_cycle]

    print(f"▶️ Runner iniciado. Intervalo = {interval_min:.1f} min.")
    print(f"🔎 NEWS ativo?: {use_news} | IA ativa?: {True} | Histórico ativado?: {True} | Twitter ativo?: {use_twitter}")
    if removed > 0:
        print(f"🧠 Removidos {removed} pares estáveis redundantes (ex.: FDUSDUSDT).")
    print(f"🧪 Moedas deste ciclo ({len(symbols)}/{len(filtered)}): {', '.join(symbols)}")

    collected = {}
    start_ts = datetime.utcnow()

    # 1) OHLC
    for sym in symbols:
        print(f"📊 Coletando OHLC {sym} (days={days})…")
        ohlc = fetch_ohlc_with_retry(sym, days, min_bars, sleep_between)
        collected[sym] = ohlc
        _sleep_sec(sleep_between)

    # salva snapshot
    try:
        with open("data_raw.json", "w", encoding="utf-8") as f:
            payload = {
                "created_at": _utcnow(),
                "symbols": symbols,
                "data": {k: [[b["t"], b["o"], b["h"], b["l"], b["c"]] for b in v] for k, v in collected.items()}
            }
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"💾 Salvo data_raw.json ({len(symbols)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar data_raw.json: {e}")

    # 2) Scores + logs
    signals = []
    for sym in symbols:
        ohlc = collected.get(sym, [])
        tech = compute_tech_score(ohlc)          # 0..1
        sent = compute_sentiment(sym, use_news, use_twitter)  # {'score', 'news_n', 'tw_n'}
        sent_score = float(sent.get("score", 0.5))

        mix = (wT * tech + wS * sent_score) / max(1e-9, (wT + wS))
        tech_pct = f"{tech*100:.1f}%"
        sent_pct = f"{sent_score*100:.1f}%"
        mix_pct  = f"{mix*100:.1f}%"
        min_pct  = f"{thr*100:.0f}%"

        print(f"[IND] {sym} | Técnico: {tech_pct} | Sentimento: {sent_pct} "
              f"(news n={sent.get('news_n',0)}, tw n={sent.get('tw_n',0)}) | "
              f"Mix(T:{wT},S:{wS}): {mix_pct} (min {min_pct})")

        if mix >= thr and ohlc:
            signals.append({"symbol": sym, "mix": mix, "tech": tech, "sent": sent_score})

    # 3) salvar sinais (se houver)
    try:
        with open("signals.json", "w", encoding="utf-8") as f:
            json.dump({"created_at": _utcnow(), "signals": signals}, f, ensure_ascii=False, indent=2)
        print(f"🗂 {len(signals)} sinais salvos em signals.json")
    except Exception as e:
        print(f"⚠️ Falha ao salvar signals.json: {e}")

    print(f"🕒 Fim: {_utcnow()}")
    elapsed = (datetime.utcnow() - start_ts).total_seconds()
    print(f"✅ Ciclo concluído em {int(elapsed)}s. Próxima execução")

# ========= entry =========
if __name__ == "__main__":
    run_pipeline()
