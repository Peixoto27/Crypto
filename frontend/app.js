const API_URL = "https://moedas-production.up.railway.app/signals";
const PASSWORD = "Zoe1001";

function checkPassword() {
  const input = document.getElementById("password-input");
  if (input.value === PASSWORD) {
    document.getElementById("login-screen").style.display = "none";
    document.getElementById("app").style.display = "block";
    fetchSignals();
    setInterval(fetchSignals, 20 * 60 * 1000); // Atualiza a cada 20 minutos
  } else {
    alert("Senha incorreta!");
  }
}

function fetchSignals() {
  fetch(API_URL)
    .then(res => res.json())
    .then(data => {
      const container = document.getElementById("signals-container");
      container.innerHTML = "";

      data.forEach(signal => {
        const card = document.createElement("div");
        card.className = "card";
        card.innerHTML = `
          <h2>📊 ${signal.pair}</h2>
          <p><strong>Sinal:</strong> ${signal.signal}</p>
          <p><strong>Entrada:</strong> $${signal.entry}</p>
          <p><strong>Alvo:</strong> $${signal.target}</p>
          <p><strong>Stop:</strong> $${signal.stop}</p>
          <p><strong>Confiança:</strong> ${signal.confidence}%</p>
          <p><strong>R/R:</strong> ${signal.rr_ratio}</p>
          <p><strong>Potencial:</strong> ${signal.potential}</p>
          <p><strong>⏰:</strong> ${signal.timestamp}</p>
        `;
        container.appendChild(card);
      });
    })
    .catch(err => {
      console.error("Erro ao carregar os sinais", err);
      document.getElementById("signals-container").innerHTML = "<p>Erro ao carregar os sinais.</p>";
    });
}
