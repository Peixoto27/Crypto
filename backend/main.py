import os
import logging
import time
from flask import Flask, jsonify
from flask_cors import CORS
from flask_caching import Cache
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime

# --- CONFIGURAÇÃO INICIAL ---

app = Flask(__name__)
CORS(app)

config = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 21600}
app.config.from_mapping(config)
cache = Cache(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

COINGECKO_MAP = {
    "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "XRPUSDT": "ripple",
    "SOLUSDT": "solana", "ADAUSDT": "cardano"
}

# --- LÓGICA PRINCIPAL ---

def get_technical_signal(symbol):
    """
    Busca dados de preço e volume de um único endpoint, calcula indicadores e gera um sinal.
    """
    try:
        coingecko_id = COINGECKO_MAP.get(symbol)
        if not coingecko_id:
            raise Exception(f"Símbolo {symbol} não mapeado.")

        # --- 1. BUSCAR DADOS DE UM ÚNICO ENDPOINT MAIS ROBUSTO ---
        days_to_fetch = 90
        logging.info(f"Buscando dados de mercado para {symbol} (ID: {coingecko_id})")
        
        url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart?vs_currency=usd&days={days_to_fetch}&interval=daily'
        response = requests.get(url, timeout=15 )
        response.raise_for_status()
        market_data = response.json()

        if 'prices' not in market_data or 'total_volumes' not in market_data:
            raise Exception("Dados da API incompletos.")

        # --- 2. PROCESSAR DADOS DIRETAMENTE (SEM MERGE) ---
        df_prices = pd.DataFrame(market_data['prices'], columns=['timestamp', 'close'])
        df_volumes = pd.DataFrame(market_data['total_volumes'], columns=['timestamp', 'volume'])
        
        # Usa o timestamp como índice para combinar os dados de forma segura
        df_prices.set_index('timestamp', inplace=True)
        df_volumes.set_index('timestamp', inplace=True)
        df = df_prices.join(df_volumes, how='inner')
        df.reset_index(inplace=True)

        if df.empty:
            raise Exception("DataFrame vazio após combinar preços e volumes.")

        # --- 3. CALCULAR INDICADORES ---
        df.ta.rsi(length=14, append=True)
        df.ta.sma(close='close', length=10, append=True)
        df.ta.sma(close='close', length=30, append=True)
        df['volume_sma_20'] = df['volume'].rolling(window=20).mean()

        df.dropna(inplace=True)
        if df.empty:
            raise Exception("Dados insuficientes para análise após cálculos.")

        # --- 4. GERAR SINAL COM LÓGICA DE VOLUME ---
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]

        signal_type = "HOLD"
        confidence = ""
        
        volume_check = last_row['volume'] > (last_row['volume_sma_20'] * 1.20)
        if volume_check:
            confidence = " (Volume Forte)"

        rsi_value = last_row.get('RSI_14', 50)
        sma_short = last_row['SMA_10']
        sma_long = last_row['SMA_30']
        prev_sma_short = prev_row['SMA_10']
        prev_sma_long = prev_row['SMA_30']

        if sma_short > sma_long and prev_sma_short <= prev_sma_long and rsi_value < 70 and volume_check:
            signal_type = "BUY"
        elif sma_short < sma_long and prev_sma_short >= prev_sma_long and rsi_value > 30 and volume_check:
            signal_type = "SELL"
        
        entry_price = float(last_row['close'])
        
        return {
            "pair": symbol.replace("USDT", "/USDT"), "entry": round(entry_price, 4),
            "signal": f"{signal_type}{confidence}", "stop": round(entry_price * 0.98, 4),
            "target": round(entry_price * 1.03, 4), "rsi": round(rsi_value, 2),
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logging.error(f"Erro ao gerar sinal para {symbol}: {e}")
        return {"pair": symbol.replace("USDT", "/USDT"), "signal": "ERROR", "error_message": str(e), "timestamp": datetime.now().isoformat()}

# --- ENDPOINTS DA API (ROTAS) ---

@app.route("/")
def home():
    return jsonify({"message": "Crypton Signals API v2.1 (Robust Data Fetching)", "status": "online"})

@app.route("/signals")
@cache.cached()
def get_signals():
    logging.info("CACHE MISS: Gerando novos sinais (com análise de volume).")
    try:
        symbols_to_process = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT"]
        signals = []
        for symbol in symbols_to_process:
            signal = get_technical_signal(symbol)
            signals.append(signal)
            time.sleep(1.2) # Pausa reduzida, pois só fazemos 1 chamada por moeda
        
        return jsonify({"signals": signals, "count": len(signals), "timestamp": datetime.now().isoformat(), "status": "success"})
        
    except Exception as e:
        logging.error(f"Erro GERAL na rota /signals: {e}")
        return jsonify({"error": f"Falha crítica ao gerar sinais: {str(e)}", "status": "error"}), 500

@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Iniciando servidor na porta {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
