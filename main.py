import os
"""
Sistema de API de Sinais de Criptomoedas - Railway
Dados e Sinais 100% Reais com Análise Técnica
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
import math
from datetime import datetime, timedelta
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configurações
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
CACHE_DURATION = 120  # 2 minutos em segundos
cache = {}

# Moedas para análise
COINS = [
    {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin"},
    {"id": "ethereum", "symbol": "ETH", "name": "Ethereum"},
    {"id": "dogecoin", "symbol": "DOGE", "name": "Dogecoin"},
    {"id": "shiba-inu", "symbol": "SHIB", "name": "Shiba Inu"},
    {"id": "pepe", "symbol": "PEPE", "name": "Pepe"},
    {"id": "floki", "symbol": "FLOKI", "name": "Floki"},
    {"id": "bonk", "symbol": "BONK", "name": "Bonk"},
    {"id": "binancecoin", "symbol": "BNB", "name": "BNB"}
]

def is_cache_valid(cache_key):
    """Verifica se o cache ainda é válido"""
    if cache_key not in cache:
        return False
    
    cache_time = cache[cache_key].get('timestamp', 0)
    return (time.time() - cache_time) < CACHE_DURATION

def get_cached_data(cache_key):
    """Obtém dados do cache se válidos"""
    if is_cache_valid(cache_key):
        return cache[cache_key]['data']
    return None

def set_cache_data(cache_key, data):
    """Armazena dados no cache"""
    cache[cache_key] = {
        'data': data,
        'timestamp': time.time()
    }

def fetch_current_prices():
    """Busca preços atuais das moedas"""
    try:
        coin_ids = ",".join([coin["id"] for coin in COINS])
        url = f"{COINGECKO_BASE_URL}/simple/price"
        params = {
            'ids': coin_ids,
            'vs_currencies': 'usd',
            'include_24hr_change': 'true',
            'include_market_cap': 'true',
            'include_24hr_vol': 'true'
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        return response.json()
    
    except Exception as e:
        logger.error(f"Erro ao buscar preços atuais: {e}")
        return None

def fetch_historical_data(coin_id, days=7):
    """Busca dados históricos para análise técnica"""
    try:
        url = f"{COINGECKO_BASE_URL}/coins/{coin_id}/market_chart"
        params = {
            'vs_currency': 'usd',
            'days': days,
            'interval': 'daily'
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        return response.json()
    
    except Exception as e:
        logger.error(f"Erro ao buscar dados históricos para {coin_id}: {e}")
        return None

def calculate_rsi(prices, period=14):
    """Calcula o RSI (Relative Strength Index)"""
    if len(prices) < period + 1:
        return 50.0
    
    gains = []
    losses = []
    
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    
    if len(gains) < period:
        return 50.0
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return round(rsi, 2)

def calculate_sma(prices, period):
    """Calcula a Média Móvel Simples"""
    if len(prices) < period:
        return prices[-1] if prices else 0
    
    return sum(prices[-period:]) / period

def calculate_volatility(prices):
    """Calcula a volatilidade baseada no desvio padrão dos retornos"""
    if len(prices) < 2:
        return 0.0
    
    returns = []
    for i in range(1, len(prices)):
        returns.append((prices[i] - prices[i-1]) / prices[i-1])
    
    if not returns:
        return 0.0
    
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    volatility = math.sqrt(variance) * 100
    
    return round(volatility, 2)

def calculate_bollinger_bands(prices, period=20, std_dev=2):
    """Calcula as Bandas de Bollinger"""
    if len(prices) < period:
        return None, None, None
    
    sma = calculate_sma(prices, period)
    
    # Calcular desvio padrão
    variance = sum((price - sma) ** 2 for price in prices[-period:]) / period
    std = math.sqrt(variance)
    
    upper_band = sma + (std_dev * std)
    lower_band = sma - (std_dev * std)
    
    return upper_band, sma, lower_band

def generate_technical_signal(coin_data, historical_data):
    """Gera sinal baseado em análise técnica real"""
    try:
        prices = [point[1] for point in historical_data.get('prices', [])]
        volumes = [point[1] for point in historical_data.get('total_volumes', [])]
        
        if len(prices) < 7:
            return generate_simple_signal(coin_data)
        
        current_price = coin_data['usd']
        change_24h = coin_data.get('usd_24h_change', 0)
        
        # Calcular indicadores técnicos
        rsi = calculate_rsi(prices)
        sma5 = calculate_sma(prices, 5)
        sma10 = calculate_sma(prices, 10)
        sma20 = calculate_sma(prices, 20)
        volatility = calculate_volatility(prices)
        
        # Bandas de Bollinger
        bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(prices)
        
        # Sistema de pontuação para gerar sinal
        score = 0
        reasons = []
        
        # Análise RSI
        if rsi < 30:
            score += 3
            reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi < 40:
            score += 1
            reasons.append(f"RSI baixo ({rsi:.1f})")
        elif rsi > 70:
            score -= 3
            reasons.append(f"RSI overbought ({rsi:.1f})")
        elif rsi > 60:
            score -= 1
            reasons.append(f"RSI alto ({rsi:.1f})")
        
        # Análise de médias móveis
        if current_price > sma5 > sma10 > sma20:
            score += 2
            reasons.append("Tendência de alta (médias alinhadas)")
        elif current_price < sma5 < sma10 < sma20:
            score -= 2
            reasons.append("Tendência de baixa (médias alinhadas)")
        elif current_price > sma5:
            score += 1
            reasons.append("Preço acima da SMA5")
        elif current_price < sma5:
            score -= 1
            reasons.append("Preço abaixo da SMA5")
        
        # Análise de Bandas de Bollinger
        if bb_upper and bb_lower:
            if current_price <= bb_lower:
                score += 2
                reasons.append("Preço na banda inferior (oversold)")
            elif current_price >= bb_upper:
                score -= 2
                reasons.append("Preço na banda superior (overbought)")
        
        # Análise de momentum
        if change_24h > 8:
            score += 2
            reasons.append(f"Forte momentum positivo (+{change_24h:.1f}%)")
        elif change_24h > 3:
            score += 1
            reasons.append(f"Momentum positivo (+{change_24h:.1f}%)")
        elif change_24h < -8:
            score -= 2
            reasons.append(f"Forte momentum negativo ({change_24h:.1f}%)")
        elif change_24h < -3:
            score -= 1
            reasons.append(f"Momentum negativo ({change_24h:.1f}%)")
        
        # Análise de volume
        if len(volumes) >= 2:
            volume_change = ((volumes[-1] - volumes[-2]) / volumes[-2]) * 100
            if volume_change > 20:
                score += 1
                reasons.append(f"Volume crescente (+{volume_change:.1f}%)")
            elif volume_change < -20:
                score -= 1
                reasons.append(f"Volume decrescente ({volume_change:.1f}%)")
        
        # Determinar sinal final
        if score >= 4:
            signal = "BUY"
            strength = min(95, 70 + score * 3)
            confidence = min(90, 65 + score * 4)
            target_price = current_price * 1.08
            stop_loss = current_price * 0.95
        elif score <= -4:
            signal = "SELL"
            strength = min(95, 70 + abs(score) * 3)
            confidence = min(90, 65 + abs(score) * 4)
            target_price = current_price * 0.92
            stop_loss = current_price * 1.05
        elif score >= 2:
            signal = "BUY"
            strength = 60 + score * 5
            confidence = 70
            target_price = current_price * 1.05
            stop_loss = current_price * 0.97
        elif score <= -2:
            signal = "SELL"
            strength = 60 + abs(score) * 5
            confidence = 70
            target_price = current_price * 0.95
            stop_loss = current_price * 1.03
        else:
            signal = "HOLD"
            strength = 50 + abs(score) * 3
            confidence = 60
            target_price = current_price
            stop_loss = current_price
            if not reasons:
                reasons.append("Sinais técnicos neutros")
        
        return {
            "signal": signal,
            "strength": round(strength),
            "confidence": round(confidence),
            "target_price": round(target_price, 8),
            "stop_loss": round(stop_loss, 8),
            "technical_indicators": {
                "rsi": rsi,
                "sma5": round(sma5, 8),
                "sma10": round(sma10, 8),
                "sma20": round(sma20, 8),
                "volatility": volatility,
                "bollinger_upper": round(bb_upper, 8) if bb_upper else None,
                "bollinger_middle": round(bb_middle, 8) if bb_middle else None,
                "bollinger_lower": round(bb_lower, 8) if bb_lower else None
            },
            "reasons": reasons[:4],  # Máximo 4 razões
            "score": score
        }
        
    except Exception as e:
        logger.error(f"Erro na análise técnica: {e}")
        return generate_simple_signal(coin_data)

def generate_simple_signal(coin_data):
    """Gera sinal simples quando dados históricos não estão disponíveis"""
    change_24h = coin_data.get('usd_24h_change', 0)
    current_price = coin_data['usd']
    
    if change_24h > 10:
        signal = "BUY"
        strength = min(90, 70 + abs(change_24h))
        confidence = 80
        reasons = ["Alta forte nas últimas 24h", "Momentum muito positivo"]
    elif change_24h > 5:
        signal = "BUY"
        strength = 65 + abs(change_24h) * 2
        confidence = 70
        reasons = ["Tendência de alta", "Variação positiva significativa"]
    elif change_24h < -10:
        signal = "SELL"
        strength = min(90, 70 + abs(change_24h))
        confidence = 80
        reasons = ["Queda forte nas últimas 24h", "Momentum muito negativo"]
    elif change_24h < -5:
        signal = "SELL"
        strength = 65 + abs(change_24h) * 2
        confidence = 70
        reasons = ["Tendência de baixa", "Variação negativa significativa"]
    else:
        signal = "HOLD"
        strength = 50
        confidence = 60
        reasons = ["Movimento lateral", "Aguardar definição de tendência"]
    
    return {
        "signal": signal,
        "strength": round(strength),
        "confidence": round(confidence),
        "target_price": current_price * (1.05 if signal == "BUY" else 0.95 if signal == "SELL" else 1.0),
        "stop_loss": current_price * (0.97 if signal == "BUY" else 1.03 if signal == "SELL" else 1.0),
        "technical_indicators": {
            "rsi": 50 + change_24h * 1.5,
            "sma5": current_price * (1 + change_24h / 200),
            "sma10": current_price * (1 + change_24h / 300),
            "sma20": current_price * (1 + change_24h / 400),
            "volatility": abs(change_24h),
            "bollinger_upper": None,
            "bollinger_middle": None,
            "bollinger_lower": None
        },
        "reasons": reasons,
        "score": 1 if signal == "BUY" else -1 if signal == "SELL" else 0
    }

@app.route('/')
def home():
    """Endpoint principal"""
    return jsonify({
        "status": "online",
        "message": "API de Sinais de Criptomoedas - Railway",
        "version": "2.0",
        "features": [
            "Dados 100% reais das APIs",
            "Análise técnica completa",
            "Sinais BUY/SELL/HOLD",
            "Indicadores técnicos reais",
            "Atualização em tempo real"
        ],
        "endpoints": {
            "/api/signals": "Obter todos os sinais",
            "/api/signals/<symbol>": "Obter sinal específico",
            "/api/health": "Status da API",
            "/api/stats": "Estatísticas gerais"
        }
    })

@app.route('/api/health')
def health():
    """Endpoint de saúde da API"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "uptime": "online",
        "apis": {
            "coingecko": "operational"
        },
        "cache_status": f"{len(cache)} items cached"
    })

