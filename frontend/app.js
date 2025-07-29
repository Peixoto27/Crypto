// Configurações
const CORRECT_PASSWORD = 'Zoe1001';
const API_URL = 'https://crypto-production-8861.up.railway.app/signals';

// Estado da aplicação
let currentTimeframe = '1d';
let alertsData = JSON.parse(localStorage.getItem('cryptoAlerts')) || [];
let signalsData = [];
let signalsHistory = JSON.parse(localStorage.getItem('signalsHistory')) || [];

// Elementos DOM
const loginScreen = document.getElementById('login-screen');
const app = document.getElementById('app');
const passwordInput = document.getElementById('password-input');
const loginButton = document.getElementById('login-button');
const signalsContainer = document.getElementById('signals-container');
const lastUpdatedElement = document.getElementById('last-updated-timestamp');

// Inicialização
document.addEventListener('DOMContentLoaded', function() {
    setupEventListeners();
    checkLogin();
});

function setupEventListeners() {
    // Login
    loginButton.addEventListener('click', handleLogin);
    passwordInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            handleLogin();
        }
    });

    // Timeframe selector
    document.querySelectorAll('.timeframe-selector button').forEach(button => {
        button.addEventListener('click', function() {
            document.querySelector('.timeframe-selector .active').classList.remove('active');
            this.classList.add('active');
            currentTimeframe = this.dataset.timeframe;
            loadSignals();
        });
    });

    // Alertas
    document.getElementById('alerts-btn').addEventListener('click', openAlertsModal);
    document.querySelector('.close').addEventListener('click', closeAlertsModal);
    document.getElementById('add-alert').addEventListener('click', addAlert);
}

function checkLogin() {
    const isLoggedIn = sessionStorage.getItem('isLoggedIn');
    if (isLoggedIn === 'true') {
        showApp();
    }
}

function handleLogin() {
    const password = passwordInput.value;
    if (password === CORRECT_PASSWORD) {
        sessionStorage.setItem('isLoggedIn', 'true');
        showApp();
    } else {
        alert('Senha incorreta!');
        passwordInput.value = '';
    }
}

function showApp() {
    loginScreen.style.display = 'none';
    app.style.display = 'block';
    loadSignals();
    setInterval(loadSignals, 30000); // Atualizar a cada 30 segundos
}

