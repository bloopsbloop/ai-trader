from app.indicators.donchian import DonchianChannel
from app.models.signal import Signal
from app.strategies.base import BaseStrategy


class DonchianBreakoutStrategy(BaseStrategy):
    def __init__(self, entry_period=20, exit_period=10):
        if not 0 < exit_period <= entry_period:
            raise ValueError("Invalid Donchian periods")
        self.entry_period, self.exit_period = entry_period, exit_period

    def analyze(self, prices):
        return Signal.WAIT

    def analyze_context(self, context):
        if len(context.candles) <= self.entry_period:
            return Signal.WAIT
        entry = DonchianChannel.calculate(context.candles[:-1], self.entry_period)
        exit_channel = DonchianChannel.calculate(context.candles[:-1], self.exit_period)
        if context.current.close > entry.high:
            return Signal.BUY
        if context.current.close < exit_channel.low:
            return Signal.SELL
        return Signal.WAIT
