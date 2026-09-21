from app.core.broker import Broker
from app.core.risk_manager import RiskManager


class PaperBroker(Broker):
    """Execution simulator. It contains no Binance client or network access."""

    def __init__(self, maker_commission=0.0002, taker_commission=0.0004, slippage=0.0005,
                 position_size=0.20, max_position_fraction=0.20):
        super().__init__(taker_commission, RiskManager(position_size, max_position_fraction), maker_commission)
        if not 0 <= slippage <= 1:
            raise ValueError("slippage must be between 0 and 1")
        self.slippage = slippage

    def market_buy(self, portfolio, price, symbol):
        amount = self.risk_manager.calculate_position_size(portfolio.balance)
        return self.buy(portfolio, price * (1 + self.slippage), amount, symbol)

    def market_sell(self, portfolio, price):
        return self.sell(portfolio, price * (1 - self.slippage))