async function loadSignals() {
    try {
        showLoading();
        
        console.log('Carregando sinais da API:', API_URL);
        
        const response = await fetch(API_URL, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        console.log('Dados recebidos da API:', data);
        
        // Verificar se há erro na resposta
        if (data.error) {
            throw new Error(data.error);
        }
        
        // Processar os dados recebidos
        signalsData = processApiData(data);
        
        // Salvar no histórico
        saveToHistory(signalsData);
        
        renderSignals(signalsData);
        updateLastUpdated();
        
        console.log('Sinais processados:', signalsData);
        
    } catch (error) {
        console.error('Erro ao carregar sinais:', error);
        showError(`Erro ao carregar dados: ${error.message}`);
        
        // Tentar carregar dados do histórico como fallback
        if (signalsHistory.length > 0) {
            const lastHistoryEntry = signalsHistory[signalsHistory.length - 1];
            signalsData = lastHistoryEntry.signals;
            renderSignals(signalsData);
            updateLastUpdated(lastHistoryEntry.timestamp);
            showWarning('Exibindo dados do histórico (API indisponível)');
        }
    }
}

function processApiData(data) {
    // Processar dados da API da Binance
    return data.map(signal => {
        // Extrair RSI do campo signal se disponível
        let rsi = 50; // valor padrão
        const rsiMatch = signal.signal.match(/RSI:\s*([0-9.]+)/);
        if (rsiMatch) {
            rsi = parseFloat(rsiMatch[1]);
        }
        
        // Determinar o tipo de sinal
        let signalType = 'HOLD';
        if (signal.signal.includes('BUY')) {
            signalType = 'BUY';
        } else if (signal.signal.includes('SELL')) {
            signalType = 'SELL';
        }
        
        // Calcular métricas
        const entry = signal.entry || 0;
        const stop = signal.stop || entry * 0.98;
        const target = signal.target || entry * 1.03;
        
        const potential = entry > 0 ? ((target - entry) / entry * 100) : 0;
        const risk = entry > 0 ? ((entry - stop) / entry * 100) : 0;
        const rr = risk > 0 ? (potential / risk) : 0;
        
        return {
            pair: signal.pair || 'N/A',
            signal: signalType,
            entry: entry,
            stop: stop,
            target: target,
            rsi: rsi,
            potential: Math.round(potential * 100) / 100,
            rr: Math.round(rr * 100) / 100,
            timestamp: new Date().toISOString(),
            originalSignal: signal.signal // Manter o sinal original para referência
        };
    });
}

function saveToHistory(signals) {
    const historyEntry = {
        timestamp: new Date().toISOString(),
        signals: signals
    };
    
    signalsHistory.push(historyEntry);
    
    // Manter apenas os últimos 50 registros
    if (signalsHistory.length > 50) {
        signalsHistory = signalsHistory.slice(-50);
    }
    
    localStorage.setItem('signalsHistory', JSON.stringify(signalsHistory));
}

function renderSignals(signals) {
    signalsContainer.innerHTML = '';
    
    if (!signals || signals.length === 0) {
        signalsContainer.innerHTML = '<div class="no-data">Nenhum sinal disponível</div>';
        return;
    }
    
    signals.forEach(signal => {
        const card = createSignalCard(signal);
        signalsContainer.appendChild(card);
    });
}

function createSignalCard(signal) {
    const card = document.createElement('div');
    card.className = `signal-card ${signal.signal.toLowerCase()}`;
    
    const signalText = getSignalText(signal.signal);
    
    card.innerHTML = `
        <div class="card-header">
            <h3>${signal.pair}</h3>
            <span class="signal-badge ${signal.signal.toLowerCase()}">${signalText}</span>
        </div>
        
        <div class="card-body">
            <div class="price-info">
                <div class="price-row">
                    <span class="label">Entrada:</span>
                    <span class="value">${formatPrice(signal.entry)}</span>
                </div>
                <div class="price-row">
                    <span class="label">Stop:</span>
                    <span class="value">${formatPrice(signal.stop)}</span>
                </div>
                <div class="price-row">
                    <span class="label">Target:</span>
                    <span class="value">${formatPrice(signal.target)}</span>
                </div>
            </div>
            
            <div class="metrics">
                <div class="metric">
                    <span class="metric-label">Potencial:</span>
                    <span class="metric-value">${signal.potential}%</span>
                </div>
                <div class="metric">
                    <span class="metric-label">R/R:</span>
                    <span class="metric-value">${signal.rr}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">RSI:</span>
                    <span class="metric-value">${signal.rsi.toFixed(1)}</span>
                </div>
            </div>
            
            ${signal.originalSignal ? `
            <div class="signal-details">
                <small class="signal-description">${signal.originalSignal}</small>
            </div>
            ` : ''}
        </div>
        
        <div class="card-footer">
            <button class="alert-btn" onclick="quickAlert('${signal.pair}', ${signal.entry})">
                <i class="fas fa-bell"></i> Alerta Rápido
            </button>
        </div>
    `;
    
    return card;
}

function getSignalText(signal) {
    switch(signal.toUpperCase()) {
        case 'BUY': return 'COMPRA';
        case 'SELL': return 'VENDA';
        case 'HOLD': return 'MANTER';
        default: return signal;
    }
}

function formatPrice(price) {
    if (!price || price === 0) return 'N/A';
    
    if (price >= 1000) {
        return price.toLocaleString('pt-BR', { 
            minimumFractionDigits: 2, 
            maximumFractionDigits: 2 
        });
    } else if (price >= 1) {
        return price.toLocaleString('pt-BR', { 
            minimumFractionDigits: 4, 
            maximumFractionDigits: 4 
        });
    } else {
        return price.toLocaleString('pt-BR', { 
            minimumFractionDigits: 6, 
            maximumFractionDigits: 6 
        });
    }
}

function updateLastUpdated(timestamp = null) {
    const time = timestamp ? new Date(timestamp) : new Date();
    const timeString = time.toLocaleString('pt-BR');
    lastUpdatedElement.textContent = `Última atualização: ${timeString}`;
}

function showLoading() {
    signalsContainer.innerHTML = '<div class="loading">🔄 Carregando sinais da Binance...</div>';
}

function showError(message) {
    signalsContainer.innerHTML = `<div class="error">❌ ${message}</div>`;
}

function showWarning(message) {
    const warningDiv = document.createElement('div');
    warningDiv.className = 'warning';
    warningDiv.innerHTML = `⚠️ ${message}`;
    signalsContainer.insertBefore(warningDiv, signalsContainer.firstChild);
    
    // Remover aviso após 5 segundos
    setTimeout(() => {
        if (warningDiv.parentNode) {
            warningDiv.parentNode.removeChild(warningDiv);
        }
    }, 5000);
}

// Funções de Alertas (mantidas iguais)
function openAlertsModal() {
    document.getElementById('alerts-modal').style.display = 'block';
    renderAlerts();
}

function closeAlertsModal() {
    document.getElementById('alerts-modal').style.display = 'none';
}

function addAlert() {
    const pair = document.getElementById('alert-pair').value;
    const condition = document.getElementById('alert-condition').value;
    const price = parseFloat(document.getElementById('alert-price').value);
    
    if (!pair || !price) {
        alert('Preencha todos os campos!');
        return;
    }
    
    const alert = {
        id: Date.now(),
        pair: pair.toUpperCase(),
        condition,
        price,
        created: new Date().toISOString()
    };
    
    alertsData.push(alert);
    localStorage.setItem('cryptoAlerts', JSON.stringify(alertsData));
    
    // Limpar formulário
    document.getElementById('alert-pair').value = '';
    document.getElementById('alert-price').value = '';
    
    renderAlerts();
}

function quickAlert(pair, currentPrice) {
    const price = prompt(`Criar alerta para ${pair}.\nPreço atual: ${formatPrice(currentPrice)}\n\nDigite o preço do alerta:`);
    
    if (price && !isNaN(price)) {
        const alert = {
            id: Date.now(),
            pair: pair,
            condition: parseFloat(price) > currentPrice ? 'above' : 'below',
            price: parseFloat(price),
            created: new Date().toISOString()
        };
        
        alertsData.push(alert);
        localStorage.setItem('cryptoAlerts', JSON.stringify(alertsData));
        
        alert('Alerta criado com sucesso!');
    }
}

function renderAlerts() {
    const alertsList = document.getElementById('alerts-list');
    
    if (alertsData.length === 0) {
        alertsList.innerHTML = '<p>Nenhum alerta configurado.</p>';
        return;
    }
    
    alertsList.innerHTML = alertsData.map(alert => `
        <div class="alert-item">
            <div class="alert-info">
                <strong>${alert.pair}</strong>
                <span>${alert.condition === 'above' ? 'Acima de' : 'Abaixo de'} ${formatPrice(alert.price)}</span>
            </div>
            <button onclick="removeAlert(${alert.id})" class="remove-btn">
                <i class="fas fa-trash"></i>
            </button>
        </div>
    `).join('');
}

function removeAlert(id) {
    alertsData = alertsData.filter(alert => alert.id !== id);
    localStorage.setItem('cryptoAlerts', JSON.stringify(alertsData));
    renderAlerts();
}

// Fechar modal clicando fora
window.onclick = function(event) {
    const modal = document.getElementById('alerts-modal');
    if (event.target === modal) {
        closeAlertsModal();
    }
}

// Função para visualizar histórico
function showHistory() {
    if (signalsHistory.length === 0) {
        alert('Nenhum histórico disponível');
        return;
    }
    
    const historyWindow = window.open('', '_blank', 'width=800,height=600');
    const historyHtml = `
        <html>
        <head>
            <title>Histórico de Sinais</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; }
                .history-entry { margin-bottom: 20px; padding: 10px; border: 1px solid #ccc; }
                .timestamp { font-weight: bold; color: #666; }
                .signals { margin-top: 10px; }
                .signal { margin: 5px 0; padding: 5px; background: #f5f5f5; }
            </style>
        </head>
        <body>
            <h1>Histórico de Sinais</h1>
            ${signalsHistory.slice(-10).reverse().map(entry => `
                <div class="history-entry">
                    <div class="timestamp">${new Date(entry.timestamp).toLocaleString('pt-BR')}</div>
                    <div class="signals">
                        ${entry.signals.map(signal => `
                            <div class="signal">
                                <strong>${signal.pair}</strong> - ${signal.signal} - 
                                Entrada: ${formatPrice(signal.entry)} - 
                                RSI: ${signal.rsi.toFixed(1)}
                            </div>
                        `).join('')}
                    </div>
                </div>
            `).join('')}
        </body>
        </html>
    `;
    
    historyWindow.document.write(historyHtml);
    historyWindow.document.close();
}

