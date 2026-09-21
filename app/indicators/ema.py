class EMA:

    @staticmethod
    def calculate(prices, period):
        if period < 1:
            raise ValueError("period must be positive")

        if len(prices) < period:
            return None

        multiplier = 2 / (period + 1)

        ema = sum(float(getattr(price, "close", price)) for price in prices[:period]) / period

        for price in prices[period:]:
            ema = (float(getattr(price, "close", price)) - ema) * multiplier + ema

        return ema
