import os
from flask import Flask, jsonify
from flask_cors import CORS
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime

app = Flask(__name__)
CORS(app)

def get_technical_signal(symbol):
    try:
        url = f'https://api.binance.us/api/v3/klines?symbol={symbol}&interval=1h&limit=100'
        response = requests.get(url)
        data = response.json()

        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        df.ta.rsi(length=14, append=True)
        df.ta.macd(append=True)
        df.ta.sma(length=10, append=True)
        df.ta.sma(length=30, append=True)
        df.dropna(inplace=True)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        score = 0
        reasons = []

        if last['SMA_10'] > last['SMA_30'] and prev['SMA_10'] <= prev['SMA_30']:
            score += 30
            reasons.append("Cruzamento de médias para cima")

        if last['MACD_12_26_9'] > last['MACDs_12_26_9']:
            score += 20
            reasons.append("MACD positivo")

        if last['RSI_14'] > 50 and last['RSI_14'] < 70:
            score += 20
            reasons.append("RSI em tendência saudável")

        confidence = min(100, score)

        if confidence < 65:
            return None

        entry = last['close']
        target = round(entry * 1.03, 2)
        stop = round(entry * 0.98, 2)

        return {
            "pair": symbol.replace("USDT", "/USDT"),
            "entry": round(entry, 2),
            "target": target,
            "stop": stop,
            "signal": "BUY",
            "confidence": confidence,
            "timestamp": datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC"),
            "rr_ratio": "1:2",
            "potential": f"{round((target - entry) / entry * 100, 2)}%"
        }
    except Exception as e:
        return None

@app.route("/signals")
def signals():
    symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
    result = [s for s in [get_technical_signal(sym) for sym in symbols] if s]
    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
