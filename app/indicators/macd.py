from dataclasses import dataclass


@dataclass(frozen=True)
class MACDValue:
    macd: float
    signal: float
    histogram: float


class MACD:
    @staticmethod
    def calculate(values, fast=12, slow=26, signal=9):
        if not 0 < fast < slow or signal < 1:
            raise ValueError("periods must satisfy 0 < fast < slow and signal > 0")
        if len(values) < slow + signal - 1:
            return None
        closes = [float(getattr(value, "close", value)) for value in values]
        fast_series = MACD._ema_series(closes, fast)
        slow_series = MACD._ema_series(closes, slow)
        macd_values = [fast_series[index] - slow_series[index] for index in range(slow - 1, len(closes))]
        signal_values = MACD._ema_series(macd_values, signal)
        return MACDValue(macd_values[-1], signal_values[-1], macd_values[-1] - signal_values[-1])

    @staticmethod
    def _ema_series(values, period):
        result = [None] * (period - 1)
        average = sum(values[:period]) / period
        result.append(average)
        multiplier = 2 / (period + 1)
        for value in values[period:]:
            average += (value - average) * multiplier
            result.append(average)
        return result
