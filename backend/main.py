import os
import requests
import pandas as pd
import pandas_ta as ta
from flask import Flask, jsonify
from flask_cors import CORS
import logging

app = Flask(__name__)
CORS(app)

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurações do Telegram
TELEGRAM_BOT_TOKEN = "7360602779:AAFIpncv7fkXaEX5PdWdEAUBb7NQ9SeA-F0"
TELEGRAM_CHAT_ID = "-1002196008777"

# Cache para evitar notificações duplicadas
last_notifications = {}

def send_telegram_notification(message):
    """Envia notificação para o canal do Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"Notificação enviada com sucesso: {message[:50]}...")
            return True
        else:
            logger.error(f"Erro ao enviar notificação: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Erro ao enviar notificação do Telegram: {e}")
        return False

def should_send_notification(pair, signal_text, confidence_score):
    """Verifica se deve enviar notificação (evita spam)"""
    # Só notifica para sinais de alta confiança (7+)
    if confidence_score < 7:
        return False
    
    # Só notifica para BUY e SELL, não para HOLD
    if not (signal_text.upper().includes("BUY") or signal_text.upper().includes("SELL")):
        return False
    
    # Evita notificações duplicadas (mesmo sinal em menos de 1 hora)
    current_time = pd.Timestamp.now()
    cache_key = f"{pair}_{signal_text}"
    
    if cache_key in last_notifications:
        time_diff = current_time - last_notifications[cache_key]
        if time_diff.total_seconds() < 3600:  # 1 hora
            return False
    
    # Atualiza o cache
    last_notifications[cache_key] = current_time
    return True

def get_crypto_data(symbol, timeframe='1d', limit=200):
    """Busca dados históricos da Binance.US"""
    try:
        url = f'https://api.binance.us/api/v3/klines'
        params = {
            'symbol': symbol,
            'interval': timeframe,
            'limit': limit
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # Converter para DataFrame
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        
        # Converter tipos de dados
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        return df
        
    except Exception as e:
        logger.error(f"Erro ao buscar dados para {symbol}: {e}")
        return None

def calculate_technical_indicators(df):
    """Calcula indicadores técnicos avançados"""
    try:
        # RSI
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        # Médias Móveis
        df['sma_10'] = ta.sma(df['close'], length=10)
        df['sma_30'] = ta.sma(df['close'], length=30)
        df['ema_12'] = ta.ema(df['close'], length=12)
        df['ema_26'] = ta.ema(df['close'], length=26)
        
        # MACD
        macd_data = ta.macd(df['close'])
        df['macd'] = macd_data['MACD_12_26_9']
        df['macd_signal'] = macd_data['MACDs_12_26_9']
        df['macd_histogram'] = macd_data['MACDh_12_26_9']
        
        # Bollinger Bands
        bb_data = ta.bbands(df['close'], length=20)
        df['bb_upper'] = bb_data['BBU_20_2.0']
        df['bb_middle'] = bb_data['BBM_20_2.0']
        df['bb_lower'] = bb_data['BBL_20_2.0']
        
        return df
        
    except Exception as e:
        logger.error(f"Erro ao calcular indicadores técnicos: {e}")
        return df

def generate_advanced_signal(symbol, timeframe='1d'):
    """Gera sinal avançado com múltiplos indicadores"""
    try:
        # Buscar dados
        df = get_crypto_data(symbol, timeframe)
        if df is None or len(df) < 50:
            return {
                "pair": symbol.replace("USDT", "/USDT"),
                "price": 0,
                "signal": "ERROR",
                "confidence": "0/10",
                "indicators": {}
            }
        
        # Calcular indicadores
        df = calculate_technical_indicators(df)
        
        # Valores atuais
        current_price = float(df['close'].iloc[-1])
        current_rsi = float(df['rsi'].iloc[-1])
        current_macd = float(df['macd'].iloc[-1])
        current_macd_signal = float(df['macd_signal'].iloc[-1])
        current_bb_upper = float(df['bb_upper'].iloc[-1])
        current_bb_lower = float(df['bb_lower'].iloc[-1])
        
        # Médias móveis
        current_sma_10 = float(df['sma_10'].iloc[-1])
        current_sma_30 = float(df['sma_30'].iloc[-1])
        
        # Análise de sinais
        signals = []
        confidence_points = 0
        
        # 1. Análise RSI
        if current_rsi < 30:  # Oversold
            signals.append("RSI Oversold")
            confidence_points += 2
        elif current_rsi > 70:  # Overbought
            signals.append("RSI Overbought")
            confidence_points += 2
        
        # 2. Cruzamento de Médias Móveis
        if current_sma_10 > current_sma_30:
            prev_sma_10 = float(df['sma_10'].iloc[-2])
            prev_sma_30 = float(df['sma_30'].iloc[-2])
            if prev_sma_10 <= prev_sma_30:  # Golden Cross
                signals.append("Golden Cross")
                confidence_points += 3
        elif current_sma_10 < current_sma_30:
            prev_sma_10 = float(df['sma_10'].iloc[-2])
            prev_sma_30 = float(df['sma_30'].iloc[-2])
            if prev_sma_10 >= prev_sma_30:  # Death Cross
                signals.append("Death Cross")
                confidence_points += 3
        
        # 3. MACD
        if current_macd > current_macd_signal:
            prev_macd = float(df['macd'].iloc[-2])
            prev_macd_signal = float(df['macd_signal'].iloc[-2])
            if prev_macd <= prev_macd_signal:  # MACD Bullish Cross
                signals.append("MACD Bullish")
                confidence_points += 3
        elif current_macd < current_macd_signal:
            prev_macd = float(df['macd'].iloc[-2])
            prev_macd_signal = float(df['macd_signal'].iloc[-2])
            if prev_macd >= prev_macd_signal:  # MACD Bearish Cross
                signals.append("MACD Bearish")
                confidence_points += 3
        
        # 4. Bollinger Bands
        if current_price <= current_bb_lower:
            signals.append("BB Oversold")
            confidence_points += 2
        elif current_price >= current_bb_upper:
            signals.append("BB Overbought")
            confidence_points += 2
        
        # Determinar sinal final
        bullish_signals = ["RSI Oversold", "Golden Cross", "MACD Bullish", "BB Oversold"]
        bearish_signals = ["RSI Overbought", "Death Cross", "MACD Bearish", "BB Overbought"]
        
        bullish_count = sum(1 for s in signals if s in bullish_signals)
        bearish_count = sum(1 for s in signals if s in bearish_signals)
        
        # Lógica de decisão mais rigorosa
        if confidence_points >= 7:
            if bullish_count > bearish_count and bullish_count >= 2:
                final_signal = "BUY (Confirmado)"
            elif bearish_count > bullish_count and bearish_count >= 2:
                final_signal = "SELL (Confirmado)"
            else:
                final_signal = f"HOLD (Sinais Mistos)"
                confidence_points = min(confidence_points, 6)  # Reduz confiança para sinais mistos
        elif confidence_points >= 5:
            if bullish_count > bearish_count:
                final_signal = f"HOLD (Tendência de Alta)"
            elif bearish_count > bullish_count:
                final_signal = f"HOLD (Tendência de Baixa)"
            else:
                final_signal = f"HOLD (Neutro)"
        else:
            final_signal = f"HOLD (Aguardando Confirmação)"
        
        # Limitar confiança a 10
        confidence_points = min(confidence_points, 10)
        
        result = {
            "pair": symbol.replace("USDT", "/USDT"),
            "price": round(current_price, 6),
            "signal": final_signal,
            "confidence": f"{confidence_points}/10",
            "indicators": {
                "rsi": round(current_rsi, 2),
                "macd": round(current_macd, 4),
                "bollinger_upper": round(current_bb_upper, 6),
                "bollinger_lower": round(current_bb_lower, 6)
            }
        }
        
        # Enviar notificação se necessário
        if should_send_notification(result["pair"], final_signal, confidence_points):
            message = f"""
