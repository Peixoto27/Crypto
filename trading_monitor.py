import logging
from datetime import datetime
from typing import Dict, Any
from prometheus_client import start_http_server, Counter, Gauge
from ..config import settings

class TradingMonitor:
    """Monitoramento em tempo real do desempenho do sistema"""
    
    def __init__(self):
        # Métricas Prometheus
        self.trades_counter = Counter('trading_trades_total', 'Total trades executed')
        self.profit_gauge = Gauge('trading_profit_usd', 'Current profit in USD')
        self.drawdown_gauge = Gauge('trading_max_drawdown', 'Maximum drawdown')
        
        # Configurar exportador de métricas
        start_http_server(settings.METRICS_PORT)
        
    def track_trade(self, trade: Dict[str, Any]):
        """Registra métricas de trade"""
        self.trades_counter.inc()
        
        if trade['profit']:
            self.profit_gauge.set(trade['profit'])
        
        # Calcular e atualizar drawdown
        self._update_drawdown(trade['balance'])
    
    def send_alert(self, message: str, level: str = "warning"):
        """Envia alertas para canais configurados"""
        channels = {
            'critical': self._send_critical_alert,
            'warning': self._send_warning,
            'info': self._send_info
        }
        
        channels.get(level, self._send_warning)(message)
    
    def _update_drawdown(self, current_balance: float):
        """Calcula drawdown atual"""
        pass