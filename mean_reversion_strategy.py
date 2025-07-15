import numpy as np
import pandas as pd
from typing import Tuple
from ..models import Signal

class MeanReversionStrategy:
    """Estratégia profissional de mean reversion com filtros"""
    
    def __init__(self, rsi_period: int = 14, bb_period: int = 20):
        self.rsi_period = rsi_period
        self.bb_period = bb_period
        
    def analyze(self, prices: pd.Series) -> Tuple[bool, Signal]:
        """Analisa condições de entrada"""
        # 1. Calcular indicadores
        rsi = self._calculate_rsi(prices)
        bb_upper, bb_lower, bb_mid = self._calculate_bollinger(prices)
        
        current_price = prices.iloc[-1]
        volatility = self._calculate_volatility(prices)
        
        # 2. Condições de entrada
        buy_condition = (
            (current_price <= bb_lower) and 
            (rsi < 30) and 
            (volatility > 0.02)
        )
        
        sell_condition = (
            (current_price >= bb_upper) and 
            (rsi > 70) and 
            (volatility > 0.02)
        )
        
        # 3. Gerar sinal com confiança
        if buy_condition:
            confidence = self._calculate_confidence(rsi, current_price, bb_lower)
            target = current_price * 1.05  # 5% target
            return True, Signal(
                type='BUY',
                confidence=confidence,
                price=current_price,
                target_price=target,
                stop_loss=bb_lower * 0.98
            )
        
        if sell_condition:
            confidence = self._calculate_confidence(rsi, current_price, bb_upper, False)
            target = current_price * 0.95  # 5% target
            return True, Signal(
                type='SELL',
                confidence=confidence,
                price=current_price,
                target_price=target,
                stop_loss=bb_upper * 1.02
            )
        
        return False, None
    
    def _calculate_confidence(self, rsi: float, price: float, 
                            band: float, is_buy: bool = True) -> float:
        """Calcula confiança baseada em distância da banda e RSI"""
        if is_buy:
            rsi_factor = max(0, (30 - rsi) / 30)
            band_factor = max(0, (band - price) / band)
        else:
            rsi_factor = max(0, (rsi - 70) / 30)
            band_factor = max(0, (price - band) / band)
            
        return min(95, 70 + (rsi_factor + band_factor) * 25)