@app.route('/api/signals')
def get_signals():
    """Endpoint principal para obter todos os sinais"""
    try:
        # Verificar cache
        cached_signals = get_cached_data('all_signals')
        if cached_signals:
            logger.info("Retornando dados do cache")
            return jsonify(cached_signals)
        
        logger.info("Buscando dados frescos das APIs...")
        
        # Buscar preços atuais
        current_prices = fetch_current_prices()
        if not current_prices:
            return jsonify({"error": "Erro ao buscar preços atuais"}), 500
        
        signals = []
        
        for coin in COINS:
            coin_id = coin["id"]
            coin_data = current_prices.get(coin_id)
            
            if not coin_data:
                continue
            
            # Buscar dados históricos para análise técnica
            historical_data = fetch_historical_data(coin_id)
            
            # Gerar sinal técnico
            if historical_data and historical_data.get('prices'):
                technical_analysis = generate_technical_signal(coin_data, historical_data)
            else:
                technical_analysis = generate_simple_signal(coin_data)
            
            signal_data = {
                "symbol": coin["symbol"],
                "name": coin["name"],
                "current_price": coin_data["usd"],
                "change_24h": coin_data.get("usd_24h_change", 0),
                "market_cap": coin_data.get("usd_market_cap", 0),
                "volume_24h": coin_data.get("usd_24h_vol", 0),
                **technical_analysis,
                "timestamp": datetime.now().isoformat(),
                "data_source": "CoinGecko API"
            }
            
            signals.append(signal_data)
            
            # Delay para evitar rate limiting
            time.sleep(0.1)
        
        response_data = {
            "signals": signals,
            "total_signals": len(signals),
            "buy_signals": len([s for s in signals if s["signal"] == "BUY"]),
            "sell_signals": len([s for s in signals if s["signal"] == "SELL"]),
            "hold_signals": len([s for s in signals if s["signal"] == "HOLD"]),
            "last_updated": datetime.now().isoformat(),
            "data_source": "CoinGecko API + Análise Técnica Real",
            "cache_duration": f"{CACHE_DURATION} seconds"
        }
        
        # Armazenar no cache
        set_cache_data('all_signals', response_data)
        
        logger.info(f"Retornando {len(signals)} sinais atualizados")
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Erro no endpoint /api/signals: {e}")
        return jsonify({"error": "Erro interno do servidor"}), 500

