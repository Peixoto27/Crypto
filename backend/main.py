import os
import logging
import time
from flask import Flask, jsonify
from flask_cors import CORS
from flask_caching import Cache
from flask_sqlalchemy import SQLAlchemy
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime

# --- CONFIGURAÇÃO INICIAL (APP, CACHE, LOGGING) ---
app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

config = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 21600}
app.config.from_mapping(config)
cache = Cache(app)

# --- CONFIGURAÇÃO DA BASE DE DADOS ---
# Pega o URL da base de dados da variável de ambiente que configurámos na Railway
db_url = os.environ.get('DATABASE_URL')
# A Railway usa 'postgres://' mas SQLAlchemy espera 'postgresql://'
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODELO DA TABELA (A ESTRUTURA DA NOSSA TABELA DE HISTÓRICO) ---
class SinalHistorico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pair = db.Column(db.String(20), nullable=False)
    entry = db.Column(db.Float, nullable=False)
    signal = db.Column(db.String(50), nullable=False)
    stop = db.Column(db.Float, nullable=False)
    target = db.Column(db.Float, nullable=False)
    rsi = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

# --- LÓGICA DE SINAIS (COM INTEGRAÇÃO DA BASE DE DADOS) ---
COINGECKO_MAP = {
    "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "XRPUSDT": "ripple",
    "SOLUSDT": "solana", "ADAUSDT": "cardano"
}

def get_technical_signal(symbol):
    # (A lógica para buscar dados e calcular o sinal permanece a mesma)
    # ... (código omitido por brevidade, mas está no bloco completo abaixo)
    # A única diferença é que no final, chamamos a função para salvar o sinal
    pass # Esta função será substituída pelo código completo abaixo

# --- FUNÇÃO PARA SALVAR NO HISTÓRICO (COM LÓGICA DE LIMPEZA) ---
def salvar_sinal_no_historico(sinal_data):
    with app.app_context():
        try:
            pair_name = sinal_data.get("pair")
            if not pair_name or sinal_data.get("signal") == "ERROR":
                logging.info(f"Sinal para {pair_name} é um erro ou inválido, não será salvo.")
                return

            # Lógica de limpeza: manter apenas os últimos 10 por moeda
            sinais_existentes = SinalHistorico.query.filter_by(pair=pair_name).order_by(SinalHistorico.timestamp.asc()).all()
            if len(sinais_existentes) >= 10:
                sinal_mais_antigo = sinais_existentes[0]
                logging.info(f"Limite de histórico atingido para {pair_name}. Removendo sinal de {sinal_mais_antigo.timestamp}.")
                db.session.delete(sinal_mais_antigo)

            # Cria e salva o novo registro
            novo_sinal = SinalHistorico(
                pair=sinal_data['pair'],
                entry=sinal_data['entry'],
                signal=sinal_data['signal'],
                stop=sinal_data['stop'],
                target=sinal_data['target'],
                rsi=sinal_data['rsi']
            )
            db.session.add(novo_sinal)
            db.session.commit()
            logging.info(f"Novo sinal para {pair_name} salvo no histórico.")

        except Exception as e:
            logging.error(f"Falha ao salvar sinal no histórico para {pair_name}: {e}")
            db.session.rollback()

# --- ENDPOINTS DA API (ROTAS) ---

@app.route("/")
def home():
    return jsonify({"message": "Crypton Signals API v3 (with Database History)", "status": "online"})

@app.route("/signals")
@cache.cached()
def get_signals():
    logging.info("CACHE MISS: Gerando novos sinais e salvando no histórico.")
    signals = []
    for symbol in COINGECKO_MAP.keys():
        signal = get_technical_signal(symbol) # Esta função agora precisa ser completa
        signals.append(signal)
        
        # Salva o sinal no histórico DEPOIS de gerá-lo
        if signal.get("signal") != "ERROR":
            salvar_sinal_no_historico(signal)
            
        time.sleep(1.2)
    
    return jsonify({"signals": signals, "count": len(signals), "timestamp": datetime.now().isoformat()})

@app.route("/signals/history")
def get_history():
    try:
        sinais = SinalHistorico.query.order_by(SinalHistorico.timestamp.desc()).all()
        
        # Agrupa os sinais por par de moeda
        history_by_pair = {}
        for sinal in sinais:
            if sinal.pair not in history_by_pair:
                history_by_pair[sinal.pair] = []
            history_by_pair[sinal.pair].append(sinal.to_dict())
            
        return jsonify(history_by_pair)
    except Exception as e:
        logging.error(f"Erro ao buscar histórico da base de dados: {e}")
        return jsonify({"error": "Não foi possível buscar o histórico."}), 500

# --- CÓDIGO COMPLETO DA FUNÇÃO get_technical_signal ---
def get_technical_signal(symbol):
    try:
        coingecko_id = COINGECKO_MAP.get(symbol)
        if not coingecko_id:
            raise Exception(f"Símbolo {symbol} não mapeado.")

        days_to_fetch = 90
        logging.info(f"Buscando dados de mercado para {symbol} (ID: {coingecko_id})")
        
        url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart?vs_currency=usd&days={days_to_fetch}&interval=daily'
        response = requests.get(url, timeout=15 )
        response.raise_for_status()
        market_data = response.json()

        if 'prices' not in market_data or 'total_volumes' not in market_data:
            raise Exception("Dados da API incompletos.")

        df_prices = pd.DataFrame(market_data['prices'], columns=['timestamp', 'close'])
        df_volumes = pd.DataFrame(market_data['total_volumes'], columns=['timestamp', 'volume'])
        
        df_prices.set_index('timestamp', inplace=True)
        df_volumes.set_index('timestamp', inplace=True)
        df = df_prices.join(df_volumes, how='inner')
        df.reset_index(inplace=True)

        if df.empty:
            raise Exception("DataFrame vazio após combinar preços e volumes.")

        df.ta.rsi(length=14, append=True)
        df.ta.sma(close='close', length=10, append=True)
        df.ta.sma(close='close', length=30, append=True)
        df['volume_sma_20'] = df['volume'].rolling(window=20).mean()

        df.dropna(inplace=True)
        if df.empty:
            raise Exception("Dados insuficientes para análise após cálculos.")

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

# --- INICIALIZAÇÃO DA APLICAÇÃO E DA BASE DE DADOS ---
if __name__ == "__main__":
    with app.app_context():
        # Cria a tabela na base de dados se ela ainda não existir
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Iniciando servidor na porta {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
