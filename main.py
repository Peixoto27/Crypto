# -*- coding: utf-8 -*-
"""
main.py — estável com fallback e mapeamento CoinGecko robusto.

- Tenta Binance se USE_BINANCE=true. Se falhar (HTTP 451, etc), usa CoinGecko.
- Resolve o id do CoinGecko via:
    a) cg_ids.json (se existir)
    b) CG_ID_MAP (cg_id_map.py)
    c) heurística (btc->bitcoin, eth->ethereum)
- Limita universo e pula pares estáveis redundantes.
"""

import os, json, time
from datetime import datetime
from typing import List, Dict, Any

def _ts(): return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
def _getenv(k,d): 
    v=os.getenv(k,d); 
    return v if v not in (None,"") else d
def _to_bool(x): return str(x).lower() in ("1","true","yes","on")

# --- normalizador ------------------------------------------------------
def _norm_rows(rows):
    out=[]
    if not rows: return out
    if isinstance(rows,list) and rows and isinstance(rows[0],list):
        for r in rows:
            if len(r)>=5: out.append([float(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4])])
        return out
    if isinstance(rows,list) and rows and isinstance(rows[0],dict):
        for r in rows:
            t=float(r.get("t",r.get("time",0.0)))
            o=float(r.get("o",r.get("open",0.0)))
            h=float(r.get("h",r.get("high",0.0)))
            l=float(r.get("l",r.get("low",0.0)))
            c=float(r.get("c",r.get("close",0.0)))
            out.append([t,o,h,l,c])
        return out
    return out

# --- imports do projeto ------------------------------------------------
from data_fetcher_binance import fetch_ohlc_binance
try:
    from data_fetcher_coingecko import fetch_ohlc as fetch_ohlc_cg
except Exception:
    fetch_ohlc_cg=None

from apply_strategies import score_signal
from sentiment_analyzer import get_sentiment_for_symbol

try:
    from history_manager import save_ohlc_symbol
except Exception:
    def save_ohlc_symbol(*a,**k): pass

# mapa estático grande
from cg_id_map import CG_ID_MAP

