# Crypton Signals - API e Frontend

Este projeto consiste num backend em Python (Flask) que gera sinais de trading para criptomoedas e um frontend em React para exibir esses sinais.

## Visão Geral

O objetivo é fornecer sinais de COMPRA, VENDA ou HOLD baseados em análise técnica, ajudando a identificar oportunidades de mercado.

- **Backend**: Desenvolvido em Flask, consome dados da API da CoinGecko.
- **Frontend**: Desenvolvido em React, exibe os sinais de forma clara e intuitiva.

---

## Funcionalidades do Backend

- **Análise Técnica Multi-indicador**: Os sinais são gerados usando uma combinação de:
  - **Cruzamento de Médias Móveis (SMA 10/30)**: Para identificar a direção da tendência.
  - **Índice de Força Relativa (RSI)**: Para medir o momentum e evitar zonas de sobrecompra/sobrevenda.
  - **Análise de Volume**: Para confirmar a força do movimento, exigindo que o volume esteja acima da sua média de 20 dias.
- **Caching Inteligente**: Os resultados são guardados em cache por 6 horas para melhorar drasticamente a performance e respeitar os limites da API externa.
- **Endpoints**:
  - `/signals`: Retorna a lista de sinais para os pares de moedas configurados.
  - `/health`: Endpoint de verificação de saúde.

---

## Como Executar o Projeto

### Pré-requisitos

- Python 3.11+
- Node.js e npm

### Backend

1.  Navegue até a pasta `backend`:
    ```bash
    cd backend
    ```
2.  Crie um ambiente virtual e ative-o:
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # No Windows: .venv\Scripts\activate
    ```
3.  Instale as dependências:
    ```bash
    pip install -r requirements.txt
    ```
4.  Execute a aplicação:
    ```bash
    gunicorn main:app
    ```
    O backend estará a correr em `http://127.0.0.1:8000`.

### Frontend

1.  Navegue até a pasta `frontend`:
    ```bash
    cd frontend
    ```
2.  Instale as dependências:
    ```bash
    npm install
    ```
3.  Execute a aplicação de desenvolvimento:
    ```bash
    npm start
    ```
    O frontend estará acessível em `http://localhost:3000`.

---

## Deploy

- O **backend** está configurado para deploy na [Railway](https://railway.app/ ) usando o `Procfile`.
- O **frontend** está configurado para deploy em serviços como [Netlify](https://www.netlify.com/ ) ou [Vercel](https://vercel.com/ ).
