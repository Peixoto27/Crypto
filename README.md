✅ Funcionalidades

🔍 Análise técnica usando SMA, RSI e MACD

📈 Geração de sinal de compra quando critérios são atendidos

🎯 Alvo e stop calculados automaticamente

🔢 Score de confiança no sinal

🧠 Filtragem para exibir apenas sinais com mais de 65% de confiança

⏱️ Timestamp da geração

🔁 Atualização automática no frontend a cada 20 minutos



---

🔌 Endpoint da API

GET /signals

Exemplo de resposta:

[
  {
    "pair": "BTC/USDT",
    "entry": 45100.25,
    "target": 46453.26,
    "stop": 44198.24,
    "signal": "BUY",
    "confidence": 85,
    "timestamp": "25/07/2025 14:20 UTC",
    "rr_ratio": "1:2",
    "potential": "3.0%"
  }
]


---

🚀 Deploy no Railway

1. Clone o projeto (opcional)

git clone https://github.com/openai-crypton/crypton-backend.git

2. Vá até Railway

Clique em "New Project"

Selecione "Deploy from GitHub"

Escolha o repositório crypton-backend

Railway detectará como projeto Python e iniciará o build



---

🧪 Teste local

Requisitos:

Python 3.8+

pip


Passos:

pip install -r requirements.txt
python main.py

A API ficará disponível em:

http://localhost:5000/signals


---

🛠️ Estrutura do Projeto

.
├── main.py            # Código principal do backend (Flask + lógica técnica)
├── requirements.txt   # Dependências
└── Procfile           # Arquivo para deploy no Railway


---

🔐 Segurança

Este projeto está preparado para uso com autenticação futura.
Atualmente a proteção de acesso é feita no frontend, com senha definida (ex: Zoe1001).


---
