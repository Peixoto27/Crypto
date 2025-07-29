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

config = {
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 21600  # 6 horas
}
app.config.from_mapping(config)
cache = Cache(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

COINGECKO_MAP = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "XRPUSDT": "ripple",
    "SOLUSDT": "solana",
    "ADAUSDT": "cardano"
}

# --- LÓGICA PRINCIPAL ---

def get_technical_signal(symbol):
    """
    Busca dados de preço E VOLUME, calcula indicadores e gera um sinal robusto.
    """
    try:
        coingecko_id = COINGECKO_MAP.get(symbol)
        if not coingecko_id:
            raise Exception(f"Símbolo {symbol} não mapeado.")

        # --- 1. BUSCAR DADOS DE PREÇO E VOLUME (DE DOIS ENDPOINTS) ---
        days_to_fetch = 90
        logging.info(f"Buscando dados para {symbol} (ID: {coingecko_id})")

        # Endpoint de Preço (OHLC)
        price_url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/ohlc?vs_currency=usd&days={days_to_fetch}'
        price_data = requests.get(price_url, timeout=15 ).json()
        
        # Endpoint de Volume
        volume_url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart?vs_currency=usd&days={days_to_fetch}&interval=daily'
        market_data = requests.get(volume_url, timeout=15 ).json()

        if not price_data or 'total_volumes' not in market_data:
            raise Exception("Dados da API incompletos.")

        # --- 2. PROCESSAR E COMBINAR OS DADOS ---
        df_price = pd.DataFrame(price_data, columns=['timestamp', 'open', 'high', 'low', 'close'])
        df_price['date'] = pd.to_datetime(df_price['timestamp'], unit='ms').dt.date

        df_volume = pd.DataFrame(market_data['total_volumes'], columns=['timestamp', 'volume'])
        df_volume['date'] = pd.to_datetime(df_volume['timestamp'], unit='ms').dt.date
        
        # Combina os dois DataFrames com base na data
        df = pd.merge(df_price, df_volume[['date', 'volume']], on='date', how='inner')

        # --- 3. CALCULAR INDICADORES (INCLUINDO MÉDIA DE VOLUME) ---
        df.ta.rsi(length=14, append=True)
        df.ta.sma(length=10, append=True)
        df.ta.sma(length=30, append=True)
        # Calcula a média móvel do volume dos últimos 20 dias
        df['volume_sma_20'] = df['volume'].rolling(window=20).mean()

        df.dropna(inplace=True)
        if df.empty:
            raise Exception("Dados insuficientes para análise.")

        # --- 4. GERAR SINAL COM A NOVA LÓGICA DE VOLUME ---
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]

        signal_type = "HOLD"
        confidence = ""
        
        # Condição de Volume: Volume atual é pelo menos 20% maior que a média
        volume_check = last_row['volume'] > (last_row['volume_sma_20'] * 1.20)
        if volume_check:
            confidence = " (Volume Forte)"

        rsi_value = last_row.get('RSI_14', 50)
        sma_short = last_row['SMA_10']
        sma_long = last_row['SMA_30']
        prev_sma_short = prev_row['SMA_10']
        prev_sma_long = prev_row['SMA_30']

        # Lógica de Compra: Cruzamento de alta + RSI não sobrecomprado + Confirmação de Volume
        if sma_short > sma_long and prev_sma_short <= prev_sma_long and rsi_value < 70 and volume_check:
            signal_type = "BUY"
        # Lógica de Venda: Cruzamento de baixa + RSI não sobrevendido + Confirmação de Volume
        elif sma_short < sma_long and prev_sma_short >= prev_sma_long and rsi_value > 30 and volume_check:
            signal_type = "SELL"
        
        entry_price = float(last_row['close'])
        
        return {
            "pair": symbol.replace("USDT", "/USDT"),
            "entry": round(entry_price, 4),
            "signal": f"{signal_type}{confidence}",
            "stop": round(entry_price * 0.98, 4),
            "target": round(entry_price * 1.03, 4),
            "rsi": round(rsi_value, 2),
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logging.error(f"Erro ao gerar sinal para {symbol}: {e}")
        return {"pair": symbol.replace("USDT", "/USDT"), "signal": "ERROR", "error_message": str(e), "timestamp": datetime.now().isoformat()}

# --- ENDPOINTS DA API (ROTAS) ---

@app.route("/")
def home():
    return jsonify({"message": "Crypton Signals API v2 (with Volume Analysis)", "status": "online"})

@app.route("/signals")
@cache.cached()
def get_signals():
    logging.info("CACHE MISS: Gerando novos sinais (com análise de volume).")
    try:
        symbols_to_process = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT"]
        signals = []
        for symbol in symbols_to_process:
            # Agora cada símbolo faz 2 chamadas, então a pausa é ainda mais importante
            time.sleep(2) 
            signal = get_technical_signal(symbol)
            signals.append(signal)
        
        return jsonify({"signals": signals, "count": len(signals), "timestamp": datetime.now().isoformat(), "status": "success"})
        
    except Exception as e:
        logging.error(f"Erro GERAL na rota /signals: {e}")
        return jsonify({"error": f"Falha crítica ao gerar sinais: {str(e)}", "status": "error"}), 500

# O resto do código (health_check, __main__) permanece o mesmo...

@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Iniciando servidor na porta {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
