import os
import logging
from datetime import datetime
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_caching import Cache
import pandas as pd
import pandas_ta as ta
import requests

# --- CONFIGURAÇÕES INICIAIS ---
db = SQLAlchemy()
cache = Cache()
cors = CORS()

# --- MODELO DO BANCO DE DADOS ---
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
        return {
            'id': self.id,
            'pair': self.pair,
            'entry': self.entry,
            'signal': self.signal,
            'stop': self.stop,
            'target': self.target,
            'rsi': self.rsi,
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }

# --- FUNÇÃO PRINCIPAL ---
def create_app():
    app = Flask(__name__)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # Configurações da aplicação
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL").replace("postgres://", "postgresql://")
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['CACHE_TYPE'] = 'SimpleCache'
    app.config['CACHE_DEFAULT_TIMEOUT'] = 21600
    app.config['COINGECKO_API_KEY'] = os.getenv("COINGECKO_API_KEY")

    db.init_app(app)
    cache.init_app(app)
    cors.init_app(app)

    COINGECKO_MAP = {
        "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "XRPUSDT": "ripple",
        "SOLUSDT": "solana", "ADAUSDT": "cardano"
    }

    def get_coingecko_data(url):
        headers = {'x-cg-demo-api-key': app.config['COINGECKO_API_KEY']} if app.config['COINGECKO_API_KEY'] else {}
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def gerar_sinal_tecnico(symbol):
        try:
            coingecko_id = COINGECKO_MAP[symbol]
            url = f'https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart?vs_currency=usd&days=365&interval=daily'
            dados = get_coingecko_data(url)

            df = pd.DataFrame(dados['prices'], columns=['timestamp', 'close'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)

            df['rsi'] = ta.rsi(df['close'], length=14)
            df['macd'], df['macd_signal'], _ = ta.macd(df['close'])
            df['adx'] = ta.adx(df['close'], length=14)['ADX_14']
            df.dropna(inplace=True)

            atual = df.iloc[-1]
            anterior = df.iloc[-2]

            entry = round(atual['close'], 4)
            stop = round(entry * 0.98, 4)
            target = round(entry * 1.03, 4)

            justificativa = []
            confianca = "Baixa"

            if atual['macd'] > atual['macd_signal'] and atual['rsi'] > 50 and atual['adx'] > 20:
                sinal = "BUY"
                justificativa.append("MACD acima da linha de sinal")
                justificativa.append("RSI > 50")
                justificativa.append("Tendência detectada (ADX > 20)")
                confianca = "Alta"
            elif atual['macd'] < atual['macd_signal'] and atual['rsi'] < 50 and atual['adx'] > 20:
                sinal = "SELL"
                justificativa.append("MACD abaixo da linha de sinal")
                justificativa.append("RSI < 50")
                justificativa.append("Tendência detectada (ADX > 20)")
                confianca = "Alta"
            else:
                sinal = "HOLD"
                justificativa.append("Sem convergência de indicadores")
                confianca = "Baixa"

            return {
                'pair': symbol.replace("USDT", "/USDT"),
                'entry': entry,
                'signal': sinal,
                'stop': stop,
                'target': target,
                'rsi': round(atual['rsi'], 2),
                'confidence': confianca,
                'justification': "; ".join(justificativa),
                'timestamp': datetime.utcnow().isoformat()
            }

        except Exception as e:
            logging.error(f"Erro no sinal de {symbol}: {e}")
            return {'pair': symbol, 'signal': 'ERROR', 'error_message': str(e)}

    def salvar_no_historico(sinal):
        if sinal['signal'] == "ERROR": return
        sinal_bd = SinalHistorico(
            pair=sinal['pair'], entry=sinal['entry'], signal=sinal['signal'],
            stop=sinal['stop'], target=sinal['target'], rsi=sinal['rsi']
        )
        db.session.add(sinal_bd)
        db.session.commit()

    @app.route("/")
    def index():
        return jsonify({"message": "API de Sinais de Criptomoedas - Versão Avançada", "status": "online"})

    @app.route("/signals")
    @cache.cached()
    def sinais():
        resultados = []
        for s in COINGECKO_MAP.keys():
            sinal = gerar_sinal_tecnico(s)
            resultados.append(sinal)
            if sinal['signal'] not in ["HOLD", "ERROR"]:
                salvar_no_historico(sinal)
        return jsonify({"signals": resultados, "count": len(resultados), "timestamp": datetime.utcnow().isoformat()})

    @app.route("/signals/history")
    def historico():
        sinais = SinalHistorico.query.order_by(SinalHistorico.timestamp.desc()).all()
        por_par = {}
        for s in sinais:
            if s.pair not in por_par:
                por_par[s.pair] = []
            por_par[s.pair].append(s.to_dict())
        return jsonify(por_par)

    @app.route("/admin/cache/clear-secret-path")
    def limpar_cache():
        cache.clear()
        return jsonify({"message": "Cache limpa com sucesso."})

    @app.route("/setup/database/create-tables-secret-path")
    def criar_tabelas():
        db.create_all()
        return jsonify({"message": "Tabelas criadas."})

    return app

app = create_app()

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
