import os
import logging
import time
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_caching import Cache
from flask_sqlalchemy import SQLAlchemy
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime

# --- INICIALIZAÇÃO --- 
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

    # Configurações
    cache_config = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 21600}
    app.config.from_mapping(cache_config)
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError("ERRO CRÍTICO: A variável de ambiente DATABASE_URL não foi encontrada.")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['COINGECKO_API_KEY'] = os.environ.get('COINGECKO_API_KEY')

    # Inicialização das extensões
    db.init_app(app)
    cache.init_app(app)
    cors.init_app(app)

    # --- CONTEXTO DA APLICAÇÃO E ROTAS --- 
    with app.app_context():
        COINGECKO_MAP = {
            "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "XRPUSDT": "ripple",
            "SOLUSDT": "solana", "ADAUSDT": "cardano"
        }

        def get_coingecko_data(url):
            headers = {}
            api_key = app.config.get('COINGECKO_API_KEY')
            if api_key:
                headers['x-cg-demo-api-key'] = api_key
            
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            return response.json()

        def get_technical_signal(symbol):
            try:
                coingecko_id = COINGECKO_MAP.get(symbol)
                logging.info(f"Buscando dados de 365 dias para {symbol} (com chave de API)")
                url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart?vs_currency=usd&days=365&interval=daily'
                all_data = get_coingecko_data(url )

                df_full = pd.DataFrame(all_data['prices'], columns=['timestamp', 'close'])
                df_full_volumes = pd.DataFrame(all_data['total_volumes'], columns=['timestamp', 'volume'])
                df_full.set_index('timestamp', inplace=True)
                df_full_volumes.set_index('timestamp', inplace=True)
                df_full = df_full.join(df_full_volumes, how='inner').reset_index()
                df_full['date'] = pd.to_datetime(df_full['timestamp'], unit='ms')

                df_daily = df_full.tail(90).copy()
                if df_daily.empty: raise Exception("DataFrame diário vazio.")
                
                df_daily.ta.rsi(length=14, append=True)
                df_daily.ta.sma(close='close', length=10, append=True)
                df_daily.ta.sma(close='close', length=30, append=True)
                df_daily.ta.bbands(length=20, append=True)
                df_daily['volume_sma_20'] = df_daily['volume'].rolling(window=20).mean()
                df_daily.dropna(inplace=True)
                if df_daily.empty: raise Exception("Dados diários insuficientes para análise.")
                last_daily, prev_daily = df_daily.iloc[-1], df_daily.iloc[-2]

                df_weekly = df_full.resample('W-SUN', on='date').last()
                if df_weekly.empty: raise Exception("DataFrame semanal vazio.")
                df_weekly['SMA_10_weekly'] = df_weekly['close'].rolling(window=10).mean()
                df_weekly.dropna(inplace=True)
                if df_weekly.empty: raise Exception("Dados semanais insuficientes para SMA_10.")
                last_weekly = df_weekly.iloc[-1]
                
                signal_type = "HOLD"
                confidence = ""
                entry_price = float(last_daily['close'])

                # --- LÓGICA DE SINAIS (ESTRATÉGIA DUPLA) --- 
                
                # 1. Estratégia de Seguimento de Tendência
                is_in_squeeze = last_daily.get('BBB_20_2.0', 1) < 0.1
                if is_in_squeeze:
                    signal_type = "ALERTA"
                    confidence = " (Squeeze: Volatilidade Iminente)"
                else:
                    trend_buy_cond = last_daily['SMA_10'] > last_daily['SMA_30'] and prev_daily['SMA_10'] <= prev_daily['SMA_30']
                    trend_sell_cond = last_daily['SMA_10'] < last_daily['SMA_30'] and prev_daily['SMA_10'] >= prev_daily['SMA_30']
                    rsi_check_buy = last_daily.get('RSI_14', 50) < 70
                    rsi_check_sell = last_daily.get('RSI_14', 50) > 30
                    volume_check = last_daily['volume'] > (last_daily['volume_sma_20'] * 1.20)
                    weekly_trend_is_up = last_weekly['close'] > last_weekly['SMA_10_weekly']
                    
                    if trend_buy_cond and rsi_check_buy and volume_check and weekly_trend_is_up:
                        signal_type = "BUY"
                        confidence = " (Cruzamento de Médias)"
                    elif trend_sell_cond and rsi_check_sell and volume_check and not weekly_trend_is_up:
                        signal_type = "SELL"
                        confidence = " (Cruzamento de Médias)"

                # 2. Estratégia de Reversão à Média (se nenhum sinal de tendência foi encontrado)
                if signal_type == "HOLD":
                    reversion_buy_cond = last_daily.get('BBP_20_2.0', 0.5) < 0.20 and last_daily.get('RSI_14', 50) < 40
                    reversion_sell_cond = last_daily.get('BBP_20_2.0', 0.5) > 0.80 and last_daily.get('RSI_14', 50) > 60

                    if reversion_buy_cond:
                        signal_type = "BUY"
                        confidence = " (Reversão à Média)"
                    elif reversion_sell_cond:
                        signal_type = "SELL"
                        confidence = " (Reversão à Média)"

                return {
                    "pair": symbol.replace("USDT", "/USDT"), "entry": round(entry_price, 4),
                    "signal": f"{signal_type}{confidence}", "stop": round(entry_price * 0.98, 4),
                    "target": round(entry_price * 1.03, 4), "rsi": round(last_daily.get('RSI_14', 50), 2),
                    "bb_upper": round(last_daily.get('BBU_20_2.0', 0), 4),
                    "bb_lower": round(last_daily.get('BBL_20_2.0', 0), 4),
                    "timestamp": datetime.now().isoformat()
                }

            except Exception as e:
                logging.error(f"Erro ao gerar sinal para {symbol}: {e}")
                return {"pair": symbol.replace("USDT", "/USDT"), "signal": "ERROR", "error_message": str(e)}

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

        @app.route("/")
        def home():
            return jsonify({"message": "Crypton Signals API v10.0 (API Key Fix)", "status": "online"})

        @app.route("/signals")
        @cache.cached()
        def get_signals():
            logging.info("CACHE MISS: Gerando novos sinais (com chave de API) e salvando no histórico.")
            signals = []
            for symbol in COINGECKO_MAP.keys():
                signal = get_technical_signal(symbol)
                signals.append(signal)
                if signal.get("signal") != "ERROR" and "ALERTA" not in signal.get("signal"):
                    salvar_sinal_no_historico(signal)
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

        @app.route("/history/chart_data")
        def get_chart_data():
            pair_name = request.args.get('pair', type=str)
            if not pair_name:
                return jsonify({"error": "O parâmetro 'pair' é obrigatório."}), 400
            
            symbol = pair_name.replace("/", "")
            coingecko_id = COINGECKO_MAP.get(symbol)
            if not coingecko_id:
                return jsonify({"error": "Par inválido."}), 400

            try:
                logging.info(f"Buscando dados de preço para o gráfico de {pair_name} (com chave de API)")
                url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart?vs_currency=usd&days=90&interval=daily'
                price_data = get_coingecko_data(url )
                prices = price_data.get('prices', [])

                sinais_do_par = SinalHistorico.query.filter_by(pair=pair_name).order_by(SinalHistorico.timestamp.asc()).all()
                markers = []
                for sinal in sinais_do_par:
                    if "BUY" in sinal.signal.upper() or "SELL" in sinal.signal.upper():
                        markers.append({
                            "timestamp": int(sinal.timestamp.timestamp() * 1000),
                            "price": sinal.entry,
                            "type": "BUY" if "BUY" in sinal.signal.upper() else "SELL",
                            "text": "C" if "BUY" in sinal.signal.upper() else "V"
                        })

                return jsonify({
                    "prices": prices,
                    "markers": markers
                })

            except Exception as e:
                logging.error(f"Erro ao buscar dados do gráfico para {pair_name}: {e}")
                return jsonify({"error": "Não foi possível buscar os dados do gráfico."}), 500

        @app.route("/setup/database/create-tables-secret-path")
        def setup_database():
            try:
                with app.app_context():
                    db.create_all()
                return jsonify({"message": "SUCESSO: As tabelas da base de dados foram criadas (ou já existiam)."}), 200
            except Exception as e:
                logging.error(f"ERRO AO CRIAR TABELAS: {e}")
                return jsonify({"error": str(e)}), 500
        
        @app.route("/admin/cache/clear-secret-path")
        def clear_cache():
            try:
                cache.clear()
                return jsonify({"message": "SUCESSO: A cache foi limpa."}), 200
            except Exception as e:
                logging.error(f"ERRO AO LIMPAR A CACHE: {e}")
                return jsonify({"error": str(e)}), 500

    return app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
