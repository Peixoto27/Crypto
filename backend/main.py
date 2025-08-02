import os
from flask import Flask, jsonify

# --- CÓDIGO MÍNIMO CORRIGIDO PARA TESTE --- 

print("DEBUG: main.py está a ser executado.")

def create_app():
    print("DEBUG: create_app() está a ser chamada.")
    app = Flask(__name__)

    @app.route("/")
    def home():
        print("DEBUG: Rota / acedida.")
        port_var = os.environ.get("PORT", "Não encontrada")
        db_url_var = os.environ.get("DATABASE_URL", "Não encontrada")
        
        return jsonify({
            "message": "Aplicação Mínima Online!",
            "status": "OK",
            "PORT_VAR": port_var,
            "DB_URL_VAR_EXISTS": "Sim" if db_url_var != "Não encontrada" else "Não"
        })

    print("DEBUG: Aplicação Flask criada e rotas registadas.")
    return app

# APLICAÇÃO É CRIADA DIRETAMENTE AQUI, FORA DO BLOCO if __name__ == "__main__":
app = create_app()

print("DEBUG: Variável 'app' definida e pronta para Gunicorn.")

# O bloco if __name__ == "__main__": não é necessário para o Gunicorn
# mas pode ser mantido para testes locais se desejar, mas sem o app.run()
# if __name__ == "__main__":
#    print("DEBUG: Bloco __name__ == '__main__' está a ser executado (apenas para testes locais).")
#    port = int(os.environ.get("PORT", 5000))
#    print(f"DEBUG: Iniciando servidor Flask localmente na porta {port}")
#    app.run(host='0.0.0.0', port=port) # ESTA LINHA NÃO DEVE SER EXECUTADA PELO GUNICORN
