class PercentageCommission:
    """Calculates exchange fees from an order's notional value."""

    def __init__(self, rate=0.001):
        if rate < 0:
            raise ValueError("Commission rate cannot be negative")
        self.rate = rate

    def calculate(self, notional):
        if notional < 0:
            raise ValueError("Order notional cannot be negative")
        return notional * self.rate
