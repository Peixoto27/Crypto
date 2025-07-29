# Painel de Sinais de Criptomoedas

![Captura de Tela do Painel](https://i.imgur.com/link-para-sua-imagem.png ) <!-- Opcional: tire um print da tela e suba no imgur.com para colocar aqui -->

Um painel de análise técnica em tempo real que busca sinais de trading (COMPRA/VENDA/MANTER) para diversos pares de criptomoedas. A aplicação foi desenvolvida para fornecer insights rápidos aos traders, com atualização automática, sistema de alertas personalizáveis e cálculo de alvos de lucro dinâmicos.

---

## ✨ Funcionalidades Principais

*   **Painel em Tempo Real:** Os sinais são atualizados automaticamente a cada 30 segundos, sem a necessidade de recarregar a página.
*   **Múltiplos Timeframes:** Analise os sinais em diferentes tempos gráficos (1m, 5m, 15m, 1h, etc.).
*   **Alvo Inteligente (Take Profit):** Cada sinal de compra ou venda exibe um "alvo" de lucro dinâmico, calculado com base na confiança do sinal.
*   **Destaque Visual de Alvo Atingido:** Quando o preço de um ativo atinge o alvo calculado, o card correspondente é destacado visualmente na interface.
*   **Sistema de Alertas Personalizados:**
    *   Crie alertas de preço para qualquer par de moedas (ex: "alertar quando BTC ultrapassar $70.000").
    *   Os alertas são salvos no navegador (`localStorage`) e persistem entre sessões.
    *   Receba notificações no navegador quando um alerta for disparado.
*   **Indicadores Técnicos:** Exibe indicadores essenciais como RSI, MACD e Bandas de Bollinger para cada sinal.
*   **Interface Responsiva:** O layout se adapta a diferentes tamanhos de tela, de desktops a dispositivos móveis.
*   **Acesso Restrito:** Uma tela de login simples protege o acesso ao painel.

---

## 🛠️ Tecnologias Utilizadas

*   **Frontend:** HTML5, CSS3, JavaScript (Vanilla JS)
*   **Ícones:** Font Awesome
*   **API de Sinais:** A aplicação consome uma API REST para obter os dados dos sinais em tempo real.
*   **Hospedagem:** O projeto está pronto para deploy em plataformas como Netlify, Vercel ou GitHub Pages.

---

## 🚀 Como Executar o Projeto

### Pré-requisitos

*   Um navegador web moderno (Chrome, Firefox, Edge, etc.).
*   Acesso à internet para consumir a API de sinais.

### Execução Local

1.  **Clone o repositório:**
    ```bash
    git clone https://github.com/seu-usuario/seu-repositorio.git
    ```

2.  **Navegue até a pasta do projeto:**
    ```bash
    cd seu-repositorio
    ```

3.  **Abra o arquivo `index.html`:**
    *   Você pode simplesmente abrir o arquivo `index.html` diretamente no seu navegador.
    *   Para uma melhor experiência (evitando problemas com CORS, se aplicável ), você pode usar um servidor local. Se tiver o VS Code, a extensão **Live Server** é uma ótima opção.

4.  **Faça o login:**
    *   A senha padrão para acesso está definida no arquivo `app.js`.

---

## ⚙️ Estrutura dos Arquivos

```
.
├── index.html    # Estrutura principal da página
├── style.css     # Todos os estilos visuais
├── app.js        # Toda a lógica da aplicação
└── README.md     # Este arquivo
```

---

## 🤝 Contribuições

Contribuições são bem-vindas! Se você tiver ideias para novas funcionalidades ou encontrar um bug, sinta-se à vontade para abrir uma *issue* ou enviar um *pull request*.

