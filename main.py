"""
API Flask para Sinais de Criptomoedas com Análise Técnica Avançada
Versão 2.0 - Implementa indicadores técnicos e sinais inteligentes
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
from datetime import datetime
import logging
from technical_analysis import TechnicalAnalysis

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins="https://candid-sundae-291faa.netlify.app")

# Instanciar analisador técnico
ta = TechnicalAnalysis()

# Configurações
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# Lista de moedas para monitorar
COINS = [
    {'id': 'bitcoin', 'symbol': 'BTC', 'name': 'Bitcoin'},
    {'id': 'ethereum', 'symbol': 'ETH', 'name': 'Ethereum'},
    {'id': 'dogecoin', 'symbol': 'DOGE', 'name': 'Dogecoin'},
    {'id': 'floki', 'symbol': 'FLOKI', 'name': 'Floki'},
    {'id': 'pepe', 'symbol': 'PEPE', 'name': 'Pepe'},
    {'id': 'bonk', 'symbol': 'BONK', 'name': 'Bonk'},
    {'id': 'shiba-inu', 'symbol': 'SHIB', 'name': 'Shiba Inu'},
    {'id': 'binancecoin', 'symbol': 'BNB', 'name': 'BNB'}
]


last_fetched_data = None
last_fetched_time = 0

def get_current_prices():
    global last_fetched_data, last_fetched_time
    CACHE_DURATION = 300  # Cache por 300 segundos (5 minutos)

    if last_fetched_data and (time.time() - last_fetched_time) < CACHE_DURATION:
        logger.info("Servindo dados do cache.")
        return last_fetched_data

    logger.info("Buscando novos dados do CoinGecko...")
    try:
        coin_ids = ",".join([coin["id"] for coin in COINS])
        url = f"{COINGECKO_BASE_URL}/simple/price"
        params = {
            "ids": coin_ids,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_market_cap": "true",
            "include_24hr_vol": "true",
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        last_fetched_data = data
        last_fetched_time = time.time()
        return data
        
    except Exception as e:
        logger.error(f"Erro ao obter preços atuais: {e}")
        return None


@app.route('/')
def home():
    """
    Endpoint principal com informações da API
    """
    return jsonify({
        'name': 'Crypto Signals API v2.0',
        'description': 'API avançada para sinais de criptomoedas com análise técnica',
        'version': '2.0.0',
        'features': [
            'Análise técnica real com RSI, médias móveis e Bandas de Bollinger',
            'Sinais inteligentes BUY/SELL/HOLD',
            'Previsão de porcentagem e confiança',
            'Preço alvo e stop loss calculados',
            'Razões técnicas detalhadas'
        ],
        'endpoints': {
            '/': 'Informações da API',
            '/health': 'Status da API',
            '/signals': 'Sinais básicos (compatibilidade)',
            '/signals/advanced': 'Sinais com análise técnica completa',
            '/analysis/<symbol>': 'Análise detalhada de uma moeda específica'
        },
        'timestamp': datetime.now().isoformat()
    })


@app.route('/health')
def health():
    """
    Endpoint de saúde da API
    """
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'uptime': 'running'
    })


@app.route('/signals')
def get_basic_signals():
    """
    Endpoint de compatibilidade - retorna sinais básicos
    """
    try:
        current_prices = get_current_prices()
        
        if not current_prices:
            return jsonify({'error': 'Erro ao obter dados de preços'}), 500
        
        signals = []
        
        for coin in COINS:
            coin_id = coin['id']
            if coin_id in current_prices:
                price_data = current_prices[coin_id]
                
                # Análise básica baseada na variação de 24h
                change_24h = price_data.get('usd_24h_change', 0)
                
                if change_24h > 5:
                    signal = 'BUY'
                elif change_24h < -5:
                    signal = 'SELL'
                else:
                    signal = 'HOLD'
                
                signals.append({
                    'symbol': coin['symbol'],
                    'name': coin['name'],
                    'current_price': price_data['usd'],
                    'change_24h': change_24h,
                    'market_cap': price_data.get('usd_market_cap', 0),
                    'volume_24h': price_data.get('usd_24h_vol', 0),
                    'signal': signal,
                    'confidence': 70 + abs(change_24h) * 2  # Confiança básica
                })
        
        return jsonify({
            'signals': signals,
            'timestamp': datetime.now().isoformat(),
            'total_coins': len(signals),
            'api_version': 'basic'
        })
        
    except Exception as e:
        logger.error(f"Erro no endpoint de sinais básicos: {e}")
        return jsonify({'error': 'Erro interno do servidor'}), 500


@app.route('/signals/advanced')
def get_advanced_signals():
    """
    Endpoint principal - retorna sinais com análise técnica completa
    """
    try:
        current_prices = get_current_prices()
        
        if not current_prices:
            return jsonify({'error': 'Erro ao obter dados de preços'}), 500
        
        signals = []
        
        for coin in COINS:
            coin_id = coin['id']
            if coin_id in current_prices:
                price_data = current_prices[coin_id]
                current_price = price_data['usd']
                
                # Realizar análise técnica completa
                analysis = ta.analyze_coin(coin_id, coin["symbol"], current_price)
                time.sleep(1) # Adiciona um atraso de 1 segundo para evitar limite de taxa
                # Adicionar dados de mercado
                analysis['market_data'] = {
                    'change_24h': price_data.get('usd_24h_change', 0),
                    'market_cap': price_data.get('usd_market_cap', 0),
                    'volume_24h': price_data.get('usd_24h_vol', 0)
                }
                
                signals.append(analysis)
        
        return jsonify({
            'signals': signals,
            'timestamp': datetime.now().isoformat(),
            'total_coins': len(signals),
            'api_version': 'advanced',
            'analysis_features': [
                'RSI (Relative Strength Index)',
                'Médias Móveis (SMA5, SMA10, SMA20)',
                'Bandas de Bollinger',
                'Análise de Volatilidade',
                'Sistema de Score Combinado',
                'Previsão de Porcentagem',
                'Cálculo de Confiança',
                'Preço Alvo e Stop Loss'
            ]
        })
        
    except Exception as e:
        logger.error(f"Erro no endpoint de sinais avançados: {e}")
        return jsonify({'error': 'Erro interno do servidor'}), 500


@app.route('/analysis/<symbol>')
def get_coin_analysis(symbol):
    """
    Análise detalhada de uma moeda específica
    """
    try:
        symbol = symbol.upper()
        
        # Encontrar a moeda na lista
        coin = None
        for c in COINS:
            if c['symbol'] == symbol:
                coin = c
                break
        
        if not coin:
            return jsonify({'error': f'Moeda {symbol} não encontrada'}), 404
        
        # Obter preço atual
        current_prices = get_current_prices()
        if not current_prices or coin['id'] not in current_prices:
            return jsonify({'error': 'Erro ao obter dados de preços'}), 500
        
        price_data = current_prices[coin['id']]
        current_price = price_data['usd']
        
        # Realizar análise técnica completa
        analysis = ta.analyze_coin(coin['id'], coin['symbol'], current_price)
        
        # Adicionar dados de mercado detalhados
        analysis['market_data'] = {
            'change_24h': price_data.get('usd_24h_change', 0),
            'market_cap': price_data.get('usd_market_cap', 0),
            'volume_24h': price_data.get('usd_24h_vol', 0)
        }
        
        # Adicionar interpretação dos indicadores
        analysis['interpretation'] = {
            'rsi_interpretation': get_rsi_interpretation(analysis['indicators']['rsi']),
            'trend_analysis': get_trend_analysis(analysis['indicators']),
            'bollinger_position': get_bollinger_position(
                current_price,
                analysis['indicators']['bollinger_upper'],
                analysis['indicators']['bollinger_lower']
            ),
            'volatility_assessment': analysis['signal']['volatility_level']
        }
        
        return jsonify(analysis)
        
    except Exception as e:
        logger.error(f"Erro na análise de {symbol}: {e}")
        return jsonify({'error': 'Erro interno do servidor'}), 500


def get_rsi_interpretation(rsi):
    """
    Interpreta o valor do RSI
    """
    if rsi < 30:
        return "Sobrevendido - Possível oportunidade de compra"
    elif rsi > 70:
        return "Sobrecomprado - Possível oportunidade de venda"
    elif 40 <= rsi <= 60:
        return "Zona neutra - Sem pressão de compra ou venda"
    elif rsi < 40:
        return "Tendência de baixa - Cautela"
    else:
        return "Tendência de alta - Momentum positivo"


def get_trend_analysis(indicators):
    """
    Analisa a tendência baseada nas médias móveis
    """
    sma5 = indicators['sma5']
    sma10 = indicators['sma10']
    sma20 = indicators['sma20']
    
    if sma5 > sma10 > sma20:
        return "Tendência de alta forte - Todas as médias em ordem crescente"
    elif sma5 < sma10 < sma20:
        return "Tendência de baixa forte - Todas as médias em ordem decrescente"
    elif sma5 > sma10:
        return "Tendência de alta de curto prazo"
    elif sma5 < sma10:
        return "Tendência de baixa de curto prazo"
    else:
        return "Tendência lateral - Consolidação"


def get_bollinger_position(price, upper_band, lower_band):
    """
    Determina a posição do preço nas Bandas de Bollinger
    """
    middle_band = (upper_band + lower_band) / 2
    
    if price >= upper_band:
        return "Preço na banda superior - Possível sobrecompra"
    elif price <= lower_band:
        return "Preço na banda inferior - Possível sobrevenda"
    elif price > middle_band:
        return "Preço acima da média - Momentum positivo"
    else:
        return "Preço abaixo da média - Momentum negativo"


@app.route('/test')
def test_endpoint():
    """
    Endpoint de teste para verificar se a API está funcionando
    """
    return jsonify({
        'message': 'API funcionando corretamente!',
        'timestamp': datetime.now().isoformat(),
        'test_data': {
            'coins_monitored': len(COINS),
            'features': [
                'Análise técnica avançada',
                'Sinais inteligentes',
                'Previsão de porcentagem',
                'Cálculo de confiança'
            ]
        }
    })


if __name__ == '__main__':
    logger.info("Iniciando Crypto Signals API v2.0...")
    logger.info(f"Monitorando {len(COINS)} criptomoedas")
    logger.info("Recursos: Análise técnica, RSI, Médias móveis, Bandas de Bollinger")
    
    # Executar em modo de desenvolvimento
    app.run(host='0.0.0.0', port=5000, debug=True)

