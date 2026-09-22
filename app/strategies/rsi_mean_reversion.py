from app.indicators.ema import EMA
from app.indicators.rsi import RSI
from app.models.signal import Signal
from app.strategies.base import BaseStrategy


class RSIMeanReversionStrategy(BaseStrategy):
    def __init__(self, rsi_period=14, oversold=25, exit_level=50, trend_ema=None):
        self.rsi_period, self.oversold, self.exit_level, self.trend_ema = rsi_period, oversold, exit_level, trend_ema

    def analyze(self, prices):
        rsi = RSI.calculate(prices, self.rsi_period)
        if rsi is None:
            return Signal.WAIT
        trend_value = EMA.calculate(prices, self.trend_ema) if self.trend_ema is not None else None
        if self.trend_ema is not None and trend_value is None:
            return Signal.WAIT
        trend_ok = self.trend_ema is None or prices[-1] >= trend_value
        if rsi <= self.oversold and trend_ok:
            return Signal.BUY
        if rsi >= self.exit_level:
            return Signal.SELL
        return Signal.WAIT
