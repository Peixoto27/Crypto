# 🔥 SinaisPro – Sistema Inteligente de Sinais de Criptomoedas

**SinaisPro** é um projeto completo com frontend e backend integrados para exibir sinais técnicos de compra, venda ou espera (HOLD) de criptomoedas em tempo real, com base em indicadores como RSI, MACD, e Bollinger Bands.

🔒 Acesso protegido por senha  
📈 Alvo inteligente por confiança  
⚠️ Notificações quando o alvo é atingido  
📊 Interface moderna e responsiva  
🧠 Backend com API real em Python (Railway)

---

## 📁 Estrutura do Projeto

```
.
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── (ícones, imagens, etc.)
└── backend/
    ├── main.py
    ├── requirements.txt
    └── Procfile
```

---

## 🌐 Demonstração

- 🔗 Site Online (Netlify): **https://sinaispro.netlify.app**  
- 🔗 API Real (Railway): **https://sinais-production.up.railway.app/signals?timeframe=1d**

> Login: Senha de acesso = `ope1001`

---

## ⚙️ Funcionalidades Principais

- Autenticação por senha
- Seleção de timeframe (1d, 4h, etc.)
- Filtros por tipo de sinal (BUY, SELL, HOLD)
- Indicadores técnicos: RSI, MACD, Bollinger Bands
- Alvo dinâmico baseado na **confiança do sinal**
- Notificação automática ao atingir o alvo
- Destaque visual no card do ativo com alvo alcançado
- Painel de alertas personalizados por par e valor

---

## 🎯 Lógica de Alvo Inteligente

Para cada sinal, o sistema calcula automaticamente um **valor-alvo seguro**, com base na **confiança (confidence)**:

| Confiança | Alvo (%) |
|-----------|----------|
| ≥ 8       | 6%       |
| ≥ 6       | 4%       |
| ≥ 4       | 2%       |
| < 4       | 1%       |

Exemplo:
```
Preço atual: $2.00
Confiança: 7/10
🎯 Alvo: $2.08 (+4%)
```

---

## 🧠 Backend (Python / FastAPI)

- Implementado em Python com FastAPI
- Endpoint principal: `/signals?timeframe=1d`
- Gera sinais simulados ou reais (dependendo da versão usada)
- Deploy no Railway com suporte para ambientes

---

## 🚀 Como rodar localmente

### 🔧 Backend

```bash
cd backend/
pip install -r requirements.txt
uvicorn main:app --reload
```

### 💻 Frontend

Basta abrir `index.html` diretamente no navegador ou usar o Netlify.

---

## 💬 Notificações

- Notificações do navegador (via Web API)
- Alerta `alert()` quando o alvo for atingido
- Cards com destaque visual (`borda + glow verde`)

---

## 📦 Deploy

- **Frontend**: Netlify (public folder: `/frontend`)
- **Backend**: Railway (root: `/backend`)

---

## 📄 Licença

Projeto privado. Para fins de uso comercial ou parceria, entre em contato.

---

## 👨‍💻 Autor

Desenvolvido por [Seu Nome ou Empresa]  
📧 Email: contato@seudominio.com  
📱 Instagram: [@seuperfil]
