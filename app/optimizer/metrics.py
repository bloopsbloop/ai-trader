from dataclasses import asdict, dataclass
from math import isinf


@dataclass(frozen=True)
class BacktestMetrics:
    total_return: float
    net_profit: float
    max_drawdown: float
    sharpe_ratio: float
    profit_factor: float
    win_rate: float
    number_of_trades: int
    expectancy: float

    @classmethod
    def from_statistics(cls, statistics):
        return cls(statistics.profit_percent, statistics.profit, statistics.max_drawdown, statistics.sharpe_ratio, statistics.profit_factor, statistics.win_rate, len(statistics.trades), statistics.expectancy)

    def as_dict(self):
        values = asdict(self)
        if isinf(values["profit_factor"]):
            values["profit_factor"] = "inf"
        return values


class WeightedScore:
    """A transparent weighted score; each weight controls its named metric."""

    DEFAULT_WEIGHTS = {"return": 1.0, "max_drawdown": 1.0, "sharpe": 1.0, "profit_factor": 1.0}

    def __init__(self, weights=None, minimum_trade_count=1, profit_factor_cap=5.0):
        self.weights = {**self.DEFAULT_WEIGHTS, **(weights or {})}
        if set(self.weights) != set(self.DEFAULT_WEIGHTS):
            raise ValueError(f"Scoring weights must use: {', '.join(self.DEFAULT_WEIGHTS)}")
        if minimum_trade_count < 0 or profit_factor_cap <= 0:
            raise ValueError("Minimum trade count must be non-negative and profit factor cap positive")
        self.minimum_trade_count = minimum_trade_count
        self.profit_factor_cap = profit_factor_cap

    def calculate(self, metrics):
        if metrics.number_of_trades < self.minimum_trade_count:
            return float("-inf")
        profit_factor = min(metrics.profit_factor, self.profit_factor_cap)
        return self.weights["return"] * metrics.total_return - self.weights["max_drawdown"] * metrics.max_drawdown + self.weights["sharpe"] * metrics.sharpe_ratio + self.weights["profit_factor"] * profit_factor
