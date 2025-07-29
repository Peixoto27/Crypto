import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime

# --- CONFIGURAÇÃO INICIAL ---

# Inicializa a aplicação Flask
app = Flask(__name__)
CORS(app)

# Configura o logging para fornecer informações mais detalhadas nos logs da Railway
# Isto ajuda a depurar problemas sem "crashar" a aplicação
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Mapeamento de símbolos da Binance para IDs da CoinGecko
# Este dicionário traduz os símbolos que o seu frontend usa para os que a API da CoinGecko espera
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
    Busca dados históricos da CoinGecko, calcula indicadores técnicos
    e gera um sinal de COMPRA, VENDA ou HOLD.
    """
    try:
        coingecko_id = COINGECKO_MAP.get(symbol)
        if not coingecko_id:
            raise Exception(f"Símbolo {symbol} não mapeado para a CoinGecko.")

        # 1. Buscar dados históricos da CoinGecko
        url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/ohlc?vs_currency=usd&days=90'
        logging.info(f"Buscando dados para {symbol} de {url}" )
        
        response = requests.get(url, timeout=15) # Aumentado o timeout para 15s por segurança
        response.raise_for_status()  # Lança um erro para respostas HTTP ruins (4xx ou 5xx)
        
        data = response.json()

        if not data:
            raise Exception("API da CoinGecko não retornou dados.")

        # 2. Processar os dados com o Pandas
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close'])
        
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

    except requests.exceptions.HTTPError as http_err:
        logging.error(f"Erro HTTP ao buscar dados para {symbol}: {http_err} - URL: {http_err.request.url} - Resposta: {http_err.response.text[:100]}" )
        return {
            "pair": symbol.replace("USDT", "/USDT"), 
            "signal": "ERROR", 
            "error_message": f"Falha na API ({http_err.response.status_code} )",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logging.error(f"Erro inesperado ao gerar sinal para {symbol}: {e}")
        return {
            "pair": symbol.replace("USDT", "/USDT"), 
            "signal": "ERROR", 
            "error_message": str(e),
            "timestamp": datetime.now().isoformat()
        }


# --- ENDPOINTS DA API (ROTAS) ---

@app.route("/")
def home():
    """Endpoint inicial que descreve a API."""
    return jsonify({
        "message": "Crypton Signals API",
        "status": "online",
        "data_source": "CoinGecko",
        "endpoints": ["/signals", "/health"],
        "timestamp": datetime.now().isoformat()
    })

@app.route("/signals")
def get_signals():
    """Endpoint principal que retorna os sinais técnicos para uma lista de moedas."""
    try:
        symbols_to_process = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT"]
        
        signals = []
        for symbol in symbols_to_process:
            signal = get_technical_signal(symbol)
            signals.append(signal)
        
        logging.info(f"Sinais técnicos gerados com sucesso: {len(signals)} sinais processados.")
        
        return jsonify({
            "signals": signals,
            "count": len(signals),
            "timestamp": datetime.now().isoformat(),
            "status": "success"
        })
        
    except Exception as e:
        logging.error(f"Erro GERAL e inesperado na rota /signals: {e}")
        return jsonify({
            "error": f"Falha crítica ao gerar sinais: {str(e)}",
            "timestamp": datetime.now().isoformat(),
            "status": "error"
        }), 500

@app.route("/health")
def health_check():
    """Endpoint de verificação de saúde, útil para serviços de monitoramento."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    })


# --- EXECUÇÃO DA APLICAÇÃO ---

if __name__ == "__main__":
    # Obtém a porta do ambiente (fornecida pela Railway) ou usa 5000 como padrão
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Iniciando servidor na porta {port}")
    # O debug=False é importante para produção
    app.run(debug=False, host='0.0.0.0', port=port)
