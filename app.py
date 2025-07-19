from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "docs": "https://github.com/seu-repo"
    })

if __name__ == '__main__':
    app.run()
