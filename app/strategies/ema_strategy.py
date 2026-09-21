from app.indicators.ema import EMA
from app.models.signal import Signal
from app.strategies.base import BaseStrategy

class EMAStrategy(BaseStrategy):
    def __init__(self, fast=20, slow=50):
        if not 0 < fast < slow:
            raise ValueError("fast EMA period must be positive and smaller than slow EMA period")
        self.fast = fast
        self.slow = slow

    def analyze(self, prices):
        ema20 = EMA.calculate(prices, self.fast)
        ema50 = EMA.calculate(prices, self.slow)

        if ema20 is None or ema50 is None:
            return Signal.WAIT

        if ema20 > ema50:

            return Signal.BUY

        if ema20 < ema50:
            return Signal.SELL

        return Signal.WAIT
