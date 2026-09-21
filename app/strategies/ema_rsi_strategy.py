from app.indicators.ema import EMA
from app.indicators.rsi import RSI
from app.models.signal import Signal
from app.strategies.base import BaseStrategy


class EMARSIStrategy(BaseStrategy):
    def __init__(self, fast=20, slow=50, rsi_period=14, oversold=30, overbought=70):
        if not 0 < fast < slow or rsi_period < 1 or not 0 < oversold < overbought < 100:
            raise ValueError("Invalid EMA/RSI parameters")
        self.fast = fast
        self.slow = slow
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

    def analyze(self, prices):
        fast_value = EMA.calculate(prices, self.fast)
        slow_value = EMA.calculate(prices, self.slow)
        rsi = RSI.calculate(prices, self.rsi_period)
        if fast_value is None or slow_value is None or rsi is None:
            return Signal.WAIT
        if fast_value > slow_value and rsi <= self.oversold:
            return Signal.BUY
        if fast_value < slow_value or rsi >= self.overbought:
            return Signal.SELL
        return Signal.WAIT
