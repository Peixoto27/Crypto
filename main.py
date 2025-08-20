# -*- coding: utf-8 -*-
"""
main.py — pipeline estável com fallback Binance -> CoinGecko.

- Lê moedas do env (SYMBOLS) ou usa TOP_SYMBOLS.
- Tenta pegar OHLC na Binance (se permitido). Se falhar (ex.: HTTP 451),
  usa CoinGecko com mapeamento de ids de cg_ids.json.
- Salva data_raw.json e imprime scores técnicos + sentimento (news/twitter).
- Compatível com runner.py chamando run_pipeline().

Env principais (exemplos):
  INTERVAL_MIN=20
  DAYS_OHLC=30
  MIN_BARS=60
  SYMBOLS=BTCUSDT,ETHUSDT,...   (opcional)
  TOP_SYMBOLS=30                (se SYMBOLS vazio)
  USE_BINANCE=true              (se false, pula direto pra CoinGecko)
  SAVE_HISTORY=true
  HISTORY_DIR=data/history

  # Sentimento (já usado no sentiment_analyzer.py)
  USE_NEWS=true
  USE_TWITTER=true
"""

import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

# --- utils -------------------------------------------------------------

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _getenv(name: str, default: str) -> str:
    v = os.getenv(name, default)
    return v if v is not None and v != "" else default

def _to_bool(x: str) -> bool:
    return str(x).lower() in ("1", "true", "yes", "on")

def _norm_rows(rows: Any) -> List[List[float]]:
    """
    Normaliza para lista de [ts, o, h, l, c]
    Aceita:
      - [[ts,o,h,l,c], ...]
      - [{"t":ts,"o":o,"h":h,"l":l,"c":c}, ...]
      - [{"open":...,"high":...,"low":...,"close":...,"t":...}, ...]
    """
    out: List[List[float]] = []
    if not rows:
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                out.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])])
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        for r in rows:
            t = float(r.get("t", r.get("time", 0.0)))
            o = float(r.get("o", r.get("open", 0.0)))
            h = float(r.get("h", r.get("high", 0.0)))
            l = float(r.get("l", r.get("low", 0.0)))
            c = float(r.get("c", r.get("close", 0.0)))
            out.append([t, o, h, l, c])
        return out
    return out

# --- imports do projeto -----------------------------------------------

# Binance fetcher (novo)
from data_fetcher_binance import fetch_ohlc_binance

# CoinGecko (já existente no teu projeto)
try:
    from data_fetcher_coingecko import fetch_ohlc as fetch_ohlc_cg
except Exception:
    fetch_ohlc_cg = None

# indicadores e sentimento
from apply_strategies import score_signal  # já no teu projeto
from sentiment_analyzer import get_sentiment_for_symbol  # arquivo que te enviei

# histórico granular (opcional)
try:
    from history_manager import save_ohlc_symbol
except Exception:
    def save_ohlc_symbol(*args, **kwargs):
        pass

# --- CoinGecko ID mapping ---------------------------------------------

def _load_cg_ids(path: str = "cg_ids.json") -> Dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# --- OHLC loader com fallback -----------------------------------------

def fetch_ohlc_with_fallback(symbol: str, days: int,
                             use_binance: bool,
                             cg_ids: Dict[str, str]) -> List[List[float]]:
    """
    1) Binance (se habilitado) -> lista de [ts,o,h,l,c]
    2) CoinGecko (mapeando símbolo -> id) -> lista de [ts,o,h,l,c]
    """
    # 1) Binance
    if use_binance:
        try:
            rows = fetch_ohlc_binance(symbol, days)
            rows = _norm_rows(rows)
            if rows:
                return rows
        except Exception as e:
            msg = str(e)
            if "451" in msg:
                print(f"⚠️ Binance {symbol}: HTTP 451 (bloqueado) — usando CoinGecko…")
            else:
                print(f"⚠️ Binance falhou {symbol}: {msg} — usando CoinGecko…")

    # 2) CoinGecko
    if fetch_ohlc_cg is None:
        print(f"⚠️ CoinGecko indisponível no ambiente. {symbol}")
        return []
    cg_id = cg_ids.get(symbol)
    if not cg_id:
        # heurística simples: btcusdt -> bitcoin
        guess = symbol.replace("USDT", "").lower()
        if guess == "btc": guess = "bitcoin"
        if guess == "eth": guess = "ethereum"
        cg_id = guess
    try:
        rows = fetch_ohlc_cg(cg_id, days)  # tua função já aceita id
        rows = _norm_rows(rows)
        return rows
    except Exception as e:
        print(f"⚠️ CoinGecko falhou {symbol}: {e}")
        return []

# --- safe score --------------------------------------------------------

def _safe_score(ohlc_rows: List[List[float]]) -> float:
    """
    Garante float entre 0-1, aceitando retorno float, tuple, dict.
    """
    try:
        s = score_signal(ohlc_rows)
        if isinstance(s, tuple):
            s = s[0]
        if isinstance(s, dict):
            s = s.get("score", s.get("value", 0.0))
        s = float(s)
        if s > 1.0:
            s /= 100.0
        return max(0.0, min(1.0, s))
    except Exception as e:
        print(f"⚠️ erro em score_signal: {e}")
        return 0.0

