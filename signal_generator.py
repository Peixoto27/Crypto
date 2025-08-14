# -*- coding: utf-8 -*-
"""
signal_generator.py
Gera score e sinal usando indicadores técnicos (básicos + extras) e, opcionalmente,
um peso de sentimento de notícias.

Requisitos que já existem no seu projeto:
- indicators.py  -> rsi, macd, ema, bollinger
- indicators_extra.py -> ichimoku, parabolic_sar, stochastic, vwap, obv   (se não existir, o módulo é ignorado)
- sentiment_analyzer.py -> get_sentiment_score(symbol)  (opcional)
- config/env via os.getenv (Railway Variables)

Retornos:
- score_signal(closes): float entre 0..1 (ou None se dados insuficientes)
- generate_signal(symbol, candles): dict com o sinal OU None

Observações:
- Tolerante: aceita 1 confirmação se o score já for alto; exige 2+ confirmações quando o score está “no limite”.
- Evita indexação inválida quando algum indicador retorna None.
"""

from statistics import fmean
import os
import time

# ------------------------------
# Config (via ENV)
# ------------------------------
SCORE_THRESHOLD   = float(os.getenv("SCORE_THRESHOLD", "0.70"))   # nota mínima do score (0..1)
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.60"))    # confiança mínima do sinal (0..1)
EXTRA_SCORE_WEIGHT= float(os.getenv("EXTRA_SCORE_WEIGHT", "0.0")) # peso de extras (0..1) no score
WEIGHT_SENT       = float(os.getenv("WEIGHT_SENT", "0.0"))        # peso de sentimento (-1..1) -> ajusta score
EXTRA_LOG         = os.getenv("EXTRA_INDICATORS_LOG", "0") == "1"

# Parametrizações do plano (TP/SL) e dados
ATR_LOOKBACK      = int(os.getenv("ATR_LOOKBACK", "15"))          # pseudo-ATR por diferenças
RISK_RR_TP        = float(os.getenv("RISK_RR_TP", "2.0"))         # alvo = 2x “faixa média”
RISK_RR_SL        = float(os.getenv("RISK_RR_SL", "1.0"))         # stop = 1x “faixa média”

# ------------------------------
# Imports internos do projeto
# ------------------------------
from indicators import rsi, macd, ema, bollinger

# Módulos extras opcionais
try:
    from indicators_extra import ichimoku, parabolic_sar, stochastic, vwap, obv
    HAS_EXTRAS = True
except Exception:
    HAS_EXTRAS = False

# Sentimento opcional
try:
    from sentiment_analyzer import get_sentiment_score
    HAS_SENTIMENT = True
except Exception:
    HAS_SENTIMENT = False


# ------------------------------
# Utilidades
# ------------------------------
def _last_safe(seq, i):
    """Retorna seq[i] caso exista e não seja None, senão None."""
    try:
        v = seq[i]
        return v if v is not None else None
    except Exception:
        return None

def _build_trade_plan(closes, risk_ratio_tp=RISK_RR_TP, risk_ratio_sl=RISK_RR_SL):
    """Plano simples usando um 'ATR-like' baseado na média das variações recentes."""
    if len(closes) < max(ATR_LOOKBACK + 1, 20):
        return None
    last = float(closes[-1])
    diffs = [abs(closes[j] - closes[j-1]) for j in range(len(closes) - ATR_LOOKBACK, len(closes))]
    atr_like = fmean(diffs) if diffs else 0.0
    if atr_like <= 0:
        return None
    sl = last - (atr_like * risk_ratio_sl)
    tp = last + (atr_like * risk_ratio_tp)
    return {"entry": last, "tp": tp, "sl": sl}


