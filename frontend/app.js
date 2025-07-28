// Variáveis globais
let allSignals = [];
let activeAlerts = JSON.parse(localStorage.getItem('sinaisProAlerts') || '[]');
let activeTimeframe = '1d';
let currentSignalsOnScreen = new Map(); // Para controlar o que já está na tela

// Ícones das moedas
const coinIcons = {
    'BTC/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/btc.svg',
    'ETH/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/eth.svg',
    'XRP/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/xrp.svg',
    'SOL/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/sol.svg',
    'ADA/USDT': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/ada.svg',
    'default': 'https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25669/svg/color/generic.svg'
};

// Inicialização quando a página carrega
document.addEventListener('DOMContentLoaded', () => {
    const loginButton = document.getElementById('login-button');
    const passwordInput = document.getElementById('password-input');

    if (loginButton) {
        loginButton.addEventListener('click', checkPassword);
    }
    if (passwordInput) {
        passwordInput.addEventListener('keyup', (event) => {
            if (event.key === 'Enter') checkPassword();
        });
    }
});

// Função de login
function checkPassword() {
    const correctPassword = "ope1001";
    const enteredPassword = document.getElementById('password-input').value;

    if (enteredPassword === correctPassword) {
        document.getElementById('login-screen').style.display = 'none';
        document.getElementById('app').style.display = 'block';
        initializeApp();
    } else {
        alert('Senha incorreta.');
        document.getElementById('password-input').value = '';
    }
}

// Inicialização da aplicação
function initializeApp() {
    setupEventListeners();
    loadAlertsFromStorage();
    fetchAndDisplaySignals(activeTimeframe);
    
    // Atualização automática a cada 30 segundos
    setInterval(() => {
        fetchAndDisplaySignals(activeTimeframe);
    }, 30000);
}

// Configuração dos event listeners
function setupEventListeners() {
    // Timeframe buttons
    const timeframeButtons = document.querySelectorAll('.timeframe-selector button');
    timeframeButtons.forEach(button => {
        button.addEventListener('click', () => {
            timeframeButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            activeTimeframe = button.dataset.timeframe;
            
            // Ao mudar timeframe, limpar a tela e buscar novos dados
            currentSignalsOnScreen.clear();
            const container = document.getElementById('signals-container');
            container.innerHTML = '';
            
            fetchAndDisplaySignals(activeTimeframe);
        });
    });

    // Filter
    const signalFilter = document.getElementById('signal-filter');
    if (signalFilter) {
        signalFilter.addEventListener('change', applyFilters);
    }

    // Buttons
    const refreshBtn = document.getElementById('refresh-btn');
    const alertsBtn = document.getElementById('alerts-btn');
    
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => fetchAndDisplaySignals(activeTimeframe));
    }
    if (alertsBtn) {
        alertsBtn.addEventListener('click', openAlertsModal);
    }

    // Modal close buttons
    document.querySelectorAll('.close').forEach(closeBtn => {
        closeBtn.addEventListener('click', closeModals);
    });

    // Add alert button
    const addAlertBtn = document.getElementById('add-alert');
    if (addAlertBtn) {
        addAlertBtn.addEventListener('click', addAlert);
    }

    // Close modal when clicking outside
    window.addEventListener('click', (event) => {
        const modals = document.querySelectorAll('.modal');
        modals.forEach(modal => {
            if (event.target === modal) {
                modal.style.display = 'none';
            }
        });
    });
}

// Buscar e exibir sinais
async function fetchAndDisplaySignals(timeframe) {
    const apiUrl = `https://sinais-production.up.railway.app/signals?timeframe=${timeframe}`;
    const container = document.getElementById('signals-container');
    
    // Mostrar loading apenas se não há nada na tela
    if (currentSignalsOnScreen.size === 0) {
        container.innerHTML = `
            <div class="loading">
                <i class="fas fa-spinner"></i>
                Analisando timeframe ${timeframe}...
            </div>
        `;
    }

    try {
        const response = await fetch(apiUrl);
        if (!response.ok) {
            throw new Error(`Erro na API: ${response.statusText}`);
        }
        
        const signals = await response.json();
        allSignals = signals;
        
        // NOVA LÓGICA: Atualização suave sem piscar
        updateSignalsSmooth(signals);
        updateLastUpdatedTimestamp();
        checkAlerts(signals);
        
    } catch (error) {
        console.error("Erro ao buscar sinais:", error);
        container.innerHTML = `
            <div class="error">
                <i class="fas fa-exclamation-triangle"></i>
                Erro ao carregar sinais: ${error.message}
            </div>
        `;
    }
}

