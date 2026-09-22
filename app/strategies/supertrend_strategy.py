from app.indicators.supertrend import Supertrend
from app.indicators.atr import ATR
from app.models.signal import Signal
from app.strategies.base import BaseStrategy


class SupertrendStrategy(BaseStrategy):
    def __init__(self, atr_period=10, multiplier=3.0):
        self.atr_period, self.multiplier = atr_period, multiplier
        self._last_time = None
        self._upper = self._lower = None
        self._bullish = None

    def analyze(self, prices):
        return Signal.WAIT

    def analyze_context(self, context):
        if len(context.candles) < self.atr_period + 2:
            return Signal.WAIT
        previous_bullish = self._bullish
        if self._last_time != context.candles[-2].open_time:
            previous = Supertrend.calculate(context.candles[:-1], self.atr_period, self.multiplier)
            if previous is None:
                return Signal.WAIT
            self._upper, self._lower, previous_bullish = previous.upper, previous.lower, previous.bullish
        atr = ATR.calculate(context.candles, self.atr_period)
        current = context.current
        midpoint = (current.high + current.low) / 2
        basic_upper, basic_lower = midpoint + self.multiplier * atr, midpoint - self.multiplier * atr
        previous_close = context.candles[-2].close
        self._upper = basic_upper if basic_upper < self._upper or previous_close > self._upper else self._upper
        self._lower = basic_lower if basic_lower > self._lower or previous_close < self._lower else self._lower
        bullish = previous_bullish
        if current.close > self._upper:
            bullish = True
        elif current.close < self._lower:
            bullish = False
        self._last_time, self._bullish = current.open_time, bullish
        if bullish and not previous_bullish:
            return Signal.BUY
        if previous_bullish and not bullish:
            return Signal.SELL
        return Signal.WAIT
