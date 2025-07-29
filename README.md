# Painel de Sinais de Criptomoedas

Este é um painel de análise técnica em tempo real que consome uma API para exibir sinais de trading (COMPRA/VENDA/MANTER) para diversos pares de criptomoedas. A aplicação foi desenvolvida com HTML, CSS e JavaScript puros, focando em fornecer insights rápidos para traders.

![demo](https://i.imgur.com/SUG5l5c.gif ) 
*Exemplo de funcionamento da interface.*

---

## ✨ Funcionalidades

*   **Painel Dinâmico:** Os sinais são atualizados automaticamente, sem a necessidade de recarregar a página.
*   **Seleção de Timeframe:** Permite analisar os sinais em diferentes tempos gráficos (1m, 5m, 15m, 1h, 1d).
*   **Alvo de Lucro (Take Profit):** Cada sinal exibe um "alvo" de lucro dinâmico, calculado com base na confiança do sinal.
*   **Destaque Visual:** Cards com alvos atingidos são destacados com uma borda verde para fácil identificação.
*   **Sistema de Alertas:** Permite ao usuário criar alertas de preço personalizados que são salvos localmente no navegador.
*   **Indicadores Técnicos:** Exibe informações como RSI, MACD e Bandas de Bollinger.
*   **Acesso com Senha:** Uma tela de login simples protege o acesso ao painel.

---

## 🛠️ Tecnologias Utilizadas

*   **Frontend:**
    *   HTML5
    *   CSS3 (com Variáveis CSS para fácil customização)
    *   JavaScript (Vanilla JS, sem frameworks)
*   **APIs & Bibliotecas:**
    *   [Font Awesome](https://fontawesome.com/ ) para ícones.
    *   API de Sinais customizada para dados de mercado.
*   **Hospedagem:**
    *   O projeto está configurado para deploy contínuo no [Netlify](https://www.netlify.com/ ).

---

## 🚀 Como Executar

### Pré-requisitos
*   Um navegador web moderno (Google Chrome, Firefox, etc.).

### Execução Local
1.  **Clone o repositório:**
    ```bash
    git clone https://github.com/Peixoto27/Crypto.git
    ```
2.  **Navegue até a pasta do projeto:**
    ```bash
    cd Crypto
    ```
3.  **Abra o arquivo `index.html`** diretamente no seu navegador.

### Deploy
O projeto está configurado para ser "arrastado e solto" ou conectado via Git a qualquer serviço de hospedagem de sites estáticos como Netlify, Vercel ou GitHub Pages. As configurações de build devem ser deixadas em branco.

---

## 📂 Estrutura dos Arquivos

```
/
├── index.html      # Arquivo principal com a estrutura da página
├── style.css       # Folha de estilos para toda a aplicação
├── app.js          # Lógica principal em JavaScript
└── README.md       # Este arquivo de documentação
```
