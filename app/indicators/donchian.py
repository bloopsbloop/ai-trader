from dataclasses import dataclass


@dataclass(frozen=True)
class DonchianValue:
    high: float
    low: float


class DonchianChannel:
    @staticmethod
    def calculate(candles, period=20):
        if period < 1:
            raise ValueError("period must be positive")
        if len(candles) < period:
            return None
        window = candles[-period:]
        return DonchianValue(max(candle.high for candle in window), min(candle.low for candle in window))
