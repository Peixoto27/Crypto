import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css'; // Importa o ficheiro CSS que acabámos de corrigir
import App from './App';

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
