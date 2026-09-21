class SMA:
    @staticmethod
    def calculate(values, period):
        if period < 1:
            raise ValueError("period must be positive")
        if len(values) < period:
            return None
        return sum(float(getattr(value, "close", value)) for value in values[-period:]) / period
