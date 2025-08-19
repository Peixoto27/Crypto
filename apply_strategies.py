# >>> PATCH: comece colando a partir daqui em apply_strategies.py <<<

import math
import os

# --- helpers robustos ---
def _as_float(x, default=0.0):
    """Converte qualquer coisa para float com segurança: None/NaN/Inf -> default."""
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default

def _clip01(v):
    return max(0.0, min(1.0, v))

def _env_f(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default

def _env_b(name, default=False):
    v = os.getenv(name, str(default)).lower()
    return v in ("1", "true", "yes", "on")

# pesos (mantém compat com seus ENV)
W_RSI      = _env_f("W_RSI",       _env_f("RSI_WEIGHT",       1.0))
W_MACD     = _env_f("W_MACD",      _env_f("MACD_WEIGHT",      1.0))
W_EMA      = _env_f("W_EMA",       _env_f("EMA_WEIGHT",       1.0))
W_BB       = _env_f("W_BB",        _env_f("BB_WEIGHT",        0.7))
W_STOCHRSI = _env_f("W_STOCHRSI",  _env_f("STOCHRSI_WEIGHT",  0.8))
W_ADX      = _env_f("W_ADX",       _env_f("ADX_WEIGHT",       0.8))
W_ATR      = _env_f("W_ATR",       _env_f("ATR_WEIGHT",       0.0))
W_CCI      = _env_f("W_CCI",       _env_f("CCI_WEIGHT",       0.5))

# toggles (se tiver no ENV)
EN_STOCHRSI = _env_b("EN_STOCHRSI", True)
EN_ADX      = _env_b("EN_ADX",      True)
EN_ATR      = _env_b("EN_ATR",      False)
EN_CCI      = _env_b("EN_CCI",      True)

# pesos de mistura já usados no main
WEIGHT_TECH = _env_f("WEIGHT_TECH", 1.0)
WEIGHT_SENT = _env_f("WEIGHT_SENT", _env_f("SENT_WEIGHT", 1.0))  # compat


def _score_from_indicators(ind: dict) -> float:
    """
    Converte o dicionário de indicadores em um score 0..1, com
    toda entrada saneada (None/NaN -> 0.0). Não lança exception.
    """

    # saneamento
    close   = _as_float(ind.get("close"))
    rsi     = _as_float(ind.get("rsi"))
    macd    = _as_float(ind.get("macd"))          # se o seu dict usa macd/hist
    macd_h  = _as_float(ind.get("hist"), macd)    # tenta 'hist', senão usa macd
    ema20   = _as_float(ind.get("ema20"))
    ema50   = _as_float(ind.get("ema50"))
    bb_mid  = _as_float(ind.get("bb_mid"))
    bb_hi   = _as_float(ind.get("bb_hi"))
    stochK  = _as_float(ind.get("stochK"))
    stochD  = _as_float(ind.get("stochD"))
    adx     = _as_float(ind.get("adx"))
    pdi     = _as_float(ind.get("pdi"))
    mdi     = _as_float(ind.get("mdi"))
    atr_rel = _as_float(ind.get("atr_rel"))
    cci     = _as_float(ind.get("cci"))
    # extras opcionais (seguem limpando mas não obrigatórios)
    _ = _as_float(ind.get("kijun"))
    _ = _as_float(ind.get("obv_slope"))
    _ = _as_float(ind.get("mfi"))
    _ = _as_float(ind.get("willr"))

    # normalizações 0..1 bem comportadas
    # RSI: 30→0, 70→1 (clamp)
    rsi_n = _clip01((rsi - 30.0) / 40.0)

    # MACD hist: usa tanh para comprimir extremos
    macd_n = _clip01(0.5 + math.tanh(macd_h / (abs(macd_h) + 1e-9)) * 0.5)

    # EMAs: >0 se ema20 > ema50; map para 0..1 com margem suave
    ema_spread = (ema20 - ema50) / (abs(ema50) + 1e-9)
    ema_n = _clip01(0.5 + math.tanh(ema_spread * 5.0) * 0.5)

    # Bandas: quão perto do topo (close vs bb_hi vs bb_mid)
    bb_range = (bb_hi - bb_mid)
    bb_pos   = (close - bb_mid) / (abs(bb_range) + 1e-9)
    # preferimos levemente acima do meio: centraliza em 0.6
    bb_n = _clip01(0.5 + math.tanh((bb_pos - 0.2) * 2.0) * 0.5)

    # StochRSI: já é 0..1 se calculado assim; senão normaliza
    stoch_n = _clip01(stochK)

    # ADX/DMI: força de tendência * direção (pdi > mdi)
    dmi_dir = (pdi - mdi) / (abs(pdi) + abs(mdi) + 1e-9)  # -1..1
    adx_s   = _clip01(adx / 50.0)  # 50~forte
    adx_n   = _clip01(0.5 + 0.5 * dmi_dir * adx_s)

    # ATR relativo: alto ATR = risco => penaliza
    atr_n = _clip01(1.0 - atr_rel)  # se atr_rel ∈ [0..1], ótimo. se >1, clipou

    # CCI: -100..100 ~ tendência; normaliza
    cci_n = _clip01(0.5 + 0.5 * math.tanh(cci / 100.0))

    # agrega com pesos (só soma se o toggle do indicador estiver ligado)
    parts = []
    parts.append(W_RSI * rsi_n)
    parts.append(W_MACD * macd_n)
    parts.append(W_EMA * ema_n)
    parts.append(W_BB * bb_n)
    if EN_STOCHRSI:
        parts.append(W_STOCHRSI * stoch_n)
    if EN_ADX:
        parts.append(W_ADX * adx_n)
    if EN_ATR:
        parts.append(W_ATR * atr_n)
    if EN_CCI:
        parts.append(W_CCI * cci_n)

    w_sum = (
        W_RSI + W_MACD + W_EMA + W_BB +
        (W_STOCHRSI if EN_STOCHRSI else 0.0) +
        (W_ADX if EN_ADX else 0.0) +
        (W_ATR if EN_ATR else 0.0) +
        (W_CCI if EN_CCI else 0.0)
    )
    if w_sum <= 0.0:
        return 0.0

    score = sum(parts) / w_sum
    return _clip01(score)


def score_signal(ohlc_slice):
    """
    Mantém a assinatura usada no projeto.
    - Espera que outra parte do código gere o dicionário 'ind' (indicadores)
      e anexe no último candle, OU que haja uma função utilitária que
      retorne esse dicionário.
    - Aqui só garantimos que a transformação em score nunca quebre.
    """
    try:
        # 1) tenta obter o dicionário de indicadores de formas comuns no projeto
        ind = None
        # (A) se o seu pipeline já monta 'ind' no último candle:
        if isinstance(ohlc_slice, list) and ohlc_slice and isinstance(ohlc_slice[-1], dict):
            ind = ohlc_slice[-1].get("ind") or ohlc_slice[-1].get("indicators")

        # (B) fallback: se existir alguma função global de indicadores no arquivo
        if ind is None:
            try:
                # funções que vocês costumam ter: get_indicators / compute_indicators / make_indicators
                for fname in ("get_indicators", "compute_indicators", "make_indicators"):
                    f = globals().get(fname)
                    if callable(f):
                        ind = f(ohlc_slice)
                        break
            except Exception:
                ind = None

        # (C) se ainda não conseguiu, monta um dicionário mínimo só com o close,
        #     para não quebrar (o score sairá baixo, mas o ciclo segue)
        if ind is None:
            last = ohlc_slice[-1] if ohlc_slice else {}
            ind = {"close": _as_float(last.get("c") or last.get("close"), 0.0)}

        # 2) calcula score robusto
        tech = _score_from_indicators(ind)

        # 3) mistura com sentimento, se já tiver vindo no 'ind'
        sent_news = _as_float(ind.get("sent_news"), 0.5)   # 0.5 neutro
        sent_twit = _as_float(ind.get("sent_twitter"), 0.5)
        # média simples entre fontes de sentimento disponíveis
        sent_list = [v for v in (sent_news, sent_twit) if v >= 0.0]
        sent = sum(sent_list)/len(sent_list) if sent_list else 0.5

        # mistura final igual ao restante do projeto
        mix = _clip01((tech * WEIGHT_TECH + sent * WEIGHT_SENT) / (WEIGHT_TECH + WEIGHT_SENT))

        # retorna no formato que o restante do seu código espera
        return {
            "tech": tech,
            "sent": sent,
            "mix": mix,
        }
    except Exception:
        # fallback extremo: nunca quebrar o ciclo
        return {"tech": 0.0, "sent": 0.5, "mix": _clip01((0.0*WEIGHT_TECH + 0.5*WEIGHT_SENT)/(WEIGHT_TECH+WEIGHT_SENT))}

# >>> PATCH: termine de colar aqui. <<<
