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
ta = TechnicalAnalysis(coinranking_api_key=COINRANKING_API_KEY, coinranking_uuid_map=coinranking_uuid_map)

# Configurações
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
COINRANKING_BASE_URL = "https://api.coinranking.com/v2"
COINRANKING_API_KEY = "" # A Coinranking permite um número limitado de requisições sem autenticação

# Lista de moedas para monitorar
COINS = [
    {'id': 'bitcoin', 'symbol': 'BTC', 'name': 'Bitcoin'},
    {'id': 'ethereum', 'symbol': 'ETH', 'name': 'Ethereum'}
]

coinranking_uuid_map = {
    "bitcoin": "Qwsogvtv82FCd", # Bitcoin UUID na Coinranking
    "ethereum": "razxDUgYGNAdQ" # Ethereum UUID na Coinranking
}


last_fetched_data = None
last_fetched_time = 0

def get_current_prices():
    global last_fetched_data, last_fetched_time
    CACHE_DURATION = 600  # Cache por 600 segundos (10 minutos)

    if last_fetched_data and (time.time() - last_fetched_time) < CACHE_DURATION:
        logger.info("Servindo dados do cache.")
        return last_fetched_data

    logger.info("Buscando novos dados de preços...")
    
    # Tentar Coinranking primeiro para preços atuais
    try:
        coin_ids_coinranking = ",".join([coinranking_uuid_map[coin["id"]] for coin in COINS])
        url_coinranking = f"{COINRANKING_BASE_URL}/coins"
        params_coinranking = {
            "uuids": coin_ids_coinranking,
            "timePeriod": "24h" # Para obter a variação de 24h
        }
        headers_coinranking = {
            "x-access-token": COINRANKING_API_KEY
        }

        response_coinranking = requests.get(url_coinranking, params=params_coinranking, headers=headers_coinranking, timeout=10)
        response_coinranking.raise_for_status()

        data_coinranking = response_coinranking.json()
        if data_coinranking["status"] == "success" and data_coinranking["data"]["coins"]:
            processed_data = {}
            for coin_data in data_coinranking["data"]["coins"]:
                original_coin_id = next(k for k, v in coinranking_uuid_map.items() if v == coin_data["uuid"])
                processed_data[original_coin_id] = {
                    "usd": float(coin_data["price"]),
                    "usd_24h_change": float(coin_data["change"]),
                    "usd_market_cap": float(coin_data["marketCap"]),
                    "usd_24h_vol": float(coin_data["24hVolume"])
                }
            logger.info("Dados de preços atuais obtidos da Coinranking.")
            last_fetched_data = processed_data
            last_fetched_time = time.time()
            return processed_data
        else:
            logger.warning("Coinranking não retornou dados válidos. Tentando CoinGecko...")

    except Exception as e:
        logger.error(f"Erro ao obter preços atuais da Coinranking: {e}. Tentando CoinGecko...")

    # Fallback para CoinGecko
    try:
        coin_ids_coingecko = ",".join([coin["id"] for coin in COINS])
        url_coingecko = f"{COINGECKO_BASE_URL}/simple/price"
        params_coingecko = {
            "ids": coin_ids_coingecko,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_market_cap": "true",
            "include_24hr_vol": "true",
        }

        response_coingecko = requests.get(url_coingecko, params=params_coingecko, timeout=10)
        response_coingecko.raise_for_status()

        data_coingecko = response_coingecko.json()
        logger.info("Dados de preços atuais obtidos da CoinGecko.")
        last_fetched_data = data_coingecko
        last_fetched_time = time.time()
        return data_coingecko
        
    except Exception as e:
        logger.error(f"Erro ao obter preços atuais do CoinGecko: {e}")
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
                
                try:
                    # Realizar análise técnica completa

                    analysis = ta.analyze_coin(coin_id, coin["symbol"], current_price)
                except Exception as e:
                    logger.error(f"Erro ao realizar análise técnica para {coin_id}: {e}. Retornando análise básica.")
                    analysis = ta._basic_analysis(coin["symbol"], current_price)

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

