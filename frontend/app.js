// Variáveis globais
let allSignals = [];
let activeAlerts = JSON.parse(localStorage.getItem('sinaisProAlerts') || '[]');
let activeTimeframe = '1d';
let currentSignalsOnScreen = new Map();
let atingidos = new Set(); // NOVO: Para controlar alvos já atingidos

// Ícones das moedas (mantido o seu)
const coinIcons = {
    'BTC/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/btc.svg',
    'ETH/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/eth.svg',
    'XRP/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/xrp.svg',
    'SOL/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/sol.svg',
    'ADA/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/ada.svg',
    'default': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/generic.svg'
};

// --- FUNÇÕES NOVAS (Alvo Inteligente ) ---

function calcularAlvo(signal) {
    const precoAtual = signal.price;
    const conf = parseInt(signal.confidence.split('/')[0]);

    let margemPercentual = 0;
    if (conf >= 8) margemPercentual = 6;
    else if (conf >= 6) margemPercentual = 4;
    else if (conf >= 4) margemPercentual = 2;
    else margemPercentual = 1;

    let valorAlvo = precoAtual;
    const signalType = signal.signal.toUpperCase();

    if (signalType.includes("BUY")) {
        valorAlvo = precoAtual * (1 + margemPercentual / 100);
    } else if (signalType.includes("SELL")) {
        valorAlvo = precoAtual * (1 - margemPercentual / 100);
    }

    return {
        valor: valorAlvo.toFixed(4),
        percentual: margemPercentual
    };
}

function checkAlvosAtingidos(signals) {
    signals.forEach(signal => {
        const key = `${signal.pair}-${activeTimeframe}`;
        if (atingidos.has(key)) return; // Evitar alerta repetido

        const alvo = calcularAlvo(signal);
        const preco = signal.price;
        const tipo = signal.signal.toUpperCase();

        let atingiu = false;
        if (tipo.includes("BUY") && preco >= parseFloat(alvo.valor)) atingiu = true;
        else if (tipo.includes("SELL") && preco <= parseFloat(alvo.valor)) atingiu = true;

        if (atingiu) {
            atingidos.add(key);
            const card = document.querySelector(`[data-pair="${signal.pair}"]`);
            if (card) card.classList.add('atingido'); // Destaque visual
            showNotification(`🎯 Alvo Atingido: ${signal.pair} alcançou $${alvo.valor}!`);
        }
    });
}


// --- SEU CÓDIGO (COM AJUSTES) ---

document.addEventListener('DOMContentLoaded', () => {
    // ... (sua lógica de login permanece a mesma)
});

// ... (sua função checkPassword permanece a mesma)

function initializeApp() {
    setupEventListeners();
    loadAlertsFromStorage();
    fetchAndDisplaySignals(activeTimeframe);
    
    setInterval(() => {
        fetchAndDisplaySignals(activeTimeframe);
    }, 30000);
}

function setupEventListeners() {
    // MODIFICADO: Limpar o set de alvos atingidos ao mudar o timeframe
    const timeframeButtons = document.querySelectorAll('.timeframe-selector button');
    timeframeButtons.forEach(button => {
        button.addEventListener('click', () => {
            timeframeButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            activeTimeframe = button.dataset.timeframe;
            
            currentSignalsOnScreen.clear();
            atingidos.clear(); // Limpa os alvos atingidos
            const container = document.getElementById('signals-container');
            container.innerHTML = '';
            
            fetchAndDisplaySignals(activeTimeframe);
        });
    });
    // ... (o resto da sua função setupEventListeners permanece a mesma)
}

async function fetchAndDisplaySignals(timeframe) {
    // ... (sua função fetchAndDisplaySignals, com uma adição)
    try {
        // ... (seu código de fetch)
        const signals = await response.json();
        allSignals = signals;
        
        updateSignalsSmooth(signals);
        updateLastUpdatedTimestamp();
        checkAlerts(signals);
        checkAlvosAtingidos(signals); // NOVO: Chamar a verificação de alvos
        
    } catch (error) {
        // ... (seu tratamento de erro)
    }
}

// ... (sua função updateSignalsSmooth permanece a mesma)
// ... (sua função hasSignalChanged permanece a mesma)

function updateExistingCard(cardElement, newSignal) {
    // MODIFICADO: Adicionar atualização do alvo
    // ... (sua lógica de atualização de preço, confiança, etc.)

    // Atualizar alvo
    const alvo = calcularAlvo(newSignal);
    const targetElement = cardElement.querySelector('.coin-target');
    if (targetElement) {
        targetElement.textContent = `🎯 Alvo: $${alvo.valor} (+${alvo.percentual}%)`;
    }
    
    // ... (o resto da sua função de atualização)
}

function createSignalCard(signal) {
    // MODIFICADO: Adicionar o elemento do alvo no HTML do card
    const card = document.createElement('div');
    card.className = 'signal-card';
    card.setAttribute('data-pair', signal.pair);

    // ... (sua lógica de signalType, iconUrl, etc.)
    
    const alvo = calcularAlvo(signal); // NOVO: Calcular o alvo

    card.innerHTML = `
        <div class="card-header">
            <div class="coin-info">
                <img src="${iconUrl}" alt="${signal.pair}" class="coin-icon">
                <div class="coin-details">
                    <h3>${signal.pair}</h3>
                    <div class="coin-price">$${signal.price}</div>
                    <!-- NOVO ELEMENTO -->
                    <div class="coin-target">🎯 Alvo: $${alvo.valor} (+${alvo.percentual}%)</div>
                </div>
            </div>
            <div class="confidence-badge" style="color: ${confidenceColor}; border-color: ${confidenceColor};">
                ${signal.confidence}
            </div>
        </div>
        <!-- O resto do seu card.innerHTML permanece o mesmo -->
        ...
    `;

    return card;
}

// ... (todas as suas outras funções: getSignalDescription, applyFilters, displaySignals, modais, alertas, etc., permanecem as mesmas)
