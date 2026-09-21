from dataclasses import dataclass
from math import sqrt
from statistics import stdev


@dataclass(frozen=True)
class EquityPoint:
    timestamp: object
    equity: float


class Statistics:
    def __init__(self, trades, start_balance, end_balance, equity_curve=None, periods_per_year=365):
        self.trades = list(trades)
        self.start_balance = start_balance
        self.end_balance = end_balance
        self.equity_curve = list(equity_curve or [])
        self.periods_per_year = periods_per_year

    @property
    def profit(self):
        return self.end_balance - self.start_balance

    @property
    def profit_percent(self):
        return self.profit / self.start_balance * 100 if self.start_balance else 0.0

    @property
    def wins(self):
        return sum(trade.profit > 0 for trade in self.trades)

    @property
    def losses(self):
        return sum(trade.profit < 0 for trade in self.trades)

    @property
    def win_rate(self):
        return self.wins / len(self.trades) * 100 if self.trades else 0.0

    @property
    def gross_profit(self):
        return sum(trade.profit for trade in self.trades if trade.profit > 0)

    @property
    def gross_loss(self):
        return abs(sum(trade.profit for trade in self.trades if trade.profit < 0))

    @property
    def profit_factor(self):
        if self.gross_loss == 0:
            return float("inf") if self.gross_profit else 0.0
        return self.gross_profit / self.gross_loss

    @property
    def average_win(self):
        return self.gross_profit / self.wins if self.wins else 0.0

    @property
    def average_loss(self):
        return self.gross_loss / self.losses if self.losses else 0.0

    @property
    def expectancy(self):
        if not self.trades:
            return 0.0
        win_probability = self.wins / len(self.trades)
        return win_probability * self.average_win - (1 - win_probability) * self.average_loss

    @property
    def max_drawdown(self):
        values = [point.equity for point in self.equity_curve] or [self.start_balance, self.end_balance]
        peak = values[0]
        maximum = 0.0
        for value in values:
            peak = max(peak, value)
            if peak:
                maximum = max(maximum, (peak - value) / peak * 100)
        return maximum

    @property
    def sharpe_ratio(self):
        values = [point.equity for point in self.equity_curve]
        if len(values) < 3:
            return 0.0
        returns = [current / previous - 1 for previous, current in zip(values, values[1:]) if previous]
        if len(returns) < 2:
            return 0.0
        volatility = stdev(returns)
        return sum(returns) / len(returns) / volatility * sqrt(self.periods_per_year) if volatility else 0.0