def _load_cg_ids(path="cg_ids.json")->Dict[str,str]:
    try:
        with open(path,"r",encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _resolve_cg_id(symbol:str, cg_ids:Dict[str,str])->str:
    if symbol in cg_ids: 
        return cg_ids[symbol]
    if symbol in CG_ID_MAP:
        return CG_ID_MAP[symbol]
    base=symbol.upper().replace("USDT","")
    guess=base.lower()
    if guess=="btc": return "bitcoin"
    if guess=="eth": return "ethereum"
    if guess=="bnb": return "binancecoin"
    if guess=="xrp": return "ripple"
    if guess=="sol": return "solana"
    if guess=="ada": return "cardano"
    if guess=="trx": return "tron"
    if guess=="avax": return "avalanche-2"
    return guess  # última tentativa

def fetch_ohlc_with_fallback(symbol:str, days:int, use_binance:bool, cg_ids:Dict[str,str]):
    # 1) Binance
    if use_binance:
        try:
            rows=fetch_ohlc_binance(symbol, days)
            rows=_norm_rows(rows)
            if rows: return rows
        except Exception as e:
            msg=str(e)
            if "451" in msg:
                print(f"⚠️ Binance {symbol}: HTTP 451 (bloqueado) — usando CoinGecko…")
            else:
                print(f"⚠️ Binance falhou {symbol}: {msg} — usando CoinGecko…")
    # 2) CoinGecko
    if fetch_ohlc_cg is None:
        print(f"⚠️ CoinGecko indisponível. {symbol}")
        return []
    cg_id=_resolve_cg_id(symbol, cg_ids)
    try:
        rows=fetch_ohlc_cg(cg_id, days)
        return _norm_rows(rows)
    except Exception as e:
        print(f"⚠️ CoinGecko falhou {symbol}: {e}\nCoinGecko para {cg_id}. Adicione em cg_ids.json.")
        return []

def _safe_score(rows:List[List[float]])->float:
    try:
        s=score_signal(rows)
        if isinstance(s,tuple): s=s[0]
        if isinstance(s,dict): s=s.get("score", s.get("value",0.0))
        s=float(s)
        if s>1.0: s/=100.0
        return max(0.0,min(1.0,s))
    except Exception as e:
        print(f"⚠️ erro em score_signal: {e}")
        return 0.0

def run_pipeline():
    interval_min=float(_getenv("INTERVAL_MIN","20"))
    days=int(_getenv("DAYS_OHLC","30"))
    min_bars=int(_getenv("MIN_BARS","60"))
    top_n=int(_getenv("TOP_SYMBOLS","30"))
    use_binance=_to_bool(_getenv("USE_BINANCE","false"))
    save_history=_to_bool(_getenv("SAVE_HISTORY","true"))
    history_dir=_getenv("HISTORY_DIR","data/history")

    news_on=_to_bool(_getenv("USE_NEWS","true"))
    tw_on=_to_bool(_getenv("USE_TWITTER","true"))
    ai_on=_to_bool(_getenv("USE_AI","true"))

    # universo (reduzido p/ estabilidade)
    symbols_env=[s for s in _getenv("SYMBOLS","").replace(" ","").split(",") if s]
    if symbols_env:
        universe=symbols_env
    else:
        universe=[
            "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
            "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT",
            "UNIUSDT","BCHUSDT","ETCUSDT","APTUSDT","IMXUSDT","FILUSDT","NEARUSDT","OPUSDT",
            "XLMUSDT","HBARUSDT","INJUSDT","ARBUSDT","LDOUSDT","ATOMUSDT","STXUSDT",
            "RNDRUSDT","ICPUSDT","PEPEUSDT","CROUSDT","MKRUSDT","TAOUSDT","AAVEUSDT",
            "SANDUSDT","QNTUSDT","EGLDUSDT","MINAUSDT","THETAUSDT","GALAUSDT","FTMUSDT","FLOWUSDT",
            "SEIUSDT","ORDIUSDT","CHZUSDT","KASUSDT","MANAUSDT"
        ][:top_n]

    # remove pares estáveis
    stable=("FDUSD","TUSD","USDC","BUSD")
    removed=[s for s in universe if s.endswith(stable)]
    if removed:
        print(f"🧠 Removidos {len(removed)} pares estáveis redundantes (ex.: {removed[0]}).")
    symbols=[s for s in universe if s not in removed]

    print("Starting Container")
    print(f"▶️ Runner iniciado. Intervalo = {interval_min:.1f} min.")
    print(f"🔎 NEWS ativo?: {news_on} | IA ativa?: {ai_on} | Histórico ativado?: {save_history} | Twitter ativo?: {tw_on}")
    print(f"🧪 Moedas deste ciclo ({len(symbols)}/{len(universe)}): {', '.join(symbols)}")

    cg_ids=_load_cg_ids("cg_ids.json")
    data:Dict[str,List[List[float]]]={}

    for sym in symbols:
        print(f"📊 Coletando OHLC {sym} (days={days})…")
        rows=fetch_ohlc_with_fallback(sym, days, use_binance, cg_ids)
        if len(rows)<min_bars:
            print(f"⚠️ {sym}: OHLC insuficiente ({len(rows)}/{min_bars})")
            continue
        rows.sort(key=lambda r:r[0])
        data[sym]=rows
        if save_history:
            try:
                os.makedirs(os.path.join(history_dir,"ohlc"), exist_ok=True)
                save_ohlc_symbol(sym, rows, history_dir=history_dir)
            except Exception as e:
                print(f"⚠️ erro ao salvar histórico {sym}: {e}")

    with open("data_raw.json","w",encoding="utf-8") as f:
        json.dump({"created_at":_ts(),"data":data}, f)
    print(f"💾 Salvo data_raw.json ({len(data)} ativos)")

    for sym,rows in data.items():
        tech=_safe_score(rows)
        try:
            sent=get_sentiment_for_symbol(sym)
            if isinstance(sent,tuple):
                sent_score=float(sent[0]); news_n=sent[1].get("news_n",0); tw_n=sent[1].get("tw_n",0)
            elif isinstance(sent,dict):
                sent_score=float(sent.get("score",0.5)); news_n=int(sent.get("news_n",0)); tw_n=int(sent.get("tw_n",0))
            else:
                sent_score=0.5; news_n=tw_n=0
        except Exception as e:
            print(f"⚠️ [SENT] erro {sym}: {e}")
            sent_score=0.5; news_n=tw_n=0

        mix=(1.5*tech + 1.0*sent_score)/2.5
        print(f"[IND] {sym} | Técnico: {round(100*tech,1)}% | Sentimento: {round(100*sent_score,1)}% (news n={news_n}, tw n={tw_n}) | Mix(T:1.5,S:1.0): {round(100*mix,1)}% (min 70%)")

    print(f"🕒 Fim: {_ts()}")

if __name__=="__main__":
    run_pipeline()
