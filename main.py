# -*- coding: utf-8 -*-
"""
main.py — orquestrador do ciclo de varredura
- Busca OHLC (CoinGecko) dos símbolos
- Salva data_raw.json
- Calcula score técnico e de sentimento (NewsData + Twitter, se habilitados)
- Gera e envia sinais (se a sua generate_signal fizer isso)
- Exposta a função run_pipeline() para o runner.py
"""

from __future__ import annotations
import os, json, time, math
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

# =========================
# Imports dos seus módulos
# =========================
# fetch_ohlc(symbol, days) deve retornar [[ts, o, h, l, c], ...]
# fetch_top_symbols(n) retorna lista de símbolos (ex.: ["BTCUSDT", ...])
try:
    from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
except Exception:
    fetch_ohlc = None
    fetch_top_symbols = None

# score_signal(ohlc_dicts)->float ou dict{"score":..}
# generate_signal(ohlc_dicts)->dict com {entry,tp,sl,confidence,strategy}
try:
    from apply_strategies import score_signal, generate_signal
except Exception:
    def score_signal(_): return 0.0
    def generate_signal(_): return None

# analisador de sentimento (News + Twitter) – opcional
# get_sentiment(symbol)-> dict {"score":0..1,"news_n":int,"tw_n":int}
try:
    from sentiment_analyzer import get_sentiment_score as _get_sentiment_core
except Exception:
    _get_sentiment_core = None

# histórico local (opcional)
try:
    from history_manager import save_cycle_ohlc
except Exception:
    def save_cycle_ohlc(*args, **kwargs):  # no-op
        return

# notifier (v1 ou v2 — tanto faz o nome do arquivo, contanto que expose send_telegram_message)
try:
    from notifier_telegram_v2 import send_telegram_message
except Exception:
    try:
        from notifier_telegram import send_telegram_message
    except Exception:
        def send_telegram_message(*args, **kwargs): return False, "notifier indisponível"


# =========================
# Helpers
# =========================
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)

def _as_bool(val: str) -> bool:
    return str(val).lower() in ("1", "true", "yes", "y", "on")

def _norm_rows(rows: Any) -> List[Dict[str, float]]:
    """Normaliza [[ts,o,h,l,c],...] -> [{"t":..,"o":..,"h":..,"l":..,"c":..},...]"""
    out: List[Dict[str, float]] = []
    if not rows:
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]),
                            "l": float(r[3]), "c": float(r[4])})
    elif isinstance(rows, list) and rows and isinstance(rows[0], dict):
        for r in rows:
            out.append({
                "t": float(r.get("t", 0.0)),
                "o": float(r.get("o", r.get("open", 0.0))),
                "h": float(r.get("h", r.get("high", 0.0))),
                "l": float(r.get("l", r.get("low", 0.0))),
                "c": float(r.get("c", r.get("close", 0.0))),
            })
    return out

def _safe_score_tech(ohlc: List[Dict[str, float]]) -> Tuple[float, Optional[str]]:
    """Protege contra retornos diferentes do score_signal e erros."""
    try:
        s = score_signal(ohlc)
        if isinstance(s, dict):
            s = s.get("score", s.get("value", 0.0))
        s = float(s)
        if s > 1.0:  # caso seja porcentagem
            s /= 100.0
        s = max(0.0, min(1.0, s))
        return s, None
    except Exception as e:
        return 0.0, f"[IND] erro em score_signal: {e}"

def _get_sentiment(symbol: str) -> Tuple[float, int, int]:
    """
    Retorna (score, news_n, tw_n) em [0..1].
    Se o módulo não existir, retorna 0.5 neutro, n=0.
    """
    # ver flags
    news_on     = bool(_get_env("NEWS_API_KEY"))
    twitter_on  = _as_bool(_get_env("TWITTER_USE", "false"))

    if _get_sentiment_core is None or (not news_on and not twitter_on):
        return 0.5, 0, 0

    try:
        res = _get_sentiment_core(symbol)
        if isinstance(res, dict):
            score  = float(res.get("score", 0.5))
            n_news = int(res.get("news_n", 0))
            n_tw   = int(res.get("tw_n", 0))
        elif isinstance(res, tuple) and len(res) >= 3:
            score, n_news, n_tw = float(res[0]), int(res[1]), int(res[2])
        else:
            score, n_news, n_tw = 0.5, 0, 0

        # normaliza
        if score > 1.0:
            score /= 100.0
        score = max(0.0, min(1.0, score))
        return score, n_news, n_tw
    except Exception:
        return 0.5, 0, 0

