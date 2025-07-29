import os
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime

# Inicializa a aplicação Flask
app = Flask(__name__)
CORS(app)

def get_technical_signal(symbol):
    """
    Busca dados históricos, calcula médias móveis e RSI, 
    e gera um sinal de COMPRA ou VENDA com base em ambos os indicadores.
    """
    try:
        # 1. Buscar dados históricos (velas diárias), agora pegando 50 dias para dar mais dados ao RSI
        # --- CORREÇÃO APLICADA AQUI ---
        # Trocado 'api.binance.com' por 'api.binance.me' para evitar bloqueio geográfico (erro 451)
        url = f'https://api.binance.me/api/v3/klines?symbol={symbol}&interval=1d&limit=50'
        response = requests.get(url, timeout=10 )
        response.raise_for_status()
        data = response.json()

        # 2. Processar os dados com o Pandas
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'quote_asset_volume', 'number_of_trades', 
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])

        # 3. Calcular os Indicadores Técnicos
        # Adicionamos o cálculo do RSI (Índice de Força Relativa)
        df.ta.rsi(length=14, append=True)  # Calcula o RSI de 14 dias e adiciona ao DataFrame
        df.ta.sma(length=10, append=True)  # Média Móvel Curta (SMA_10)
        df.ta.sma(length=30, append=True)  # Média Móvel Longa (SMA_30)

        # Remove linhas que não têm dados suficientes para os cálculos
        df.dropna(inplace=True)
        if df.empty:
            raise Exception("Não há dados suficientes para a análise após o cálculo dos indicadores.")

        # 4. Gerar o Sinal com a Lógica Combinada
        # Pegamos os valores mais recentes dos indicadores
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else last_row

        signal_type = "HOLD"  # Sinal padrão
        rsi_value = last_row.get('RSI_14', 50)

        # CONDIÇÃO DE COMPRA (MAIS RIGOROSA)
        # A média curta cruzou para CIMA da longa E o RSI está abaixo de 70 (não sobrecomprado)
        if last_row['SMA_10'] > last_row['SMA_30'] and prev_row['SMA_10'] <= prev_row['SMA_30']:
            if rsi_value < 70:
                signal_type = "BUY"
            else:
                signal_type = "HOLD"

        # CONDIÇÃO DE VENDA (MAIS RIGOROSA)
        # A média curta cruzou para BAIXO da longa E o RSI está acima de 30 (não sobrevendido)
        elif last_row['SMA_10'] < last_row['SMA_30'] and prev_row['SMA_10'] >= prev_row['SMA_30']:
            if rsi_value > 30:
                signal_type = "SELL"
            else:
                signal_type = "HOLD"
        
        # Se não houve cruzamento, definimos o estado de HOLD
        elif last_row['SMA_10'] > last_row['SMA_30']:
            signal_type = "HOLD"
        else:
            signal_type = "HOLD"

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
            "entry": 0, 
            "stop": 0,
            "target": 0,
            "rsi": 0,
            "error_message": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.route("/")
def home():
    return jsonify({
        "message": "Crypton Signals API",
        "status": "online",
        "endpoints": ["/signals"],
        "timestamp": datetime.now().isoformat()
    })

@app.route("/signals")
def get_signals():
    try:
        # Lista de símbolos para processar
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