// NOVA FUNÇÃO: Atualização suave dos sinais (sem piscar)
function updateSignalsSmooth(newSignals) {
    const container = document.getElementById('signals-container');
    
    // Se não há sinais, limpar tudo
    if (newSignals.length === 0) {
        container.innerHTML = `
            <div class="error">
                <i class="fas fa-info-circle"></i>
                Nenhum sinal encontrado para este timeframe.
            </div>
        `;
        currentSignalsOnScreen.clear();
        return;
    }

    // Remover loading se existir
    const loadingElement = container.querySelector('.loading');
    if (loadingElement) {
        loadingElement.remove();
    }

    // Criar um mapa dos novos sinais para facilitar a comparação
    const newSignalsMap = new Map();
    newSignals.forEach(signal => {
        if (signal.signal !== 'ERROR') {
            newSignalsMap.set(signal.pair, signal);
        }
    });

    // 1. Atualizar sinais existentes ou criar novos
    newSignalsMap.forEach((newSignal, pair) => {
        const existingCard = document.querySelector(`[data-pair="${pair}"]`);
        
        if (existingCard) {
            // Sinal já existe na tela - verificar se precisa atualizar
            const oldSignal = currentSignalsOnScreen.get(pair);
            if (hasSignalChanged(oldSignal, newSignal)) {
                updateExistingCard(existingCard, newSignal);
            }
        } else {
            // Sinal novo - criar card
            const newCard = createSignalCard(newSignal);
            container.appendChild(newCard);
        }
        
        // Atualizar o mapa de controle
        currentSignalsOnScreen.set(pair, newSignal);
    });

    // 2. Remover sinais que não existem mais
    currentSignalsOnScreen.forEach((oldSignal, pair) => {
        if (!newSignalsMap.has(pair)) {
            const cardToRemove = document.querySelector(`[data-pair="${pair}"]`);
            if (cardToRemove) {
                cardToRemove.remove();
            }
            currentSignalsOnScreen.delete(pair);
        }
    });
}

// Verificar se um sinal mudou (para decidir se precisa atualizar)
function hasSignalChanged(oldSignal, newSignal) {
    if (!oldSignal) return true;
    
    return (
        oldSignal.price !== newSignal.price ||
        oldSignal.signal !== newSignal.signal ||
        oldSignal.confidence !== newSignal.confidence ||
        JSON.stringify(oldSignal.indicators) !== JSON.stringify(newSignal.indicators)
    );
}

// Atualizar um card existente (sem recriar)
function updateExistingCard(cardElement, newSignal) {
    // Atualizar preço
    const priceElement = cardElement.querySelector('.coin-price');
    if (priceElement) {
        priceElement.textContent = `$${newSignal.price}`;
    }

    // Atualizar confiança
    const confidenceElement = cardElement.querySelector('.confidence-badge');
    if (confidenceElement) {
        const confidenceNum = parseInt(newSignal.confidence.split('/')[0]);
        let confidenceColor = '#666666';
        if (confidenceNum >= 7) confidenceColor = '#34c759';
        else if (confidenceNum >= 5) confidenceColor = '#ff9500';
        else confidenceColor = '#ff3b30';
        
        confidenceElement.textContent = newSignal.confidence;
        confidenceElement.style.color = confidenceColor;
        confidenceElement.style.borderColor = confidenceColor;
    }

    // Atualizar sinal (LÓGICA ROBUSTA)
    const signalDisplay = cardElement.querySelector('.signal-display');
    const signalText = cardElement.querySelector('.signal-text');
    const signalDescription = cardElement.querySelector('.signal-description');
    
    if (signalDisplay && signalText && signalDescription) {
        // LÓGICA ROBUSTA para determinar o tipo de sinal
        let signalType = newSignal.signal.toUpperCase();
        if (signalType.includes("BUY")) signalType = "BUY";
        else if (signalType.includes("SELL")) signalType = "SELL";
        else signalType = "HOLD";

        // Determinar ícone
        let signalIcon = 'fa-clock';
        if (signalType === 'BUY') signalIcon = 'fa-arrow-trend-up';
        else if (signalType === 'SELL') signalIcon = 'fa-arrow-trend-down';

        // Atualizar classes CSS
        signalDisplay.className = `signal-display signal-${signalType}`;
        signalText.className = `signal-text signal-${signalType}`;
        
        // Atualizar conteúdo
        signalText.innerHTML = `<i class="fas ${signalIcon}"></i> ${newSignal.signal}`;
        
        const confidenceNum = parseInt(newSignal.confidence.split('/')[0]);
        signalDescription.textContent = getSignalDescription(newSignal.signal, confidenceNum);
    }

    // Atualizar indicadores
    const indicators = cardElement.querySelectorAll('.indicator-value');
    if (indicators.length >= 4) {
        indicators[0].textContent = newSignal.indicators.rsi;
        indicators[1].textContent = newSignal.indicators.macd;
        indicators[2].textContent = newSignal.indicators.bollinger_upper;
        indicators[3].textContent = newSignal.indicators.bollinger_lower;
    }
}