# ------------------------------
# Score (0..1)
# ------------------------------
def score_signal(closes):
    """Calcula um score agregando RSI, MACD, EMAs e Bandas de Bollinger (+ extras opcionais)."""
    if not closes or len(closes) < 60:
        return None

    i = len(closes) - 1
    c = float(closes[i])

    # --- indicadores básicos ---
    r = rsi(closes, 14)                              # lista
    macd_line, signal_line, hist = macd(closes, 12, 26, 9)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    bb_up, bb_mid, bb_low = bollinger(closes, 20, 2.0)

    r_i       = _last_safe(r, i)
    macd_i    = _last_safe(macd_line, i)
    macdsig_i = _last_safe(signal_line, i)
    hist_i    = _last_safe(hist, i)
    ema20_i   = _last_safe(ema20, i)
    ema50_i   = _last_safe(ema50, i)
    bb_low_i  = _last_safe(bb_low, i)

    # Sinais básicos (booleans)
    is_rsi_bull   = (r_i is not None) and (45 <= r_i <= 65)
    is_macd_cross = (macd_i is not None and macdsig_i is not None and
                     macd_i > macdsig_i and _last_safe(macd_line, i-1) is not None and _last_safe(signal_line, i-1) is not None and
                     macd_line[i-1] <= signal_line[i-1])
    is_trend_up   = (ema20_i is not None and ema50_i is not None and ema20_i > ema50_i)
    near_bb_low   = (bb_low_i is not None and c <= bb_low_i * 1.01)

    # Notas parciais
    s_rsi   = 1.0 if is_rsi_bull else (0.6 if (r_i is not None and 40 <= r_i <= 70) else 0.0)
    s_macd  = 1.0 if is_macd_cross else (0.7 if (hist_i is not None and hist_i > 0) else 0.2)
    s_trend = 1.0 if is_trend_up else 0.3
    s_bb    = 1.0 if near_bb_low else 0.5

    parts = [s_rsi, s_macd, s_trend, s_bb]

    # --- extras opcionais (dão um plus no score) ---
    extras_used = []
    if HAS_EXTRAS and EXTRA_SCORE_WEIGHT > 0.0:
        try:
            # Ichimoku (kumo / linha de base)
            ich = ichimoku(closes)  # deve retornar dict com chaves, ou tupla; aqui checamos no "modo simples"
            ich_bull = False
            if isinstance(ich, dict):
                # critérios simples: preço acima da nuvem e/ou kijun acima de tenkan
                cloud_top = ich.get("spanA")[-1] if ich.get("spanA") else None
                cloud_bot = ich.get("spanB")[-1] if ich.get("spanB") else None
                kijun     = ich.get("kijun")[-1]  if ich.get("kijun") else None
                tenkan    = ich.get("tenkan")[-1] if ich.get("tenkan") else None
                if cloud_top is not None and cloud_bot is not None:
                    above_cloud = c > max(cloud_top, cloud_bot)
                else:
                    above_cloud = False
                ich_bull = (above_cloud or (kijun is not None and tenkan is not None and kijun >= tenkan))
            extras_used.append(1.0 if ich_bull else 0.0)

            # SAR parabólico (tendência de alta quando preço acima do SAR)
            sar_vals = parabolic_sar(closes)
            sar_i = _last_safe(sar_vals, i)
            extras_used.append(1.0 if (sar_i is not None and c > sar_i) else 0.0)

            # Stochastic (saída de sobrevenda)
            k, d = stochastic(closes, 14, 3)
            k_i = _last_safe(k, i); d_i = _last_safe(d, i)
            stoch_bull = (k_i is not None and d_i is not None and
                          k_i > d_i and k_i < 50)  # cruzado para cima em região inferior
            extras_used.append(1.0 if stoch_bull else 0.0)

            # VWAP (preço acima do VWAP)
            vwap_vals = vwap(closes)
            vwap_i = _last_safe(vwap_vals, i)
            extras_used.append(1.0 if (vwap_i is not None and c >= vwap_i) else 0.0)

            # OBV (só se função existir — algumas impl. precisam de volumes)
            try:
                obv_vals = obv(closes)
                # tendência de alta simplificada: OBV crescente nas últimas barras
                obv_up = False
                if obv_vals and len(obv_vals) >= 3:
                    obv_up = obv_vals[-1] > obv_vals[-2] > obv_vals[-3]
                extras_used.append(1.0 if obv_up else 0.0)
            except Exception:
                pass

        except Exception as e:
            if EXTRA_LOG:
                print(f"⚠️  Extras falharam: {e}")

    # score base
    base_score = fmean(parts)

    # complementa com extras (se houver)
    if extras_used:
        extras_avg = fmean(extras_used)
        base_score = (1 - EXTRA_SCORE_WEIGHT) * base_score + EXTRA_SCORE_WEIGHT * extras_avg

    # leve normalização por volatilidade do hist do MACD (quando existir)
    try:
        recent = [abs(h) for h in hist[-20:] if h is not None]
        if recent:
            hist_i_abs = abs(hist_i) if hist_i is not None else 0.0
            vol_boost = max(0.0, min(hist_i_abs / (max(recent) + 1e-9), 1.0))
            base_score = 0.85 * base_score + 0.15 * vol_boost
    except Exception:
        pass

    # sentimento (se existir) – transforma [-1..1] em ajuste no score
    if HAS_SENTIMENT and WEIGHT_SENT != 0.0:
        try:
            # Aqui não temos o símbolo; o ajuste final será feito no generate_signal
            # Mantemos somente base_score aqui.
            pass
        except Exception:
            pass

    # clamp
    base_score = max(0.0, min(1.0, base_score))
    return base_score


