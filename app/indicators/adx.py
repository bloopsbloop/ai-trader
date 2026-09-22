class ADX:
    @staticmethod
    def calculate(candles, period=14):
        if period < 1:
            raise ValueError("period must be positive")
        if len(candles) < period * 2 + 1:
            return None
        true_ranges, plus_dm, minus_dm = [], [], []
        for previous, current in zip(candles, candles[1:]):
            true_ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
            up = current.high - previous.high
            down = previous.low - current.low
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)
        tr = sum(true_ranges[:period])
        plus = sum(plus_dm[:period])
        minus = sum(minus_dm[:period])
        dx = []
        for index in range(period, len(true_ranges)):
            tr = tr - tr / period + true_ranges[index]
            plus = plus - plus / period + plus_dm[index]
            minus = minus - minus / period + minus_dm[index]
            plus_di = 100 * plus / tr if tr else 0
            minus_di = 100 * minus / tr if tr else 0
            dx.append(100 * abs(plus_di - minus_di) / (plus_di + minus_di) if plus_di + minus_di else 0)
        return sum(dx[-period:]) / period if len(dx) >= period else None
