class RiskManager:
    def __init__(
        self,
        risk_percent=2,
        max_position_fraction=0.20,
        max_daily_loss_fraction=0.03,
    ):
        self.risk_fraction = risk_percent if risk_percent <= 1 else risk_percent / 100
        self.max_position_fraction = max_position_fraction
        self.max_daily_loss_fraction = max_daily_loss_fraction
        for limit in (
            self.risk_fraction,
            self.max_position_fraction,
            self.max_daily_loss_fraction,
        ):
            if not 0 < limit <= 1:
                raise ValueError("Risk limits must be between 0 and 1")

    def calculate_position_size(self, balance):
        return balance * self.risk_fraction

    def validate_buy(self, portfolio, notional, commission):
        if notional <= 0:
            return "Order amount must be positive"
        if portfolio.has_position():
            return "Only one position can be open at a time"
        if notional > portfolio.initial_balance * self.max_position_fraction:
            return "Order exceeds the maximum position size"
        if notional + commission > portfolio.balance:
            return "Insufficient balance"
        if portfolio.realized_pnl <= -portfolio.initial_balance * self.max_daily_loss_fraction:
            return "Daily loss limit has been reached"
        return None

    def validate_sell(self, portfolio):
        if not portfolio.has_position():
            return "No open position to sell"
        return None
