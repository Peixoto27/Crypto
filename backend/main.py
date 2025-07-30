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

# --- INICIALIZAÇÃO DAS EXTENSÕES ---
db = SQLAlchemy()
cache = Cache()
cors = CORS()

# --- MODELO DA BASE DE DADOS ---
class SinalHistorico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pair = db.Column(db.String(20), nullable=False)
    entry = db.Column(db.Float, nullable=False)
    signal = db.Column(db.String(50), nullable=False)
    stop = db.Column(db.Float, nullable=False)
    target = db.Column(db.Float, nullable=False)
    rsi = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    def to_dict(self):
        data = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        if 'timestamp' in data and isinstance(data['timestamp'], datetime):
            data['timestamp'] = data['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
        return data

# --- FUNÇÃO DE CRIAÇÃO DA APLICAÇÃO (APPLICATION FACTORY) ---
def create_app():
    app = Flask(__name__)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # --- CONFIGURAÇÃO DA APP ---
    cache_config = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 21600}
    app.config.from_mapping(cache_config)

    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError("ERRO CRÍTICO: A variável de ambiente DATABASE_URL não foi encontrada.")
    
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # --- LIGA AS EXTENSÕES À APLICAÇÃO ---
    db.init_app(app)
    cache.init_app(app)
    cors.init_app(app)

    # --- REGISTO DAS ROTAS (BLUEPRINTS) ---
    with app.app_context():
        COINGECKO_MAP = {
            "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "XRPUSDT": "ripple",
            "SOLUSDT": "solana", "ADAUSDT": "cardano"
        }

        @app.route("/")
        def home():
            return jsonify({"message": "Crypton Signals API v3.4 (Final)", "status": "online"})

        @app.route("/signals")
        @cache.cached()
        def get_signals():
            logging.info("CACHE MISS: Gerando novos sinais e salvando no histórico.")
            signals = []
            for symbol in COINGECKO_MAP.keys():
                signal = get_technical_signal(symbol)
                signals.append(signal)
                if signal.get("signal") != "ERROR":
                    salvar_sinal_no_historico(signal)
                time.sleep(1.2)
            return jsonify({"signals": signals, "count": len(signals), "timestamp": datetime.now().isoformat()})

        @app.route("/signals/history")
        def get_history():
            try:
                sinais = SinalHistorico.query.order_by(SinalHistorico.timestamp.desc()).all()
                history_by_pair = {}
                for sinal in sinais:
                    if sinal.pair not in history_by_pair:
                        history_by_pair[sinal.pair] = []
                    history_by_pair[sinal.pair].append(sinal.to_dict())
                return jsonify(history_by_pair)
            except Exception as e:
                logging.error(f"Erro ao buscar histórico da base de dados: {e}")
                return jsonify({"error": "Não foi possível buscar o histórico."}), 500

        @app.route("/setup/database/create-tables-secret-path")
        def setup_database():
            try:
                db.create_all()
                return jsonify({"message": "SUCESSO: As tabelas da base de dados foram criadas (ou já existiam)."}), 200
            except Exception as e:
                logging.error(f"ERRO AO CRIAR TABELAS: {e}")
                return jsonify({"error": str(e)}), 500
        
        # --- ✅ ROTA SECRETA PARA LIMPAR A CACHE ---
        @app.route("/admin/cache/clear-secret-path")
        def clear_cache():
            try:
                cache.clear()
                return jsonify({"message": "SUCESSO: A cache foi limpa."}), 200
            except Exception as e:
                logging.error(f"ERRO AO LIMPAR A CACHE: {e}")
                return jsonify({"error": str(e)}), 500

        def salvar_sinal_no_historico(sinal_data):
            try:
                pair_name = sinal_data.get("pair")
                if not pair_name or sinal_data.get("signal") == "ERROR": return
                sinais_existentes = SinalHistorico.query.filter_by(pair=pair_name).order_by(SinalHistorico.timestamp.asc()).all()
                if len(sinais_existentes) >= 10: db.session.delete(sinais_existentes[0])
                novo_sinal = SinalHistorico(
                    pair=sinal_data['pair'], entry=sinal_data['entry'], signal=sinal_data['signal'],
                    stop=sinal_data['stop'], target=sinal_data['target'], rsi=sinal_data['rsi']
                )
                db.session.add(novo_sinal)
                db.session.commit()
                logging.info(f"Novo sinal para {pair_name} salvo no histórico.")
            except Exception as e:
                logging.error(f"Falha ao salvar sinal no histórico para {pair_name}: {e}")
                db.session.rollback()

        def get_technical_signal(symbol):
            try:
                coingecko_id = COINGECKO_MAP.get(symbol)
                url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart?vs_currency=usd&days=90&interval=daily'
                response = requests.get(url, timeout=15 )
                response.raise_for_status()
                market_data = response.json()
                df_prices = pd.DataFrame(market_data['prices'], columns=['timestamp', 'close'])
                df_volumes = pd.DataFrame(market_data['total_volumes'], columns=['timestamp', 'volume'])
                df_prices.set_index('timestamp', inplace=True)
                df_volumes.set_index('timestamp', inplace=True)
                df = df_prices.join(df_volumes, how='inner').reset_index()
                if df.empty: raise Exception("DataFrame vazio.")
                df.ta.rsi(length=14, append=True)
                df.ta.sma(close='close', length=10, append=True)
                df.ta.sma(close='close', length=30, append=True)
                df['volume_sma_20'] = df['volume'].rolling(window=20).mean()
                df.dropna(inplace=True)
                if df.empty: raise Exception("Dados insuficientes para análise.")
                last_row, prev_row = df.iloc[-1], df.iloc[-2]
                signal_type, confidence = "HOLD", ""
                volume_check = last_row['volume'] > (last_row['volume_sma_20'] * 1.20)
                if volume_check: confidence = " (Volume Forte)"
                rsi, sma_short, sma_long = last_row.get('RSI_14', 50), last_row['SMA_10'], last_row['SMA_30']
                prev_sma_short, prev_sma_long = prev_row['SMA_10'], prev_row['SMA_30']
                if sma_short > sma_long and prev_sma_short <= prev_sma_long and rsi < 70 and volume_check: signal_type = "BUY"
                elif sma_short < sma_long and prev_sma_short >= prev_sma_long and rsi > 30 and volume_check: signal_type = "SELL"
                entry_price = float(last_row['close'])
                return {
                    "pair": symbol.replace("USDT", "/USDT"), "entry": round(entry_price, 4),
                    "signal": f"{signal_type}{confidence}", "stop": round(entry_price * 0.98, 4),
                    "target": round(entry_price * 1.03, 4), "rsi": round(rsi, 2),
                    "timestamp": datetime.now().isoformat()
                }
            except Exception as e:
                logging.error(f"Erro ao gerar sinal para {symbol}: {e}")
                return {"pair": symbol.replace("USDT", "/USDT"), "signal": "ERROR", "error_message": str(e)}

    return app

# --- PONTO DE ENTRADA DA APLICAÇÃO ---
app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Iniciando servidor na porta {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
