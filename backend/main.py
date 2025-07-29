import os
from flask import Flask, jsonify
from flask_cors import CORS
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta

# Inicializa a aplicação Flask
app = Flask(__name__)
CORS(app)

# Mapeamento de símbolos da Binance para IDs da CoinGecko
COINGECKO_MAP = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "XRPUSDT": "ripple",
    "SOLUSDT": "solana",
    "ADAUSDT": "cardano"
}

def get_technical_signal(symbol):
    """
    Busca dados históricos da CoinGecko, calcula indicadores técnicos
    e gera um sinal de COMPRA, VENDA ou HOLD.
    """
    try:
        coingecko_id = COINGECKO_MAP.get(symbol)
        if not coingecko_id:
            raise Exception(f"Símbolo {symbol} não mapeado para a CoinGecko.")

        # 1. Buscar dados históricos da CoinGecko (velas diárias para os últimos 90 dias)
        # A CoinGecko não tem 'limit', então pedimos um intervalo de datas.
        # Pedimos 90 dias para garantir dados suficientes para os cálculos.
        url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/ohlc?vs_currency=usd&days=90'
        response = requests.get(url, timeout=10 )
        response.raise_for_status()
        data = response.json()

        if not data:
            raise Exception("API da CoinGecko não retornou dados.")

        # 2. Processar os dados com o Pandas
        # O formato da CoinGecko é [timestamp, open, high, low, close]
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close'])
        
        # A CoinGecko já fornece os tipos numéricos corretos, então a conversão não é necessária.
        # df['close'] = pd.to_numeric(df['close']) ...

        # 3. Calcular os Indicadores Técnicos
        df.ta.rsi(length=14, append=True)
        df.ta.sma(length=10, append=True)
        df.ta.sma(length=30, append=True)

        df.dropna(inplace=True)
        if df.empty:
            raise Exception("Não há dados suficientes para a análise após o cálculo dos indicadores.")

        # 4. Gerar o Sinal com a Lógica Combinada
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else last_row

        signal_type = "HOLD"
        rsi_value = last_row.get('RSI_14', 50)

        # Lógica de cruzamento de médias
        sma_short = last_row['SMA_10']
        sma_long = last_row['SMA_30']
        prev_sma_short = prev_row['SMA_10']
        prev_sma_long = prev_row['SMA_30']

        # Cruzamento de alta (compra)
        if sma_short > sma_long and prev_sma_short <= prev_sma_long and rsi_value < 70:
            signal_type = "BUY"
        # Cruzamento de baixa (venda)
        elif sma_short < sma_long and prev_sma_short >= prev_sma_long and rsi_value > 30:
            signal_type = "SELL"
        
        entry_price = float(last_row['close'])
        
        return {
            "pair": symbol.replace("USDT", "/USDT"),
            "entry": round(entry_price, 4),
            "signal": f"{signal_type} (RSI: {rsi_value:.2f})",
            "stop": round(entry_price * 0.98, 4),
            "target": round(entry_price * 1.03, 4),
            "rsi": round(rsi_value, 2),
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        print(f"Erro ao gerar sinal técnico para {symbol}: {e}")
        return {
            "pair": symbol.replace("USDT", "/USDT"), 
            "signal": "ERROR", 
            "error_message": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.route("/")
def home():
    return jsonify({
        "message": "Crypton Signals API",
        "status": "online",
        "data_source": "CoinGecko",
        "endpoints": ["/signals"],
        "timestamp": datetime.now().isoformat()
    })

@app.route("/signals")
def get_signals():
    try:
        symbols_to_process = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT"]
        
        signals = []
        for symbol in symbols_to_process:
            signal = get_technical_signal(symbol)
            signals.append(signal)
        
        print(f"Sinais técnicos gerados com sucesso: {len(signals)} sinais")
        
        return jsonify({
            "signals": signals,
            "count": len(signals),
            "timestamp": datetime.now().isoformat(),
            "status": "success"
        })
        
    except Exception as e:
        print(f"Erro geral ao gerar os sinais: {e}")
        return jsonify({
            "error": f"Falha ao gerar sinais: {str(e)}",
            "timestamp": datetime.now().isoformat(),
            "status": "error"
        }), 500

@app.route("/health")
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Iniciando servidor na porta {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
