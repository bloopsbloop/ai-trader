from app.models.signal import Signal
from app.models.trade import Trade
from app.core.risk_manager import RiskManager


class BacktestEngine:

    def __init__(self, strategy):

        self.strategy = strategy

        self.balance = 1000.0

        self.position = None

        self.trades = []

        self.risk = RiskManager()

    def run(self, candles):

        prices = []

        for candle in candles:

            prices.append(candle.close)

            signal = self.strategy.analyze(prices)

            if signal == Signal.BUY and self.position is None:

                self.position = candle.close

            elif signal == Signal.SELL and self.position is not None:

                position_size = self.risk.calculate_position_size(
                    self.balance
                )

                profit_percent = (
                    candle.close - self.position
                ) / self.position

                profit = position_size * profit_percent

                self.balance += profit

                self.trades.append(
                    Trade(
                        entry=self.position,
                        exit=candle.close,
                        profit=profit,
                        profit_percent=profit_percent * 100
                    )
                )

                self.position = None

        self.print_statistics()

    def print_statistics(self):

        print("=" * 40)

        print(f"Start   : 1000.00$")
        print(f"Balance : {self.balance:.2f}$")
        print(f"Profit  : {self.balance - 1000:.2f}$")

        print(f"Trades  : {len(self.trades)}")

        if len(self.trades) == 0:
            return

        wins = len([t for t in self.trades if t.profit > 0])

        losses = len(self.trades) - wins

        print(f"Wins    : {wins}")
        print(f"Losses  : {losses}")
        print(f"WinRate : {wins / len(self.trades) * 100:.2f}%")