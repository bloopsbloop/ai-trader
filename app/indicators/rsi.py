class RSI:
    @staticmethod
    def calculate(values, period=14):
        if period < 1:
            raise ValueError("period must be positive")
        if len(values) <= period:
            return None
        closes = [float(getattr(value, "close", value)) for value in values]
        changes = [current - previous for previous, current in zip(closes, closes[1:])]
        gains = [max(change, 0.0) for change in changes]
        losses = [max(-change, 0.0) for change in changes]
        average_gain = sum(gains[:period]) / period
        average_loss = sum(losses[:period]) / period
        for gain, loss in zip(gains[period:], losses[period:]):
            average_gain = (average_gain * (period - 1) + gain) / period
            average_loss = (average_loss * (period - 1) + loss) / period
        if average_loss == 0:
            return 100.0 if average_gain else 50.0
        return 100 - 100 / (1 + average_gain / average_loss)
