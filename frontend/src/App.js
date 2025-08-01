import React, { useState, useEffect } from 'react';
import './App.css';
import HistoryChart from './HistoryChart'; // ✅ Importa o novo componente

const API_BASE_URL = "https://reliable-mercy-production.up.railway.app";

function Modal({ children, onClose } ) {
  return (
    <div className="modal-backdrop">
      <div className="modal-content">
        <button onClick={onClose} className="modal-close-button">&times;</button>
        {children}
      </div>
    </div>
  );
}

function App() {
  const [signals, setSignals] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [historyData, setHistoryData] = useState([]);
  const [chartData, setChartData] = useState(null); // ✅ Estado para os dados do gráfico
  const [selectedPair, setSelectedPair] = useState(null);
  const [loadingHistory, setLoadingHistory] = useState(false);

  useEffect(() => {
    const fetchSignals = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/signals`);
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
  }, []);

  const handleOpenHistory = async (pair) => {
    setSelectedPair(pair);
    setIsModalOpen(true);
    setLoadingHistory(true);
    setHistoryData([]);
    setChartData(null); // ✅ Limpa os dados do gráfico anterior

    try {
      // Busca o histórico em tabela
      const historyResponse = await fetch(`${API_BASE_URL}/signals/history`);
      if (!historyResponse.ok) throw new Error('Falha ao buscar histórico em tabela');
      const allHistory = await historyResponse.json();
      setHistoryData(allHistory[pair] || []);

      // ✅ Busca os dados para o gráfico
      const chartResponse = await fetch(`${API_BASE_URL}/history/chart_data?pair=${pair}`);
      if (!chartResponse.ok) throw new Error('Falha ao buscar dados do gráfico');
      const chartJson = await chartResponse.json();
      setChartData(chartJson);

    } catch (e) {
      console.error("Erro ao buscar histórico:", e);
      setError(`Erro ao buscar histórico: ${e.message}`);
    } finally {
      setLoadingHistory(false);
    }
  };

  if (loading) {
    return <div className="loading-container"><h1>Carregando Sinais...</h1></div>;
  }

  if (error && signals.length === 0) {
    return <div className="error-container"><h2>{error}</h2></div>;
  }

  return (
    <div className="App">
      <header className="App-header">
        <h1>Crypton Signals</h1>
        <p>Análise técnica para os principais pares de criptomoedas.</p>
      </header>
      <main className="signals-grid">
        {signals.map((signal, index) => (
          <div key={index} className="signal-card">
            <h2>{signal.pair}</h2>
            <div className={`signal-box ${signal.signal.includes('BUY') ? 'buy' : signal.signal.includes('SELL') ? 'sell' : signal.signal.includes('ALERTA') ? 'squeeze' : ''}`}>
              {signal.signal}
            </div>
            {signal.signal !== 'ERROR' ? (
              <>
                <p><strong>Entrada:</strong> $ {signal.entry}</p>
                <p><strong>Alvo:</strong> $ {signal.target}</p>
                <p><strong>Stop:</strong> $ {signal.stop}</p>
                <p><strong>RSI:</strong> {signal.rsi}</p>
                <p><strong>BB:</strong> ${signal.bb_lower} - ${signal.bb_upper}</p>
              </>
            ) : (
              <div className="error-message-small">
                <p>Não foi possível gerar o sinal.</p>
                <p>Tente novamente mais tarde.</p>
              </div>
            )}
            <button className="history-button" onClick={() => handleOpenHistory(signal.pair)}>
              Ver Histórico
            </button>
          </div>
        ))}
      </main>

      {isModalOpen && (
        <Modal onClose={() => setIsModalOpen(false)}>
          <h2>Histórico para {selectedPair}</h2>
          {loadingHistory ? (
            <p>Carregando histórico...</p>
          ) : (
            <>
              {/* ✅ Renderiza o gráfico se os dados existirem */}
              {chartData && chartData.prices ? (
                <HistoryChart data={chartData} />
              ) : (
                <p>Não foi possível carregar o gráfico.</p>
              )}

              {historyData.length > 0 ? (
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
                    {historyData.map((item, index) => (
                      <tr key={index}>
                        <td>{item.timestamp}</td>
                        <td>{item.signal}</td>
                        <td>$ {item.entry.toFixed(4)}</td>
                        <td>$ {item.target.toFixed(4)}</td>
                        <td>$ {item.stop.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p>Nenhum registro de histórico para este par.</p>
              )}
            </>
          )}
        </Modal>
      )}
    </div>
  );
}

export default App;