// Criar card de sinal (VERSÃO MELHORADA)
function createSignalCard(signal) {
    const card = document.createElement('div');
    card.className = 'signal-card';
    card.setAttribute('data-pair', signal.pair); // Para identificação

    // LÓGICA ROBUSTA para determinar o tipo de sinal
    let signalType = signal.signal.toUpperCase();
    if (signalType.includes("BUY")) signalType = "BUY";
    else if (signalType.includes("SELL")) signalType = "SELL";
    else signalType = "HOLD";

    const iconUrl = coinIcons[signal.pair] || coinIcons['default'];
    
    // Determinar ícone do sinal
    let signalIcon = 'fa-clock';
    if (signalType === 'BUY') signalIcon = 'fa-arrow-trend-up';
    else if (signalType === 'SELL') signalIcon = 'fa-arrow-trend-down';

    // Determinar cor da confiança
    const confidenceNum = parseInt(signal.confidence.split('/')[0]);
    let confidenceColor = '#666666';
    if (confidenceNum >= 7) confidenceColor = '#34c759';
    else if (confidenceNum >= 5) confidenceColor = '#ff9500';
    else confidenceColor = '#ff3b30';

    card.innerHTML = `
        <div class="card-header">
            <div class="coin-info">
                <img src="${iconUrl}" alt="${signal.pair}" class="coin-icon">
                <div class="coin-details">
                    <h3>${signal.pair}</h3>
                    <div class="coin-price">$${signal.price}</div>
                </div>
            </div>
            <div class="confidence-badge" style="color: ${confidenceColor}; border-color: ${confidenceColor};">
                ${signal.confidence}
            </div>
        </div>

        <div class="signal-display signal-${signalType}">
            <div class="signal-text signal-${signalType}">
                <i class="fas ${signalIcon}"></i>
                ${signal.signal}
            </div>
            <div class="signal-description">
                ${getSignalDescription(signal.signal, confidenceNum)}
            </div>
        </div>

        <div class="indicators-section">
            <div class="indicators-grid">
                <div class="indicator-item">
                    <span class="indicator-label">RSI</span>
                    <span class="indicator-value">${signal.indicators.rsi}</span>
                </div>
                <div class="indicator-item">
                    <span class="indicator-label">MACD</span>
                    <span class="indicator-value">${signal.indicators.macd}</span>
                </div>
                <div class="indicator-item">
                    <span class="indicator-label">B. Sup</span>
                    <span class="indicator-value">${signal.indicators.bollinger_upper}</span>
                </div>
                <div class="indicator-item">
                    <span class="indicator-label">B. Inf</span>
                    <span class="indicator-value">${signal.indicators.bollinger_lower}</span>
                </div>
            </div>
        </div>
    `;

    return card;
}

// Obter descrição do sinal
function getSignalDescription(signalText, confidence) {
    if (signalText.includes('BUY')) {
        if (confidence >= 7) return 'Sinal forte de compra confirmado por múltiplos indicadores';
        return 'Tendência de alta detectada, aguardando confirmação';
    } else if (signalText.includes('SELL')) {
        if (confidence >= 7) return 'Sinal forte de venda confirmado por múltiplos indicadores';
        return 'Tendência de baixa detectada, aguardando confirmação';
    } else {
        return 'Mercado em consolidação, aguardando movimento definido';
    }
}

// Aplicar filtros
function applyFilters() {
    const signalFilter = document.getElementById('signal-filter').value;
    
    let filteredSignals = allSignals;
    
    if (signalFilter !== 'all') {
        filteredSignals = allSignals.filter(signal => 
            signal.signal.includes(signalFilter)
        );
    }
    
    // Ao aplicar filtros, usar a lógica normal de exibição
    displaySignals(filteredSignals);
}

