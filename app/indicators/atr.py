class ATR:
    @staticmethod
    def calculate(candles, period=14):
        if period < 1:
            raise ValueError("period must be positive")
        if len(candles) < period + 1:
            return None
        ranges = [
            max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))
            for previous, current in zip(candles, candles[1:])
        ]
        average = sum(ranges[:period]) / period
        for true_range in ranges[period:]:
            average = (average * (period - 1) + true_range) / period
        return average