def _print_status_header():
    news_on     = bool(_get_env("NEWS_API_KEY"))
    ia_on       = _as_bool(_get_env("AI_USE", "true"))
    hist_on     = _as_bool(_get_env("SAVE_HISTORY", "true"))
    tw_on       = _as_bool(_get_env("TWITTER_USE", "false"))
    print(f"🔎 NEWS ativo?: {str(news_on)} | IA ativa?: {str(ia_on)} | Histórico ativado?: {str(hist_on)} | Twitter ativo?: {str(tw_on)}")


# =========================
# Núcleo do ciclo
# =========================
def run_pipeline():
    """Executa UM ciclo completo. (Chamado pelo runner.py)"""
    try:
        interval_min = float(_get_env("INTERVAL_MIN", "20"))
        print(f"▶️ Runner iniciado. Intervalo = {interval_min:.1f} min.")
    except Exception:
        print("▶️ Runner iniciado. Intervalo = 20.0 min.")
    _print_status_header()

    # Configs
    days         = int(float(_get_env("DAYS_OHLC", "30")))
    min_bars     = int(float(_get_env("MIN_BARS", "180")))
    thr_mix      = float(_get_env("SCORE_THRESHOLD", "0.70"))     # limiar final para sinal
    weight_tech  = float(_get_env("WEIGHT_TECH", "1.5"))
    weight_sent  = float(_get_env("WEIGHT_SENT", "1.0"))

    # Universo de símbolos
    syms_env = [s for s in _get_env("SYMBOLS", "").replace(" ", "").split(",") if s]
    if syms_env:
        universe = syms_env
    else:
        n = int(_get_env("TOP_SYMBOLS", "93"))
        universe = fetch_top_symbols(n) if fetch_top_symbols else []
    # Remoção opcional de estáveis redundantes (ex.: FDUSDUSDT, BUSDUSDT etc.)
    STABLE_FILTER = _as_bool(_get_env("FILTER_STABLES", "true"))
    if STABLE_FILTER:
        st = ("BUSDUSDT","FDUSDUSDT","USDCUSDT","TUSDUSDT","USDPUSDT","DAIUSDT")
        removed = [s for s in universe if s in st]
        if removed:
            print(f"🧠 Removidos {len(removed)} pares estáveis redundantes (ex.: {removed[0]}).")
        universe = [s for s in universe if s not in st]

    # Quantos por ciclo
    per_cycle = int(_get_env("PAIRS_PER_CYCLE", "8"))
    # cursor simples com base na hora — suficientemente estável para produção
    idx_base = int(datetime.utcnow().timestamp() // (interval_min*60)) if (interval_min := float(_get_env("INTERVAL_MIN", "20"))) else 0
    start = (idx_base * per_cycle) % max(1, len(universe))
    symbols = universe[start:start+per_cycle]
    print(f"🧪 Moedas deste ciclo ({len(symbols)}/{len(universe)}): {', '.join(symbols) if symbols else '—'}")

    if not symbols:
        print("❌ Nenhum ativo com OHLC suficiente.")
        print("✅ Ciclo concluído em 0s. Próxima execução")
        return

    # coleta OHLC
    data_map: Dict[str, List[List[float]]] = {}
    collected_norm: Dict[str, List[Dict[str, float]]] = {}
    for sym in symbols:
        try:
            print(f"📊 Coletando OHLC {sym} (days={days})…")
            rows = []
            if fetch_ohlc is None:
                raise RuntimeError("fetch_ohlc indisponível")
            rows = fetch_ohlc(sym, days)  # esta função já implementa 429/backoff nos seus módulos
            norm = _norm_rows(rows)
            if len(norm) >= min_bars:
                data_map[sym] = rows
                collected_norm[sym] = norm
                print(f"   → OK | candles={len(norm)}")
            else:
                print(f"❌ Dados insuficientes para {sym} (candles={len(norm)}/{min_bars})")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    # salva data_raw.json
    if collected_norm:
        out_json = {
            "saved_at": _ts(),
            "symbols": list(collected_norm.keys()),
            "data": {k: data_map.get(k, []) for k in collected_norm.keys()}
        }
        with open("data_raw.json", "w", encoding="utf-8") as f:
            json.dump(out_json, f, ensure_ascii=False)
        print(f"💾 Salvo data_raw.json ({len(collected_norm)} ativos)")

        # salva histórico (um arquivo por símbolo) se habilitado
        if _as_bool(_get_env("SAVE_HISTORY", "true")):
            try:
                save_cycle_ohlc(collected_norm)  # history_manager.py
            except Exception:
                pass
    else:
        print("❌ Nenhum ativo válido para salvar.")
        return

    # IA / Sinais
    ia_on = _as_bool(_get_env("AI_USE", "true"))
    min_pct = int(round(thr_mix * 100))
    sent_logs = _as_bool(_get_env("DEBUG_INDICATORS", "false"))

    saved_signals = []
    for sym, ohlc in collected_norm.items():
        # técnico
        tech, tech_err = _safe_score_tech(ohlc)
        # sentimento
        sent, n_news, n_tw = _get_sentiment(sym)
        # mistura
        mix = (tech * weight_tech + sent * weight_sent) / max(1e-9, (weight_tech + weight_sent))

        pct_tech = f"{tech*100:.1f}%"
        pct_sent = f"{sent*100:.1f}%"
        pct_mix  = f"{mix*100:.1f}%"

        if tech_err:
            print(tech_err)

        if sent_logs:
            last = ohlc[-1]
            dbg = (f"[IND] close={last['c']:.2f} | score={pct_tech}")
            print(dbg)

        print(f"[IND] {sym} | Técnico: {pct_tech} | Sentimento: {pct_sent} (news n={n_news}, tw n={n_tw}) | "
              f"Mix(T:{weight_tech:.1f},S:{weight_sent:.1f}): {pct_mix} (min {min_pct}%)")

        # sinal somente se IA ativa e mix >= threshold
        if ia_on and mix >= thr_mix:
            try:
                sig = generate_signal(ohlc) or {}
            except Exception:
                sig = {}

            # normaliza preços em USD (não notação científica)
            entry = float(sig.get("entry", ohlc[-1]["c"]))
            tp    = float(sig.get("tp",   entry * 1.02))
            sl    = float(sig.get("sl",   entry * 0.99))
            conf  = float(sig.get("confidence", mix))
            strat = sig.get("strategy", "RSI+MACD+EMA+BB+STOCHRSI+ADX+CCI+ICHI+OBV+MFI+WILLR")

            msg = (
                f"📢 **Novo sinal** para **{sym}**\n"
                f"🎯 **Entrada:** ${entry:.6f}\n"
                f"🎯 **Alvo:**   ${tp:.6f}\n"
                f"🛑 **Stop:**   ${sl:.6f}\n"
                f"📈 **R:R:** {abs((tp-entry)/max(1e-9, entry-sl)):.1f}\n"
                f"📊 **Confiança:** {conf*100:.2f}%\n"
                f"🧠 **Estratégia:** {strat}\n"
                f"🗞 **Sentimento:** {pct_sent} (news n={n_news}, tw n={n_tw})\n"
                f"📅 **Criado:** { _ts() }"
            )
            ok, err = send_telegram_message(msg)
            if not ok:
                print(f"⚠️ Falha ao enviar Telegram: {err}")
            saved_signals.append({
                "symbol": sym, "created_at": _ts(),
                "entry": entry, "tp": tp, "sl": sl,
                "confidence": conf, "mix": mix,
                "sentiment": sent, "news_n": n_news, "tw_n": n_tw
            })

    # persiste signals.json
    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump(saved_signals, f, ensure_ascii=False, indent=2)
    print(f"🗂 {len(saved_signals)} sinais salvos em signals.json")
    print(f"🕒 Fim: { _ts() }")


# =========================
# Execução direta (sem runner)
# =========================
if __name__ == "__main__":
    # Loop interno opcional quando rodar direto: python main.py
    try:
        interval_min = float(_get_env("INTERVAL_MIN", "20"))
    except Exception:
        interval_min = 20.0

    while True:
        run_pipeline()
        wait = int(round(interval_min * 60))
        print(f"✅ Ciclo concluído em {wait}s. Próxima execução")
        time.sleep(wait)
