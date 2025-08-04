import React, { useState, useEffect } from 'react';
import Chart from 'react-apexcharts';
import './App.css';

const SignalCard = ({ signal, onHistoryClick }) => {
  const signalType = signal.signal.split(' ')[0];
  const isError = signalType === 'ERROR';
  const isSqueeze = signalType === 'ALERTA';

  return (
    <div className={`signal-card ${isError ? 'error' : ''} ${isSqueeze ? 'squeeze-alert' : ''}`}>
      <h2>{signal.pair}</h2>
      <div className={`signal-display ${signalType.toLowerCase()}`}>
        {isError ? 'ERRO' : signal.signal}
      </div>
      <div className="signal-details">
        <p><strong>Entrada:</strong> $ {isError ? '' : signal.entry}</p>
        <p><strong>Alvo:</strong> $ {isError ? '' : signal.target}</p>
        <p><strong>Stop:</strong> $ {isError ? '' : signal.stop}</p>
        <p><strong>RSI:</strong> {isError ? '' : signal.rsi}</p>
        <p><strong>BB:</strong> {isError ? '' : `$${signal.bb_lower} - $${signal.bb_upper}`}</p>
      </div>
      <button onClick={() => onHistoryClick(signal.pair)} className="history-button">
        Ver Histórico
      </button>
    </div>
  );
};

const Modal = ({ isOpen, onClose, children }) => {
  if (!isOpen) return null;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close-button" onClick={onClose}>×</button>
        {children}
      </div>
    </div>
  );
};

const HistoryChart = ({ chartData }) => {
  if (!chartData || !chartData.prices || chartData.prices.length === 0) {
    return <p>Não foi possível carregar o gráfico.</p>;
  }

  const options = {
    chart: {
      type: 'line',
      height: 350,
      foreColor: '#e0e0e0'
    },
    stroke: {
      curve: 'smooth',
      width: 2
    },
    xaxis: {
      type: 'datetime',
      labels: {
        style: {
          colors: '#e0e0e0'
        }
      }
    },
    yaxis: {
      labels: {
        formatter: (value) => `$${value.toFixed(2)}`,
        style: {
          colors: '#e0e0e0'
        }
      }
    },
    tooltip: {
      theme: 'dark',
      x: {
        format: 'dd MMM yyyy'
      }
    },
    grid: {
      borderColor: '#555'
    },
    annotations: {
      points: chartData.markers.map(marker => ({
        x: marker.timestamp,
        y: marker.price,
        marker: {
          size: 6,
          fillColor: marker.type === 'BUY' ? '#2ecc71' : '#e74c3c',
          strokeColor: '#ffffff',
          strokeWidth: 2,
          shape: 'circle',
          radius: 2,
        },
        label: {
          borderColor: marker.type === 'BUY' ? '#2ecc71' : '#e74c3c',
          offsetY: 0,
          style: {
            color: '#fff',
            background: marker.type === 'BUY' ? '#2ecc71' : '#e74c3c',
          },
          text: marker.text,
        }
      }))
    }
  };

  const series = [{
    name: 'Preço',
    data: chartData.prices
  }];

  return (
    <div className="chart-container">
      <h4>Histórico de Preço com Sinais</h4>
      <Chart options={options} series={series} type="line" height={350} />
    </div>
  );
};

function App() {
  const [signals, setSignals] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalContent, setModalContent] = useState({ pair: '', history: [], chartData: null });

  const API_URL = 'https://kind-perfection-production-6085.up.railway.app';

  useEffect(() => {
    const fetchSignals = async () => {
      try {
        setLoading(true);
        const response = await fetch(`${API_URL}/signals`);
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        setSignals(data.signals || []);
      } catch (e) {
        setError(`Erro de Conexão: ${e.message}`);
      } finally {
        setLoading(false);
      }
    };
    fetchSignals();
  }, [API_URL]);

  const handleHistoryClick = async (pair) => {
    try {
      const historyResponse = await fetch(`${API_URL}/signals/history`);
      if (!historyResponse.ok) throw new Error('Falha ao buscar histórico da tabela.');
      const historyData = await historyResponse.json();
      const pairHistoryAll = historyData[pair] || [];

      const filteredHistory = pairHistoryAll.filter(item => 
        item.signal.toUpperCase().includes('BUY') || item.signal.toUpperCase().includes('SELL')
      );

      const chartResponse = await fetch(`${API_URL}/history/chart_data?pair=${pair}`);
      if (!chartResponse.ok) throw new Error('Falha ao buscar dados do gráfico.');
      const chartData = await chartResponse.json();

      setModalContent({ pair, history: filteredHistory, chartData });
      setIsModalOpen(true);
    } catch (e) {
      console.error("Erro ao buscar histórico:", e);
      setModalContent(prevState => ({ ...prevState, pair, history: [], chartData: null }));
      setIsModalOpen(true);
    }
  };

  if (loading) {
    return <div className="loading-container"><h1>Carregando Sinais...</h1></div>;
  }

  if (error) {
    return <div className="error-container"><h1>{error}</h1></div>;
  }

  return (
    <div className="App">
      <header className="App-header">
        <h1>Crypton Signals</h1>
        <p>Análise técnica para os principais pares de criptomoedas.</p>
      </header>
      <main className="signals-grid">
        {signals.map((signal, index) => (
          <SignalCard key={index} signal={signal} onHistoryClick={handleHistoryClick} />
        ))}
      </main>
      <Modal isOpen={isModalOpen} onClose={() => setIsModalOpen(false)}>
        <h2>Histórico para {modalContent.pair}</h2>
        <HistoryChart chartData={modalContent.chartData} />
        <div className="history-table-container">
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
              {modalContent.history.length > 0 ? (
                modalContent.history.map((item, index) => (
                  <tr key={index}>
                    <td>{item.timestamp}</td>
                    <td>{item.signal}</td>
                    <td>$ {item.entry.toFixed(4)}</td>
                    <td>$ {item.target.toFixed(4)}</td>
                    <td>$ {item.stop.toFixed(4)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="5">Nenhum sinal de Compra ou Venda no histórico recente.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Modal>
    </div>
  );
}

export default App;
