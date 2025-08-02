import os
from flask import Flask, jsonify

# --- CÓDIGO MÍNIMO PARA TESTE ---

def create_app():
    app = Flask(__name__)

    @app.route("/")
    def home():
        # Testa se a variável de ambiente do Railway está acessível
        port_var = os.environ.get('PORT', 'Não encontrada')
        db_url_var = os.environ.get('DATABASE_URL', 'Não encontrada')
        
        return jsonify({
            "message": "Aplicação Mínima Online!",
            "status": "OK",
            "PORT_VAR": port_var,
            "DB_URL_VAR_EXISTS": "Sim" if db_url_var != 'Não encontrada' else "Não"
        })

    return app

app = create_app()

if __name__ == "__main__":
    # Este bloco não é usado pelo Gunicorn, mas é bom para testes locais
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
