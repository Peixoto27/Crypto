import ccxt
from decimal import Decimal, getcontext
from typing import Dict, Optional
from ..models import Signal, Order
from ..utils import logger, retry_on_failure

getcontext().prec = 8  # Precisão decimal para cripto

class TradingExecutor:
    def __init__(self, config: Dict):
        self.exchanges = {}
        self.setup_exchanges(config['EXCHANGES'])
        
    def setup_exchanges(self, exchange_configs: Dict):
        """Configura conexões com exchanges"""
        for name, config in exchange_configs.items():
            exchange_class = getattr(ccxt, name.lower())
            self.exchanges[name] = exchange_class({
                'apiKey': config['API_KEY'],
                'secret': config['API_SECRET'],
                'enableRateLimit': True,
                'options': {
                    'createMarketBuyOrderRequiresPrice': False
                }
            })
    
    @retry_on_failure(max_retries=3)
    async def execute_order(self, signal: Signal) -> Optional[Order]:
        """Executa ordem baseada em sinal com gerenciamento de risco"""
        exchange = self.exchanges.get(signal.exchange)
        if not exchange:
            logger.error(f"Exchange {signal.exchange} não configurada")
            return None
        
        try:
            # 1. Análise de Risco
            if not self._risk_analysis(signal):
                logger.warning(f"Ordem rejeitada por análise de risco: {signal}")
                return None
            
            # 2. Cálculo de tamanho de posição
            balance = await self._get_balance(exchange, signal.symbol)
            position_size = self._calculate_position_size(balance, signal)
            
            # 3. Estratégia de Entrada
            order = await self._entry_strategy(exchange, signal, position_size)
            
            # 4. Gerenciamento de Saída (stop loss/take profit)
            await self._setup_exit_strategy(exchange, order)
            
            return order
            
        except ccxt.NetworkError as e:
            logger.error(f"Erro de rede: {e}")
        except ccxt.ExchangeError as e:
            logger.error(f"Erro na exchange: {e}")
        except Exception as e:
            logger.error(f"Erro inesperado: {e}")
        
        return None
    
    def _risk_analysis(self, signal: Signal) -> bool:
        """Aplica regras de risco antes de executar ordem"""
        # Implementar:
        # - Verificação de volatilidade
        # - Horário de mercado
        # - Correlação com BTC
        # - Volume recente
        return signal.confidence >= 75  # Exemplo simples
        
    async def _get_balance(self, exchange, symbol: str) -> Decimal:
        """Obtém saldo disponível para trading"""
        quote_currency = symbol.split('/')[1]
        balance = await exchange.fetch_balance()
        return Decimal(str(balance['free'].get(quote_currency, 0)))
    
    def _calculate_position_size(self, balance: Decimal, signal: Signal) -> Decimal:
        """Calcula tamanho da posição com base no risco"""
        # 1% a 2% do capital por operação (ajustável)
        risk_percentage = Decimal('0.02')
        position_size = balance * risk_percentage
        
        # Ajustar para alvo de risco/recompensa
        if signal.target_percentage:
            risk_reward = Decimal('3.0')  # 1:3
            potential_loss = position_size * Decimal('0.01')  # 1% stop
            potential_gain = potential_loss * risk_reward
            position_size = min(position_size, potential_gain)
            
        return position_size.quantize(Decimal('0.00000001'))
    
    async def _entry_strategy(self, exchange, signal: Signal, amount: Decimal) -> Order:
        """Executa estratégia de entrada com melhor execução"""
        symbol = signal.symbol
        price = Decimal(str(signal.price))
        
        # Tipos de ordem disponíveis
        order_types = {
            'MARKET': self._execute_market_order,
            'LIMIT': self._execute_limit_order,
            'TWAP': self._execute_twap_order
        }
        
        # Seleciona melhor estratégia baseada em condições de mercado
        order_func = order_types.get(signal.order_type, self._execute_market_order)
        return await order_func(exchange, symbol, amount, price)
    
    async def _setup_exit_strategy(self, exchange, order: Order):
        """Configura ordens OCO (One Cancels Other)"""
        # Implementar stop loss e take profit
        pass