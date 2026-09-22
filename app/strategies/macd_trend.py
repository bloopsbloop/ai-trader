from app.indicators.ema import EMA
from app.indicators.macd import MACD
from app.models.signal import Signal
from app.strategies.base import BaseStrategy


class MACDTrendStrategy(BaseStrategy):
    def __init__(self, fast=12, slow=26, signal_period=9, trend_ema=None):
        self.fast, self.slow, self.signal_period, self.trend_ema = fast, slow, signal_period, trend_ema

    def analyze(self, prices):
        current = MACD.calculate(prices, self.fast, self.slow, self.signal_period)
        previous = MACD.calculate(prices[:-1], self.fast, self.slow, self.signal_period) if len(prices) > self.slow + self.signal_period - 1 else None
        if current is None or previous is None:
            return Signal.WAIT
        trend_value = EMA.calculate(prices, self.trend_ema) if self.trend_ema is not None else None
        if self.trend_ema is not None and trend_value is None:
            return Signal.WAIT
        trend_ok = self.trend_ema is None or prices[-1] > trend_value
        if previous.histogram <= 0 < current.histogram and trend_ok:
            return Signal.BUY
        if previous.histogram >= 0 > current.histogram:
            return Signal.SELL
        return Signal.WAIT
