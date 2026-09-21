from app.indicators.atr import ATR
from app.strategies.ema_rsi_strategy import EMARSIStrategy


class EMARSIATRStrategy(EMARSIStrategy):
    def __init__(self, fast=20, slow=50, rsi_period=14, oversold=30, overbought=70, atr_period=14, stop_atr_multiplier=2.0, take_profit_atr_multiplier=3.0):
        super().__init__(fast, slow, rsi_period, oversold, overbought)
        if atr_period < 1 or stop_atr_multiplier <= 0 or take_profit_atr_multiplier <= 0:
            raise ValueError("Invalid ATR parameters")
        self.atr_period = atr_period
        self.stop_atr_multiplier = stop_atr_multiplier
        self.take_profit_atr_multiplier = take_profit_atr_multiplier

    def analyze_context(self, context):
        if ATR.calculate(context.candles, self.atr_period) is None:
            from app.models.signal import Signal
            return Signal.WAIT
        return self.analyze(context.prices)

    def protective_levels(self, context, entry_price):
        atr = ATR.calculate(context.candles, self.atr_period)
        if atr is None:
            return None, None
        return entry_price - atr * self.stop_atr_multiplier, entry_price + atr * self.take_profit_atr_multiplier
