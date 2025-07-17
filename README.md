# API de Sinais de Criptomoedas - Railway

Sistema completo de API com dados e sinais 100% reais para Railway.

## 🚀 **Características:**

### ✅ **Dados 100% Reais:**
- **CoinGecko API** - Preços reais em tempo real
- **Dados históricos** - 7 dias para análise técnica
- **Preços atuais** - Direto das APIs oficiais
- **Market Cap e Volume** - Dados reais de mercado

### ✅ **Análise Técnica Real:**
- **RSI** - Relative Strength Index calculado
- **Médias Móveis** - SMA5, SMA10, SMA20
- **Bandas de Bollinger** - Indicador de volatilidade
- **Volatilidade** - Cálculo baseado em dados históricos
- **Sistema de Score** - Combinação de múltiplos indicadores

### ✅ **Sinais Inteligentes:**
- **BUY/SELL/HOLD** - Baseados em análise técnica real
- **Força e Confiança** - Percentuais calculados
- **Target Price** - Preço alvo baseado em análise
- **Stop Loss** - Nível de stop calculado
- **Razões Técnicas** - Explicação dos sinais

## 📡 **Endpoints da API:**

### **GET /** 
- **Descrição:** Informações gerais da API
- **Resposta:** Status e documentação

### **GET /api/health**
- **Descrição:** Status de saúde da API
- **Resposta:** Status operacional e cache

### **GET /api/signals**
- **Descrição:** Todos os sinais com análise técnica
- **Resposta:** Array completo de sinais
- **Cache:** 2 minutos

### **GET /api/signals/{symbol}**
- **Descrição:** Sinal específico de uma moeda
- **Parâmetros:** symbol (BTC, ETH, DOGE, etc.)
- **Resposta:** Dados detalhados da moeda

### **GET /api/stats**
- **Descrição:** Estatísticas gerais do mercado
- **Resposta:** Resumo e top performers

## 🔧 **Exemplo de Resposta:**

```json
{
  "signals": [
    {
      "symbol": "BTC",
      "name": "Bitcoin",
      "current_price": 67234.56,
      "change_24h": 2.34,
      "signal": "BUY",
      "strength": 78,
      "confidence": 82,
      "target_price": 72613.32,
      "stop_loss": 63872.83,
      "technical_indicators": {
        "rsi": 45.2,
        "sma5": 66890.12,
        "sma10": 65234.78,
        "sma20": 64123.45,
        "volatility": 3.2,
        "bollinger_upper": 69500.00,
        "bollinger_middle": 67000.00,
        "bollinger_lower": 64500.00
      },
      "reasons": [
        "RSI baixo (45.2)",
        "Preço acima da SMA5",
        "Momentum positivo (+2.3%)"
      ],
      "timestamp": "2025-07-16T10:30:00",
      "data_source": "CoinGecko API"
    }
  ],
  "total_signals": 8,
  "buy_signals": 3,
  "sell_signals": 2,
  "hold_signals": 3,
  "last_updated": "2025-07-16T10:30:00"
}
```

## 🚀 **Deploy no Railway:**

1. **Conecte** seu repositório GitHub ao Railway
2. **Configure** as variáveis de ambiente (se necessário)
3. **Deploy** automático será iniciado
4. **URL** será gerada automaticamente

## 📊 **Moedas Suportadas:**

- **BTC** - Bitcoin
- **ETH** - Ethereum  
- **DOGE** - Dogecoin
- **SHIB** - Shiba Inu
- **PEPE** - Pepe
- **FLOKI** - Floki
- **BONK** - Bonk
- **BNB** - Binance Coin

## ⚡ **Performance:**

- **Cache:** 2 minutos para otimização
- **Rate Limiting:** Respeitado para APIs externas
- **Timeout:** 10 segundos para requests
- **Logging:** Completo para debugging
- **Error Handling:** Robusto e informativo

## 🔒 **Segurança:**

- **CORS** habilitado para frontend
- **Error Handling** sem exposição de dados sensíveis
- **Timeout** configurado para evitar travamentos
- **Logging** para monitoramento

## 📈 **Algoritmo de Sinais:**

### **Sistema de Score:**
- **RSI < 30:** +3 pontos (oversold)
- **RSI > 70:** -3 pontos (overbought)
- **Médias alinhadas:** ±2 pontos
- **Bandas de Bollinger:** ±2 pontos
- **Momentum forte:** ±2 pontos
- **Volume crescente:** ±1 ponto

### **Classificação:**
- **Score ≥ 4:** BUY (alta confiança)
- **Score ≤ -4:** SELL (alta confiança)
- **Score 2-3:** BUY (média confiança)
- **Score -2 a -3:** SELL (média confiança)
- **Score -1 a 1:** HOLD (neutro)

## ✅ **Garantias:**

- ✅ **Dados 100% reais** das APIs oficiais
- ✅ **Análise técnica completa** e precisa
- ✅ **Sinais baseados em múltiplos indicadores**
- ✅ **Cache inteligente** para performance
- ✅ **Error handling robusto**
- ✅ **Logging completo** para debugging
- ✅ **Compatível com Railway**

