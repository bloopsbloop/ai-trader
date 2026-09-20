from app.models.signal import Signal
from app.models.trade import Trade

from app.core.portfolio import Portfolio
from app.core.broker import Broker
from app.core.risk_manager import RiskManager
from app.models.order import OrderStatus


class BacktestEngine:

    def __init__(self, strategy):

        self.strategy = strategy

        self.portfolio = Portfolio(1000)
        self.risk_manager = RiskManager(risk_percent=2)
        self.broker = Broker(risk_manager=self.risk_manager)

        self.trades = []

    def run(self, candles):

        prices = []

        for candle in candles:

            prices.append(candle.close)

            signal = self.strategy.analyze(prices)

            if signal == Signal.BUY and not self.portfolio.has_position():

                amount = self.risk_manager.calculate_position_size(self.portfolio.balance)
                self.broker.buy(
                    self.portfolio,
                    candle.close,
                    amount
                )

            elif signal == Signal.SELL and self.portfolio.has_position():

                result = self.broker.sell(
                    self.portfolio,
                    candle.close
                )
                if result.status == OrderStatus.FILLED:
                    self.trades.append(
                        Trade(
                            entry=result.entry_price,
                            exit=result.price,
                            profit=result.realized_pnl,
                            profit_percent=(
                                (result.price - result.entry_price)
                                / result.entry_price
                            ) * 100
                        )
                    )

        self.print_statistics()

    def print_statistics(self):

        print("=" * 40)

        print(f"Start Balance : 1000.00$")
        print(f"End Balance   : {self.portfolio.balance:.2f}$")
        print(f"Profit        : {self.portfolio.balance - 1000:.2f}$")

        print()

        print(f"Trades        : {len(self.trades)}")

        if not self.trades:
            return

        wins = len([t for t in self.trades if t.profit > 0])

        losses = len(self.trades) - wins

        print(f"Wins          : {wins}")
        print(f"Losses        : {losses}")
        print(f"Win Rate      : {wins / len(self.trades) * 100:.2f}%")
