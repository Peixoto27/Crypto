## Crypton Signals PRO (CoinGecko + Telegram)

### Variáveis (.env)
- TRAINING_ENABLED=true|false
- MIN_CONFIDENCE=0.75
- API_DELAY_SEC=2.5
- TELEGRAM_BOT_TOKEN=seu_token (opcional; no notifier já está fixo)
- TELEGRAM_CHAT_ID=@botsinaistop (opcional)
- LOG_LEVEL=INFO

### Rodar
1) `pip install -r requirements.txt`
2) Coloque `.env` na raiz (se quiser sobrepor configs).
3) `python main.py`

### Pipeline
Coleta (CoinGecko) → Indicadores → Score → Filtra ≥ MIN_CONFIDENCE → `signals.json` → Telegram → (Treino opcional)
