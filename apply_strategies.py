# -*- coding: utf-8 -*-
from typing import Dict, Any, Tuple, List
import math
from datetime import datetime

#
# Este arquivo mantém duas funções compatíveis com o seu projeto:
# - score_signal(ohlc, ...) -> (score_float, details_dict)
# - generate_signal(ohlc)   -> dict com entry/tp/sl/rr/strategy/created_at
#
# A assinatura de score_signal agora aceita **kwargs para tolerar
# o parâmetro 'extra_log' (quando enviado pelo main), sem quebrar.


def _sma(values: List[float], n: int) -> float:
    n = max(1, int(n))
    if len(values) < n:
        return sum(values) / max(1, len(values))
    return sum(values[-n:]) / float(n)


def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 70.0
    rs = gains / losses
    rsi = 100 - (100 / (1 + rs))
    return max(0.0, min(100.0, rsi))


def score_signal(
    ohlc: list,
    min_confidence: float = 0.6,
    extra_weight: float = 0.0,
    **kwargs
) -> Tuple[float, Dict[str, Any]]:
    """
    Retorna (score, details). 'score' ∈ [0..1].
    Aceita **kwargs para ignorar 'extra_log' sem quebrar.
    """
    # aceita e ignora 'extra_log' se vier
    _ = bool(kwargs.get("extra_log", False))

    closes = [c["close"] for c in ohlc]
    if len(closes) < 20:
        return 0.0, {"reason": "few_bars"}

    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50) if len(closes) >= 50 else _sma(closes, len(closes))
    rsi14 = _rsi(closes, 14)
    last = closes[-1]

    score = 0.0
    # tendência: preço acima da média curta
    if last > sma20:
        score += 0.35
    # cruzamento simples: sma20 acima da sma50
    if sma20 > sma50:
        score += 0.35
    # momentum via RSI
    if rsi14 >= 50:
        score += 0.25
    # peso extra (opcional)
    score += max(0.0, float(extra_weight))

    score = max(0.0, min(1.0, score))
    details = {
        "last": last,
        "sma20": sma20,
        "sma50": sma50,
        "rsi14": rsi14,
        "min_confidence": min_confidence,
        "extra_weight": extra_weight,
    }
    return score, details


def generate_signal(ohlc: list) -> Dict[str, Any]:
    """
    Gera um plano simples RR=2.0 baseado no último close.
    Seu projeto já formata e envia; aqui só padronizamos.
    """
    closes = [c["close"] for c in ohlc]
    last = closes[-1] if closes else 0.0
    if last <= 0:
        return {}

    # alvo ~ +1.5%, stop ~ -1.0% como exemplo conservador
    entry = float(last)
    tp    = round(entry * 1.015, 6)
    sl    = round(entry * 0.99, 6)
    rr    = 2.0

    return {
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "strategy": "RSI+MA (lite)",
        "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
