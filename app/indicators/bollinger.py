from dataclasses import dataclass
from statistics import pstdev

from app.indicators.sma import SMA


@dataclass(frozen=True)
class BollingerValue:
    middle: float
    upper: float
    lower: float


class BollingerBands:
    @staticmethod
    def calculate(values, period=20, multiplier=2.0):
        if period < 2 or multiplier <= 0:
            raise ValueError("Invalid Bollinger parameters")
        if len(values) < period:
            return None
        prices = [float(getattr(value, "close", value)) for value in values[-period:]]
        middle = SMA.calculate(prices, period)
        deviation = pstdev(prices)
        return BollingerValue(middle, middle + multiplier * deviation, middle - multiplier * deviation)
