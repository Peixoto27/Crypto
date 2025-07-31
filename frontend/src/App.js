import React, { useState, useEffect } from 'react';
import ReactDOM from 'react-dom';
import './App.css';

// URL da nossa API na Railway
const API_URL = 'https://reliable-mercy-production.up.railway.app';

// --- COMPONENTE MODAL (POPUP ) ---
const Modal = ({ isOpen, onClose, title, children }) => {
  if (!isOpen) return null;

  return ReactDOM.createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">{title}</h2>
          <button className="close-button" onClick={onClose}>&times;</button>
        </div>
        {children}
      </div>
    </div>,
    document.getElementById('modal-root')
  );
};

// --- COMPONENTE PRINCIPAL DA APLICAÇÃO ---
function App() {
  const [signals, setSignals] = useState([]);
  const [history, setHistory] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedPair, setSelectedPair] = useState(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        const [signalsResponse, historyResponse] = await Promise.all([
          fetch(`${API_URL}/signals`),
          fetch(`${API_URL}/signals/history`)
        ]);

        if (!signalsResponse.ok) throw new Error(`Erro ao buscar sinais: ${signalsResponse.statusText}`);
        if (!historyResponse.ok) throw new Error(`Erro ao buscar histórico: ${historyResponse.statusText}`);

        const signalsData = await signalsResponse.json();
        const historyData = await historyResponse.json();

        setSignals(signalsData.signals || []);
        setHistory(historyData || {});
        setError(null);
      } catch (err) {
        setError(err.message);
        setSignals([]);
        setHistory({});
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const openHistoryModal = (pair) => {
    setSelectedPair(pair);
    setIsModalOpen(true);
  };

  const closeHistoryModal = () => {
    setIsModalOpen(false);
    setSelectedPair(null);
  };

  const getSignalClass = (signal) => {
    if (signal.toLowerCase().includes('buy')) return 'buy';
    if (signal.toLowerCase().includes('sell')) return 'sell';
    if (signal.toLowerCase().includes('alerta')) return 'alert'; // ✅ NOVO: Classe para Alertas
    if (signal.toLowerCase().includes('hold')) return 'hold';
    return 'error';
  };

  if (loading) {
    return <div className="status-container"><h1>Carregando Sinais...</h1></div>;
  }

  if (error) {
    return <div className="status-container"><div className="error-message"><h2>Erro de Conexão</h2><p>{error}</p></div></div>;
  }

  return (
    <div className="App">
      <header className="app-header">
        <h1>Crypton Signals</h1>
        <p>Análise técnica para os principais pares de criptomoedas.</p>
      </header>

      <main>
        <div className="signals-grid">
          {signals.map((signal, index) => (
            <div key={index} className="signal-card">
              <h3 className="pair-name">{signal.pair}</h3>
              
              <div className={`signal-display ${getSignalClass(signal.signal)}`}>
                {signal.signal}
              </div>

              <div className="signal-details">
                <p><strong>Entrada:</strong> <span>${signal.entry?.toFixed(4)}</span></p>
                <p><strong>Alvo:</strong> <span>${signal.target?.toFixed(4)}</span></p>
                <p><strong>Stop:</strong> <span>${signal.stop?.toFixed(4)}</span></p>
                <p><strong>RSI:</strong> <span>{signal.rsi?.toFixed(2)}</span></p>
                {/* ✅ NOVO: Exibe os dados das Bandas de Bollinger se existirem */}
                {signal.bb_upper > 0 && (
                  <p className="bollinger-bands">
                    <strong>BB:</strong> 
                    <span>${signal.bb_lower?.toFixed(4)} - ${signal.bb_upper?.toFixed(4)}</span>
                  </p>
                )}
              </div>

              <button className="history-button" onClick={() => openHistoryModal(signal.pair)}>
                Ver Histórico
              </button>
            </div>
          ))}
        </div>
      </main>

      <Modal isOpen={isModalOpen} onClose={closeHistoryModal} title={`Histórico para ${selectedPair}`}>
        <table className="history-table">
          <thead>
            <tr>
              <th>Data</th>
              <th>Sinal</th>
              <th>Entrada</th>
              <th>Alvo</th>
              <th>Stop</th>
            </tr>
          </thead>
          <tbody>
            {(history[selectedPair] || []).map((record) => (
              <tr key={record.id} className={`signal-row ${getSignalClass(record.signal)}-row`}>
                <td>{record.timestamp}</td>
                <td>
                  <span className={`signal-cell ${getSignalClass(record.signal)}`}>
                    {record.signal}
                  </span>
                </td>
                <td>${record.entry.toFixed(4)}</td>
                <td>${record.target.toFixed(4)}</td>
                <td>${record.stop.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {(!history[selectedPair] || history[selectedPair].length === 0) && (
          <p style={{ textAlign: 'center', marginTop: '20px' }}>Nenhum histórico encontrado para este par.</p>
        )}
      </Modal>
    </div>
  );
}

export default App;
