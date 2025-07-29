import React, { useState, useEffect } from 'react';
import './App.css'; // Importa os nossos estilos

function App() {
  // Estado para guardar a lista de sinais recebida da API
  const [signals, setSignals] = useState([]);
  
  // Estado para controlar a exibição da mensagem "A carregar..."
  const [loading, setLoading] = useState(true);
  
  // Estado para guardar qualquer mensagem de erro que ocorra
  const [error, setError] = useState(null);

  // URL COMPLETO da sua API no backend (Railway)
  const API_URL = "https://crypto-production-8861.up.railway.app/signals";

  // useEffect é um hook do React que executa o código depois do componente ser renderizado.
  // O array vazio `[]` no final garante que ele só execute UMA VEZ, quando o componente carrega.
  useEffect(( ) => {
    const fetchSignals = () => {
      console.log("Buscando sinais da API...");
      setLoading(true); // Inicia o carregamento
      setError(null);   // Limpa erros anteriores

      fetch(API_URL)
        .then(response => {
          // Se a resposta da rede não for 'OK' (ex: erro 404 ou 500), lançamos um erro.
          if (!response.ok) {
            throw new Error(`Erro de rede: ${response.status} - ${response.statusText}`);
          }
          return response.json(); // Converte a resposta para JSON
        })
        .then(data => {
          console.log("Dados recebidos:", data);
          // ✅ A VERIFICAÇÃO MAIS IMPORTANTE!
          // Verificamos se a resposta (`data`) existe e se DENTRO dela existe
          // uma propriedade chamada `signals` que seja um array.
          if (data && Array.isArray(data.signals)) {
            setSignals(data.signals); // Sucesso! Guardamos o array de sinais no estado.
          } else {
            // Se a resposta não tiver o formato esperado, informamos um erro.
            throw new Error("A resposta da API não tem o formato esperado.");
          }
        })
        .catch(err => {
          // Captura qualquer erro que tenha acontecido no processo
          console.error("Falha ao buscar sinais:", err);
          setError(err.message); // Guarda a mensagem de erro para ser exibida ao utilizador
        })
        .finally(() => {
          // Este bloco executa sempre, seja em caso de sucesso ou erro.
          setLoading(false); // Para de mostrar a mensagem "A carregar..."
        });
    };

    fetchSignals(); // Chama a função para iniciar a busca de dados.
  }, []);

  // --- Lógica de Renderização ---

  // Se estiver a carregar, mostra uma mensagem simples.
  if (loading) {
    return (
      <div className="app-container status-container">
        <h1>A Carregar Sinais...</h1>
      </div>
    );
  }

  // Se ocorreu um erro, mostra a mensagem de erro.
  if (error) {
    return (
      <div className="app-container status-container">
        <h1>Ocorreu um Erro</h1>
        <p className="error-message">{error}</p>
        <p>Por favor, tente recarregar a página.</p>
      </div>
    );
  }

  // Se tudo correu bem, mostra os sinais.
  return (
    <div className="app-container">
      <header className="app-header">
        <h1>Sinais de Criptomoedas</h1>
        <p>Análise técnica diária com base em SMA, RSI e Volume.</p>
      </header>
      
      <main className="signals-grid">
        {signals.length > 0 ? (
          signals.map((signal, index) => (
            <div key={index} className="signal-card">
              <h2 className="pair-name">{signal.pair}</h2>
              
              {signal.signal === "ERROR" ? (
                <div className="signal-display error">
                  <p>Erro ao processar</p>
                  <span className="error-details">{signal.error_message}</span>
                </div>
              ) : (
                <>
                  <div className={`signal-display ${signal.signal.includes('BUY') ? 'buy' : signal.signal.includes('SELL') ? 'sell' : 'hold'}`}>
                    {signal.signal}
                  </div>
                  <div className="signal-details">
                    <p><strong>Preço:</strong> ${signal.entry}</p>
                    <p><strong>RSI (14):</strong> {signal.rsi}</p>
                    <p><strong>Stop:</strong> ${signal.stop}</p>
                    <p><strong>Alvo:</strong> ${signal.target}</p>
                  </div>
                </>
              )}
            </div>
          ))
        ) : (
          <div className="status-container">
            <p>Nenhum sinal disponível no momento.</p>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