# --- pipeline ----------------------------------------------------------

def run_pipeline():
    interval_min = float(_getenv("INTERVAL_MIN", "20"))
    days = int(_getenv("DAYS_OHLC", "30"))
    min_bars = int(_getenv("MIN_BARS", "60"))
    symbols_env = [s for s in _getenv("SYMBOLS", "").replace(" ", "").split(",") if s]
    top_n = int(_getenv("TOP_SYMBOLS", "30"))
    use_binance = _to_bool(_getenv("USE_BINANCE", "false"))
    save_history = _to_bool(_getenv("SAVE_HISTORY", "true"))
    history_dir = _getenv("HISTORY_DIR", "data/history")

    # flags de sentimento (apenas exibe no header)
    news_on = _to_bool(_getenv("USE_NEWS", "true"))
    tw_on = _to_bool(_getenv("USE_TWITTER", "true"))
    ai_on = _to_bool(_getenv("USE_AI", "true"))

    print("Starting Container")
    print(f"▶️ Runner iniciado. Intervalo = {interval_min:.1f} min.")
    print(f"🔎 NEWS ativo?: {news_on} | IA ativa?: {ai_on} | Histórico ativado?: {save_history} | Twitter ativo?: {tw_on}")

    # universo de moedas
    if symbols_env:
        universe = symbols_env
    else:
        # se não houver fonte dinâmica, define uma lista padrão top
        universe = [
            "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
            "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT"
        ][:top_n]

    # remove pares estáveis redundantes (FDUSD, TUSD etc.)
    stable_suffixes = ("FDUSD", "TUSD", "USDC", "BUSD")
    removed = [s for s in universe if s.endswith(tuple(stable_suffixes))]
    if removed:
        print(f"🧠 Removidos {len(removed)} pares estáveis redundantes (ex.: {removed[0]}).")
    symbols = [s for s in universe if s not in removed]

    print(f"🧪 Moedas deste ciclo ({len(symbols)}/{len(universe)}): {', '.join(symbols[:100])}")

    cg_ids = _load_cg_ids("cg_ids.json")
    data_dict: Dict[str, List[List[float]]] = {}

    # coleta
    for sym in symbols:
        print(f"📊 Coletando OHLC {sym} (days={days})…")
        rows = fetch_ohlc_with_fallback(sym, days, use_binance, cg_ids)
        if len(rows) < min_bars:
            print(f"⚠️ {sym}: OHLC insuficiente ({len(rows)}/{min_bars})")
            continue
        # garante ordenação por tempo crescente
        rows.sort(key=lambda r: r[0])
        data_dict[sym] = rows

        # cache granular por símbolo (opcional)
        if save_history:
            try:
                os.makedirs(os.path.join(history_dir, "ohlc"), exist_ok=True)
                save_ohlc_symbol(sym, rows, history_dir=history_dir)
            except Exception as e:
                print(f"⚠️ erro ao salvar histórico {sym}: {e}")

    # salva snapshot do ciclo
    try:
        with open("data_raw.json", "w", encoding="utf-8") as f:
            json.dump({"created_at": _ts(), "data": data_dict}, f)
        print(f"💾 Salvo data_raw.json ({len(data_dict)} ativos)")
    except Exception as e:
        print(f"⚠️ erro ao salvar data_raw.json: {e}")

    # pontua e loga
    for sym, rows in data_dict.items():
        tech = _safe_score(rows)  # 0-1
        # sentimento
        try:
            sent = get_sentiment_for_symbol(sym)  # deve retornar dict {"score":0-1,"news_n":..,"tw_n":..}
            if isinstance(sent, tuple):
                # compatibilidade antiga (score,meta)
                sent_score = float(sent[0])
                news_n = sent[1].get("news_n", 0) if isinstance(sent[1], dict) else 0
                tw_n = sent[1].get("tw_n", 0) if isinstance(sent[1], dict) else 0
            elif isinstance(sent, dict):
                sent_score = float(sent.get("score", 0.5))
                news_n = int(sent.get("news_n", 0))
                tw_n = int(sent.get("tw_n", 0))
            else:
                sent_score = 0.5
                news_n = tw_n = 0
        except TypeError as te:
            # caso antigo: função não aceita kwargs/params extras
            sent_score = 0.5
            news_n = tw_n = 0
        except Exception as e:
            print(f"⚠️ [SENT] erro {sym}: {e}")
            sent_score = 0.5
            news_n = tw_n = 0

        mix = (1.5*tech + 1.0*sent_score) / 2.5
        print(f"[IND] {sym} | Técnico: {round(100*tech,1)}% | Sentimento: {round(100*sent_score,1)}% (news n={news_n}, tw n={tw_n}) | Mix(T:1.5,S:1.0): {round(100*mix,1)}% (min 70%)")

    print(f"🕒 Fim: {_ts()}")

# compatível com runner.py
if __name__ == "__main__":
    run_pipeline()
