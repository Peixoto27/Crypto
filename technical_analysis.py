"""
Módulo de Análise Técnica para Criptomoedas
Implementa indicadores técnicos para geração de sinais inteligentes
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
import requests
import time
from datetime import datetime, timedelta
import json
import os


class TechnicalAnalysis:
    """
    Classe para cálculo de indicadores técnicos e geração de sinais
    """
    
    def __init__(self):
        self.coingecko_base_url = "https://api.coingecko.com/api/v3"
        self.historical_data_cache = {}
        self.CACHE_DURATION_HISTORICAL = 86400  # Cache por 24 horas (em segundos)
        self.CACHE_DIR = "/tmp/crypto_cache" # Usar /tmp para cache em disco no Railway
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    def _get_cache_file_path(self, cache_key: str) -> str:
        return os.path.join(self.CACHE_DIR, f"{cache_key}.json")

    def _load_cache_from_disk(self, cache_key: str) -> Optional[Dict]:
        file_path = self._get_cache_file_path(cache_key)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                if (time.time() - data["timestamp"]) < self.CACHE_DURATION_HISTORICAL:
                    print(f"Servindo dados de {cache_key} do cache em disco.")
                    # Converter de volta para DataFrame
                    df = pd.DataFrame(data["data"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    return {"data": df, "timestamp": data["timestamp"]}
                else:
                    print(f"Cache em disco para {cache_key} expirado.")
                    os.remove(file_path) # Remover arquivo expirado
            except Exception as e:
                print(f"Erro ao carregar cache em disco para {cache_key}: {e}")
                if os.path.exists(file_path): os.remove(file_path) # Remover arquivo corrompido
        return None

    def _save_cache_to_disk(self, cache_key: str, data: pd.DataFrame):
        file_path = self._get_cache_file_path(cache_key)
        try:
            # Converter DataFrame para formato serializável
            serializable_data = {
                "timestamp": time.time(),
                "data": data.to_dict(orient="records") # Salvar como lista de dicionários
            }
            with open(file_path, "w") as f:
                json.dump(serializable_data, f)
            print(f"Dados de {cache_key} salvos no cache em disco.")
        except Exception as e:
            print(f"Erro ao salvar cache em disco para {cache_key}: {e}")

    def get_historical_data(self, coin_id: str, days: int = 7, retry_count: int = 0) -> Optional[pd.DataFrame]:
        """
        Obtém dados históricos de preços do CoinGecko com cache em disco e retry.
        """
        cache_key = f"{coin_id}_{days}"
        
        # Tentar carregar do cache em disco
        cached_data = self._load_cache_from_disk(cache_key)
        if cached_data:
            return cached_data["data"]

        print(f"Buscando novos dados históricos para {coin_id} do CoinGecko...")
        if retry_count > 0: # Adiciona um atraso apenas em retries
            time.sleep(5) # Atraso maior para retries

        try:
            url = f"{self.coingecko_base_url}/coins/{coin_id}/market_chart"
            params = {
                "vs_currency": "usd",
                "days": days,
                "interval": "daily" if days > 1 else "hourly",
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            # Converter para DataFrame
            df = pd.DataFrame({
                "timestamp": [item[0] for item in data["prices"]],
                "price": [item[1] for item in data["prices"]],
                "market_cap": [item[1] for item in data["market_caps"]],
                "volume": [item[1] for item in data["total_volumes"]],
            })

            # Converter timestamp para datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.sort_values("timestamp").reset_index(drop=True)
            
            # Salvar no cache em disco
            self._save_cache_to_disk(cache_key, df)
            
            return df
            
        except requests.exceptions.HTTPError as http_err:
            if http_err.response.status_code == 429:
                print(f"Erro 429 Too Many Requests para {coin_id}. Tentando novamente (tentativa {retry_count + 1})...")
                if retry_count < 3: # Limita o número de retries
                    return self.get_historical_data(coin_id, days, retry_count + 1) 
                else:
                    print(f"Máximo de retries atingido para {coin_id}.")
                    return None
            else:
                print(f"Erro HTTP ao obter dados históricos para {coin_id}: {http_err}")
                return None
        except Exception as e:
            print(f"Erro ao obter dados históricos para {coin_id}: {e}")
            return None
    
    def calculate_rsi(self, prices: pd.Series, period: int = 7) -> pd.Series:
        """
        Calcula o Relative Strength Index (RSI)
        
        Args:
            prices: Série de preços
            period: Período para cálculo (padrão 7)
            
        Returns:
            Série com valores RSI
        """
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_moving_averages(self, prices: pd.Series) -> Dict[str, pd.Series]:
        """
        Calcula médias móveis simples
        
        Args:
            prices: Série de preços
            
        Returns:
            Dicionário com SMA5, SMA10, SMA20
        """
        return {
            'SMA5': prices.rolling(window=5).mean(),
            'SMA10': prices.rolling(window=10).mean(),
            'SMA20': prices.rolling(window=20).mean()
        }
    
    def calculate_bollinger_bands(self, prices: pd.Series, period: int = 7, std_dev: float = 2) -> Dict[str, pd.Series]:
        """
        Calcula Bandas de Bollinger
        
        Args:
            prices: Série de preços
            period: Período para média móvel (padrão 7)
            std_dev: Número de desvios padrão (padrão 2)
            
        Returns:
            Dicionário com upper_band, middle_band, lower_band
        """
        sma = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()
        
        return {
            'upper_band': sma + (std * std_dev),
            'middle_band': sma,
            'lower_band': sma - (std * std_dev)
        }
    
    def calculate_volatility(self, prices: pd.Series, period: int = 7) -> pd.Series:
        """
        Calcula volatilidade baseada no desvio padrão dos retornos
        
        Args:
            prices: Série de preços
            period: Período para cálculo
            
        Returns:
            Série com volatilidade
        """
        returns = prices.pct_change()
        volatility = returns.rolling(window=period).std() * np.sqrt(365)  # Anualizada
        
        return volatility
    
    def generate_signal_score(self, coin_data: Dict) -> Dict:
        """
        Gera score de sinal baseado em múltiplos indicadores
        
        Args:
            coin_data: Dados da moeda com indicadores calculados
            
        Returns:
            Dicionário com signal, confidence, target_price, stop_loss, reasons
        """
        current_price = coin_data['current_price']
        rsi = coin_data['rsi']
        sma5 = coin_data['sma5']
        sma10 = coin_data['sma10']
        sma20 = coin_data['sma20']
        bb_upper = coin_data['bb_upper']
        bb_lower = coin_data['bb_lower']
        volatility = coin_data['volatility']
        
        # Inicializar score
        buy_score = 0
        sell_score = 0
        reasons = []
        
        # Análise RSI
        if rsi < 30:  # Oversold
            buy_score += 25
            reasons.append("RSI indica sobrevendido (< 30)")
        elif rsi > 70:  # Overbought
            sell_score += 25
            reasons.append("RSI indica sobrecomprado (> 70)")
        elif 40 <= rsi <= 60:  # Neutro
            reasons.append("RSI em zona neutra")
        
        # Análise de Médias Móveis
        if current_price > sma5 > sma10 > sma20:  # Tendência de alta
            buy_score += 20
            reasons.append("Tendência de alta confirmada (preço > SMA5 > SMA10 > SMA20)")
        elif current_price < sma5 < sma10 < sma20:  # Tendência de baixa
            sell_score += 20
            reasons.append("Tendência de baixa confirmada (preço < SMA5 < SMA10 < SMA20)")
        
        # Cruzamento de médias
        if sma5 > sma10 and sma10 > sma20:  # Golden cross pattern
            buy_score += 15
            reasons.append("Padrão Golden Cross detectado")
        elif sma5 < sma10 and sma10 < sma20:  # Death cross pattern
            sell_score += 15
            reasons.append("Padrão Death Cross detectado")
        
        # Análise de Bandas de Bollinger
        if current_price <= bb_lower:  # Preço na banda inferior
            buy_score += 20
            reasons.append("Preço tocou banda inferior de Bollinger")
        elif current_price >= bb_upper:  # Preço na banda superior
            sell_score += 20
            reasons.append("Preço tocou banda superior de Bollinger")
        
        # Análise de Volatilidade
        if volatility > 0.5:  # Alta volatilidade
            buy_score -= 5
            sell_score -= 5
            reasons.append("Alta volatilidade detectada - cuidado")
        elif volatility < 0.2:  # Baixa volatilidade
            buy_score += 5
            reasons.append("Baixa volatilidade - ambiente mais estável")
        
        # Determinar sinal final
        total_score = buy_score - sell_score
        
        if total_score >= 30:
            signal = "BUY"
            confidence = min(95, 50 + abs(total_score))
            target_percentage = min(15, 3 + (abs(total_score) / 10))
            target_price = current_price * (1 + target_percentage / 100)
            stop_loss = current_price * 0.95  # 5% stop loss
        elif total_score <= -30:
            signal = "SELL"
            confidence = min(95, 50 + abs(total_score))
            target_percentage = min(15, 3 + (abs(total_score) / 10))
            target_price = current_price * (1 - target_percentage / 100)
            stop_loss = current_price * 1.05  # 5% stop loss para venda
        else:
            signal = "HOLD"
            confidence = max(60, 80 - abs(total_score))
            target_percentage = 2  # Movimento mínimo esperado
            target_price = current_price * (1 + target_percentage / 100) if total_score > 0 else current_price * (1 - target_percentage / 100)
            stop_loss = current_price * 0.97  # 3% stop loss conservador
        
        return {
            'signal': signal,
            'confidence': round(confidence, 1),
            'target_price': round(target_price, 6),
            'target_percentage': round(target_percentage, 2),
            'stop_loss': round(stop_loss, 6),
            'buy_score': buy_score,
            'sell_score': sell_score,
            'total_score': total_score,
            'reasons': reasons,
            'volatility_level': 'Alta' if volatility > 0.5 else 'Média' if volatility > 0.2 else 'Baixa'
        }
    
    def analyze_coin(self, coin_id: str, symbol: str, current_price: float) -> Dict:
        """
        Análise completa de uma moeda
        
        Args:
            coin_id: ID da moeda no CoinGecko
            symbol: Símbolo da moeda
            current_price: Preço atual
            
        Returns:
            Dicionário com análise completa
        """
        try:
            # Obter dados históricos
            df = self.get_historical_data(coin_id, days=7)
            
            if df is None or len(df) < 7: # Ajustado para 7 dias
                # Fallback para análise básica se não houver dados suficientes
                return self._basic_analysis(symbol, current_price)
            
            prices = df["price"]
            
            # Calcular indicadores
            rsi = self.calculate_rsi(prices, period=7) # Ajustado para 7 dias
            mas = self.calculate_moving_averages(prices)
            bb = self.calculate_bollinger_bands(prices)
            volatility = self.calculate_volatility(prices)
            
            # Obter valores mais recentes
            latest_data = {
                'current_price': current_price,
                'rsi': rsi.iloc[-1] if not rsi.empty else 50,
                'sma5': mas['SMA5'].iloc[-1] if not mas['SMA5'].empty else current_price,
                'sma10': mas['SMA10'].iloc[-1] if not mas['SMA10'].empty else current_price,
                'sma20': mas['SMA20'].iloc[-1] if not mas['SMA20'].empty else current_price,
                'bb_upper': bb['upper_band'].iloc[-1] if not bb['upper_band'].empty else current_price * 1.1,
                'bb_lower': bb['lower_band'].iloc[-1] if not bb['lower_band'].empty else current_price * 0.9,
                'volatility': volatility.iloc[-1] if not volatility.empty else 0.3
            }
            
            # Gerar sinal
            signal_data = self.generate_signal_score(latest_data)
            
            # Combinar todos os dados
            result = {
                'symbol': symbol,
                'coin_id': coin_id,
                'current_price': current_price,
                'analysis_timestamp': datetime.now().isoformat(),
                'indicators': {
                    'rsi': round(latest_data['rsi'], 2),
                    'sma5': round(latest_data['sma5'], 6),
                    'sma10': round(latest_data['sma10'], 6),
                    'sma20': round(latest_data['sma20'], 6),
                    'bollinger_upper': round(latest_data['bb_upper'], 6),
                    'bollinger_lower': round(latest_data['bb_lower'], 6),
                    'volatility': round(latest_data['volatility'], 4)
                },
                'signal': signal_data
            }
            
            return result
            
        except Exception as e:
            print(f"Erro na análise de {symbol}: {e}")
            return self._basic_analysis(symbol, current_price)
    
    def _basic_analysis(self, symbol: str, current_price: float) -> Dict:
        """
        Análise básica quando dados históricos não estão disponíveis
        """
        return {
            'symbol': symbol,
            'current_price': current_price,
            'analysis_timestamp': datetime.now().isoformat(),
            'indicators': {
                'rsi': 50,
                'sma5': current_price,
                'sma10': current_price,
                'sma20': current_price,
                'bollinger_upper': current_price * 1.05,
                'bollinger_lower': current_price * 0.95,
                'volatility': 0.3
            },
            'signal': {
                'signal': 'HOLD',
                'confidence': 60.0,
                'target_price': current_price * 1.02,
                'target_percentage': 2.0,
                'stop_loss': current_price * 0.97,
                'buy_score': 0,
                'sell_score': 0,
                'total_score': 0,
                'reasons': ['Dados históricos insuficientes para análise completa'],
                'volatility_level': 'Média'
            }
        }


def test_technical_analysis():
    """
    Função de teste para o módulo de análise técnica
    """
    ta = TechnicalAnalysis()
    
    # Testar com Bitcoin
    print("Testando análise técnica com Bitcoin...")
    result = ta.analyze_coin('bitcoin', 'BTC', 45000.0)
    
    print(f"Símbolo: {result['symbol']}")
    print(f"Preço atual: ${result['current_price']:,.2f}")
    print(f"RSI: {result['indicators']['rsi']}")
    print(f"Sinal: {result['signal']['signal']}")
    print(f"Confiança: {result['signal']['confidence']}% ")
    print(f"Preço alvo: ${result['signal']['target_price']:,.2f}")
    print(f"Razões: {', '.join(result['signal']['reasons'])}")


if __name__ == "__main__":
    test_technical_analysis()



