class PercentageCommission:
    """Calculates exchange fees from an order's notional value."""

    def __init__(self, maker_rate=0.001, taker_rate=None):
        self.maker_rate = maker_rate
        self.taker_rate = maker_rate if taker_rate is None else taker_rate
        if self.maker_rate < 0 or self.taker_rate < 0:
            raise ValueError("Commission rates cannot be negative")

    def calculate(self, notional, is_maker=False):
        if notional < 0:
            raise ValueError("Order notional cannot be negative")
        rate = self.maker_rate if is_maker else self.taker_rate
        return notional * rate
