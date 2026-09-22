from app.indicators.bollinger import BollingerBands
from app.indicators.rsi import RSI
from app.models.signal import Signal
from app.strategies.base import BaseStrategy


class BollingerMeanReversionStrategy(BaseStrategy):
    def __init__(self, period=20, std_multiplier=2.0, rsi_period=14, rsi_oversold=None):
        self.period, self.std_multiplier = period, std_multiplier
        self.rsi_period, self.rsi_oversold = rsi_period, rsi_oversold

    def analyze(self, prices):
        bands = BollingerBands.calculate(prices, self.period, self.std_multiplier)
        if bands is None:
            return Signal.WAIT
        rsi = RSI.calculate(prices, self.rsi_period) if self.rsi_oversold is not None else None
        if prices[-1] < bands.lower and (rsi is None or rsi <= self.rsi_oversold):
            return Signal.BUY
        if prices[-1] >= bands.middle:
            return Signal.SELL
        return Signal.WAIT
