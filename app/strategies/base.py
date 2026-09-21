from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class MarketContext:
    candles: tuple

    @property
    def prices(self):
        return tuple(candle.close for candle in self.candles)

    @property
    def current(self):
        return self.candles[-1]


class BaseStrategy(ABC):
    @abstractmethod
    def analyze(self, prices):
        """Return a Signal using observations available so far."""

    def analyze_context(self, context):
        return self.analyze(context.prices)

    def protective_levels(self, context, entry_price):
        return None, None
