from dataclasses import dataclass

from app.indicators.atr import ATR


@dataclass(frozen=True)
class SupertrendValue:
    value: float
    bullish: bool
    upper: float
    lower: float


class Supertrend:
    @staticmethod
    def calculate(candles, period=10, multiplier=3.0):
        if period < 1 or multiplier <= 0:
            raise ValueError("Invalid Supertrend parameters")
        if len(candles) < period + 2:
            return None
        trend = True
        upper = lower = None
        for index in range(period + 1, len(candles) + 1):
            window = candles[:index]
            atr = ATR.calculate(window, period)
            current = window[-1]
            midpoint = (current.high + current.low) / 2
            basic_upper, basic_lower = midpoint + multiplier * atr, midpoint - multiplier * atr
            upper = basic_upper if upper is None or basic_upper < upper or window[-2].close > upper else upper
            lower = basic_lower if lower is None or basic_lower > lower or window[-2].close < lower else lower
            if current.close > upper:
                trend = True
            elif current.close < lower:
                trend = False
        return SupertrendValue(lower if trend else upper, trend, upper, lower)