# ------------------------------
# Sinal
# ------------------------------
def generate_signal(symbol, candles):
    """
    Gera um sinal para o símbolo dado um array de candles (cada candle = dict com 'close').
    Aplica:
      - score >= SCORE_THRESHOLD
      - confirmações flexíveis (3/2/1) de indicadores básicos
      - plano (entry/tp/sl)
      - sentimento opcional para ajustar confiança
    """
    if not candles or len(candles) < 60:
        if EXTRA_LOG:
            print(f"{symbol}: poucos candles ({len(candles) if candles else 0})")
        return None

    closes = [float(c.get("close")) for c in candles if "close" in c]

    sc = score_signal(closes)
    if sc is None:
        if EXTRA_LOG:
            print(f"{symbol}: score None (dados insuficientes).")
        return None

    # indicadores básicos para confirmações
    i = len(closes) - 1
    c = float(closes[i])

    r = rsi(closes, 14)
    macd_line, signal_line, hist = macd(closes, 12, 26, 9)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    bb_up, bb_mid, bb_low = bollinger(closes, 20, 2.0)

    r_i       = _last_safe(r, i)
    macd_i    = _last_safe(macd_line, i)
    macdsig_i = _last_safe(signal_line, i)
    ema20_i   = _last_safe(ema20, i)
    ema50_i   = _last_safe(ema50, i)
    bb_low_i  = _last_safe(bb_low, i)

    rsi_ok   = (r_i is not None) and (45 <= r_i <= 65)
    macd_ok  = (macd_i is not None and macdsig_i is not None and macd_i > macdsig_i)
    ema_ok   = (ema20_i is not None and ema50_i is not None and ema20_i > ema50_i)
    bb_ok    = (bb_low_i is not None and c <= bb_low_i * 1.01)

    confirmations = sum([rsi_ok, macd_ok, ema_ok, bb_ok])

    # Regra flexível: se o score for bem acima do threshold, 1 confirmação já basta.
    # Se for na margem, precisa de 2. Se for na risca, 3.
    needed = 1 if sc >= (SCORE_THRESHOLD + 0.10) else (2 if sc >= (SCORE_THRESHOLD + 0.02) else 3)

    if sc < SCORE_THRESHOLD or confirmations < needed:
        if EXTRA_LOG:
            print(f"{symbol}: score={sc:.2f}, confs={confirmations}/{needed} -> sem sinal.")
        return None

    # Plano de trade
    plan = _build_trade_plan(closes)
    if plan is None:
        if EXTRA_LOG:
            print(f"{symbol}: não conseguiu montar plano (ATR-like).")
        return None

    # Confiança inicial baseada no score
    confidence = sc

    # Bonus/pênalti de confirmações
    if confirmations >= 4:
        confidence += 0.10
    elif confirmations == 3:
        confidence += 0.05
    elif confirmations == 2:
        confidence -= 0.05
    else:  # 1
        confidence -= 0.10

    # Ajuste de sentimento (se disponível)
    if HAS_SENTIMENT and WEIGHT_SENT != 0.0:
        try:
            sent = get_sentiment_score(symbol)  # [-1..1]
            # mapeia sentimento para ajuste: positivo ajuda, negativo reduz
            confidence = (1 - abs(WEIGHT_SENT)) * confidence + (WEIGHT_SENT) * ((sent + 1.0) / 2.0)
            if EXTRA_LOG:
                print(f"🧠 Sentiment {symbol}: {sent:+.2f} -> confiança ajustada={confidence:.2f}")
        except Exception as e:
            if EXTRA_LOG:
                print(f"⚠️  Falha sentimento {symbol}: {e}")

    # clamp confiança
    confidence = max(0.0, min(1.0, confidence))

    if confidence < MIN_CONFIDENCE:
        if EXTRA_LOG:
            print(f"{symbol}: confiança {confidence:.2f} < MIN_CONFIDENCE {MIN_CONFIDENCE:.2f}")
        return None

    # monta sinal
    created_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    sig = {
        "symbol": symbol,
        "timestamp": int(time.time()),
        "confidence": round(confidence, 4),
        "entry": plan["entry"],
        "tp": plan["tp"],
        "sl": plan["sl"],
        "strategy": "RSI+MACD+EMA+BB" + ("+EXTRAS" if HAS_EXTRAS and EXTRA_SCORE_WEIGHT > 0 else ""),
        "source": "coingecko",
        "created_at": created_at,
        "id": f"{symbol}-{int(time.time())}"
    }

    if EXTRA_LOG:
        print(f"✅ {symbol}: sinal gerado | score={sc:.2f} confs={confirmations} conf={sig['confidence']:.2f}")

    return sig
