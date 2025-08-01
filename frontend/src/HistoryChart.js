import React from 'react';
import Chart from 'react-apexcharts';

const HistoryChart = ({ data }) => {
  const { prices, markers } = data;

  // Formata os dados de preço para o gráfico
  const series = [{
    name: 'Preço',
    data: prices.map(p => [p[0], p[1].toFixed(4)]) // Timestamp e Preço
  }];

  // Configurações do gráfico
  const options = {
    chart: {
      type: 'line',
      height: 350,
      background: '#2c3e50', // Fundo do gráfico
      foreColor: '#ecf0f1'   // Cor do texto (eixos, etc.)
    },
    stroke: {
      curve: 'smooth',
      width: 2
    },
    title: {
      text: 'Histórico de Preço com Sinais',
      align: 'left',
      style: {
        color: '#ecf0f1'
      }
    },
    markers: {
      size: 0 // Esconde os marcadores padrão da linha
    },
    xaxis: {
      type: 'datetime',
      labels: {
        style: {
          colors: '#bdc3c7'
        }
      }
    },
    yaxis: {
      labels: {
        formatter: function (value) {
          return "$" + value.toFixed(2);
        },
        style: {
          colors: '#bdc3c7'
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
      borderColor: '#34495e'
    },
    // ✅ Marcadores personalizados para os sinais de Compra/Venda
    annotations: {
      points: markers.map(marker => ({
        x: marker.timestamp,
        y: marker.price,
        marker: {
          size: 6,
          fillColor: marker.type === 'BUY' ? '#2ecc71' : '#e74c3c', // Verde para Compra, Vermelho para Venda
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
          text: marker.text, // "C" ou "V"
        }
      }))
    }
  };

  return (
    <div className="chart-container">
      <Chart options={options} series={series} type="line" height={350} />
    </div>
  );
};

export default HistoryChart;