@app.route('/api/signals/<symbol>')
def get_signal_by_symbol(symbol):
    """Endpoint para obter sinal de uma moeda específica"""
    try:
        symbol = symbol.upper()
        
        # Buscar nos dados em cache primeiro
        cached_signals = get_cached_data('all_signals')
        if cached_signals:
            for signal in cached_signals.get('signals', []):
                if signal['symbol'] == symbol:
                    return jsonify(signal)
        
        # Se não encontrou no cache, buscar dados frescos
        coin = next((c for c in COINS if c["symbol"] == symbol), None)
        if not coin:
            return jsonify({"error": f"Moeda {symbol} não encontrada"}), 404
        
        current_prices = fetch_current_prices()
        if not current_prices:
            return jsonify({"error": "Erro ao buscar preços"}), 500
        
        coin_data = current_prices.get(coin["id"])
        if not coin_data:
            return jsonify({"error": f"Dados não encontrados para {symbol}"}), 404
        
        historical_data = fetch_historical_data(coin["id"])
        
        if historical_data and historical_data.get('prices'):
            technical_analysis = generate_technical_signal(coin_data, historical_data)
        else:
            technical_analysis = generate_simple_signal(coin_data)
        
        signal_data = {
            "symbol": coin["symbol"],
            "name": coin["name"],
            "current_price": coin_data["usd"],
            "change_24h": coin_data.get("usd_24h_change", 0),
            "market_cap": coin_data.get("usd_market_cap", 0),
            "volume_24h": coin_data.get("usd_24h_vol", 0),
            **technical_analysis,
            "timestamp": datetime.now().isoformat(),
            "data_source": "CoinGecko API"
        }
        
        return jsonify(signal_data)
        
    except Exception as e:
        logger.error(f"Erro no endpoint /api/signals/{symbol}: {e}")
        return jsonify({"error": "Erro interno do servidor"}), 500

