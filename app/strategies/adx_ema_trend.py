from app.indicators.adx import ADX
from app.indicators.ema import EMA
from app.models.signal import Signal
from app.strategies.base import BaseStrategy


class ADXEMATrendStrategy(BaseStrategy):
    def __init__(self, fast=20, slow=50, adx_period=14, adx_threshold=25):
        self.fast, self.slow = fast, slow
        self.adx_period, self.adx_threshold = adx_period, adx_threshold

    def analyze(self, prices):
        return Signal.WAIT

    def analyze_context(self, context):
        fast = EMA.calculate(context.prices, self.fast)
        slow = EMA.calculate(context.prices, self.slow)
        adx = ADX.calculate(context.candles, self.adx_period)
        if fast is None or slow is None or adx is None:
            return Signal.WAIT
        if fast > slow and adx >= self.adx_threshold:
            return Signal.BUY
        if fast < slow or adx < self.adx_threshold:
            return Signal.SELL
        return Signal.WAIT