// Exibir sinais na interface (versão original para filtros)
function displaySignals(signals) {
    const container = document.getElementById('signals-container');
    container.innerHTML = '';
    currentSignalsOnScreen.clear();

    if (signals.length === 0) {
        container.innerHTML = `
            <div class="error">
                <i class="fas fa-info-circle"></i>
                Nenhum sinal encontrado para este timeframe.
            </div>
        `;
        return;
    }

    signals.forEach(signal => {
        if (signal.signal === 'ERROR') return;

        const card = createSignalCard(signal);
        container.appendChild(card);
        currentSignalsOnScreen.set(signal.pair, signal);
    });
}

// Abrir modal de alertas
function openAlertsModal() {
    document.getElementById('alerts-modal').style.display = 'block';
    displayActiveAlerts();
}

// Fechar modais
function closeModals() {
    document.querySelectorAll('.modal').forEach(modal => {
        modal.style.display = 'none';
    });
}

// Adicionar alerta
function addAlert() {
    const pair = document.getElementById('alert-pair').value;
    const price = parseFloat(document.getElementById('alert-price').value);
    const condition = document.getElementById('alert-condition').value;

    if (!pair || !price) {
        alert('Por favor, preencha todos os campos.');
        return;
    }

    const alert = {
        id: Date.now(),
        pair: pair,
        price: price,
        condition: condition,
        created: new Date().toLocaleString()
    };

    activeAlerts.push(alert);
    saveAlertsToStorage();
    displayActiveAlerts();

    // Limpar formulário
    document.getElementById('alert-pair').value = '';
    document.getElementById('alert-price').value = '';
}

// Remover alerta
function removeAlert(alertId) {
    activeAlerts = activeAlerts.filter(alert => alert.id !== alertId);
    saveAlertsToStorage();
    displayActiveAlerts();
}

// Exibir alertas ativos
function displayActiveAlerts() {
    const container = document.getElementById('alerts-list');
    
    if (activeAlerts.length === 0) {
        container.innerHTML = '<p style="color: #666; text-align: center;">Nenhum alerta ativo</p>';
        return;
    }

    container.innerHTML = activeAlerts.map(alert => `
        <div class="alert-item">
            <div>
                <strong>${alert.pair}</strong> ${alert.condition === 'above' ? 'acima de' : 'abaixo de'} $${alert.price}
                <br><small style="color: #666;">Criado em: ${alert.created}</small>
            </div>
            <button onclick="removeAlert(${alert.id})">Remover</button>
        </div>
    `).join('');
}

// Verificar alertas
function checkAlerts(signals) {
    activeAlerts.forEach(alert => {
        const signal = signals.find(s => s.pair === alert.pair);
        if (!signal) return;

        const currentPrice = signal.price;
        let triggered = false;

        if (alert.condition === 'above' && currentPrice >= alert.price) {
            triggered = true;
        } else if (alert.condition === 'below' && currentPrice <= alert.price) {
            triggered = true;
        }

        if (triggered) {
            showNotification(`Alerta: ${alert.pair} ${alert.condition === 'above' ? 'acima de' : 'abaixo de'} $${alert.price}!`);
            removeAlert(alert.id);
        }
    });
}

// Mostrar notificação
function showNotification(message) {
    // Verificar se o navegador suporta notificações
    if ('Notification' in window) {
        if (Notification.permission === 'granted') {
            new Notification('Sinais Pro', { body: message });
        } else if (Notification.permission !== 'denied') {
            Notification.requestPermission().then(permission => {
                if (permission === 'granted') {
                    new Notification('Sinais Pro', { body: message });
                }
            });
        }
    }
    
    // Fallback: alert
    alert(message);
}

// Salvar alertas no localStorage
function saveAlertsToStorage() {
    localStorage.setItem('sinaisProAlerts', JSON.stringify(activeAlerts));
}

// Carregar alertas do localStorage
function loadAlertsFromStorage() {
    const stored = localStorage.getItem('sinaisProAlerts');
    if (stored) {
        activeAlerts = JSON.parse(stored);
    }
}

// Atualizar timestamp da última atualização
function updateLastUpdatedTimestamp() {
    const timestamp = document.getElementById('last-updated-timestamp');
    if (timestamp) {
        const now = new Date();
        timestamp.textContent = `Última atualização: ${now.toLocaleTimeString()}`;
    }
}

// Solicitar permissão para notificações quando a app inicializa
function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
}