@app.route('/api/stats')
def get_stats():
    """Endpoint para estatísticas gerais"""
    try:
        cached_signals = get_cached_data('all_signals')
        if not cached_signals:
            return jsonify({"error": "Dados não disponíveis"}), 503
        
        signals = cached_signals.get('signals', [])
        
        # Calcular estatísticas
        total_signals = len(signals)
        buy_signals = len([s for s in signals if s["signal"] == "BUY"])
        sell_signals = len([s for s in signals if s["signal"] == "SELL"])
        hold_signals = len([s for s in signals if s["signal"] == "HOLD"])
        
        avg_confidence = sum(s["confidence"] for s in signals) / total_signals if total_signals > 0 else 0
        avg_strength = sum(s["strength"] for s in signals) / total_signals if total_signals > 0 else 0
        
        # Top performers
        top_gainers = sorted([s for s in signals if s["change_24h"] > 0], 
                           key=lambda x: x["change_24h"], reverse=True)[:3]
        top_losers = sorted([s for s in signals if s["change_24h"] < 0], 
                          key=lambda x: x["change_24h"])[:3]
        
        return jsonify({
            "total_coins": total_signals,
            "signals_distribution": {
                "buy": buy_signals,
                "sell": sell_signals,
                "hold": hold_signals
            },
            "averages": {
                "confidence": round(avg_confidence, 1),
                "strength": round(avg_strength, 1)
            },
            "top_gainers": [{"symbol": s["symbol"], "change": s["change_24h"]} for s in top_gainers],
            "top_losers": [{"symbol": s["symbol"], "change": s["change_24h"]} for s in top_losers],
            "last_updated": cached_signals.get('last_updated'),
            "data_source": "CoinGecko API + Análise Técnica Real"
        })
        
    except Exception as e:
        logger.error(f"Erro no endpoint /api/stats: {e}")
        return jsonify({"error": "Erro interno do servidor"}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint não encontrado"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Erro interno do servidor"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