🚨 <b>SINAL DE ALTA CONFIANÇA</b> 🚨

💰 <b>{result["pair"]}</b>
📊 <b>Sinal:</b> {final_signal}
💵 <b>Preço:</b> ${result["price"]}
⭐ <b>Confiança:</b> {result["confidence"]}

📈 <b>Indicadores:</b>
• RSI: {result["indicators"]["rsi"]}
• MACD: {result["indicators"]["macd"]}
• Bollinger Superior: ${result["indicators"]["bollinger_upper"]}
• Bollinger Inferior: ${result["indicators"]["bollinger_lower"]}

🕐 <b>Timeframe:</b> {timeframe.upper()}
            """.strip()
            
            send_telegram_notification(message)
        
        return result
        
    except Exception as e:
        logger.error(f"Erro ao gerar sinal para {symbol}: {e}")
        return {
            "pair": symbol.replace("USDT", "/USDT"),
            "price": 0,
            "signal": "ERROR",
            "confidence": "0/10",
            "indicators": {}
        }

@app.route("/signals")
def get_signals():
    """Endpoint principal para obter sinais"""
    try:
        timeframe = request.args.get('timeframe', '1d')
        
        # Lista de moedas para análise
        symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT"]
        
        signals = []
        for symbol in symbols:
            signal = generate_advanced_signal(symbol, timeframe)
            signals.append(signal)
            
        logger.info(f"Sinais gerados com sucesso para timeframe {timeframe}")
        return jsonify(signals)
        
    except Exception as e:
        logger.error(f"Erro geral ao gerar sinais: {e}")
        return jsonify({"error": f"Falha ao gerar sinais: {str(e)}"}), 500

@app.route("/test-telegram")
def test_telegram():
    """Endpoint para testar notificações do Telegram"""
    try:
        test_message = """
🧪 <b>TESTE DE NOTIFICAÇÃO</b> 🧪

✅ O sistema de notificações está funcionando!
📱 Você receberá alertas quando houver sinais de alta confiança.

🔔 <b>Configuração:</b>
• Bot: Ativo
• Canal: Conectado
• Filtro: Confiança ≥ 7/10
        """.strip()
        
        success = send_telegram_notification(test_message)
        
        if success:
            return jsonify({"status": "success", "message": "Notificação de teste enviada com sucesso!"})
        else:
            return jsonify({"status": "error", "message": "Falha ao enviar notificação de teste"}), 500
            
    except Exception as e:
        logger.error(f"Erro no teste do Telegram: {e}")
        return jsonify({"status": "error", "message": f"Erro: {str(e)}"}), 500

@app.route("/")
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "online",
        "service": "Sinais Pro API",
        "telegram": "configured",
        "endpoints": ["/signals", "/test-telegram"]
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)

