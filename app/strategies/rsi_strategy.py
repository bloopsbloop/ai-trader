from app.indicators.rsi import RSI
from app.models.signal import Signal
from app.strategies.base import BaseStrategy


class RSIStrategy(BaseStrategy):
    def __init__(self, rsi_period=14, oversold=30, overbought=70):
        if rsi_period < 1 or not 0 < oversold < overbought < 100:
            raise ValueError("Invalid RSI parameters")
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

    def analyze(self, prices):
        value = RSI.calculate(prices, self.rsi_period)
        if value is None:
            return Signal.WAIT
        if value <= self.oversold:
            return Signal.BUY
        if value >= self.overbought:
            return Signal.SELL
        return Signal.WAIT
