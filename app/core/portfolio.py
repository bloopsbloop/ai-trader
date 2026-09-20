from app.models.position import Position


class Portfolio:
    def __init__(self, balance=1000.0):
        if balance < 0:
            raise ValueError("Initial balance cannot be negative")
        self.initial_balance = balance
        self.balance = balance
        self.position = None
        self.total_commissions = 0.0
        self.realized_pnl = 0.0

    def open_position(self, symbol, price, quantity, commission):
        notional = price * quantity
        total_cost = notional + commission
        if total_cost > self.balance:
            raise ValueError("Insufficient balance")
        self.position = Position(
            symbol=symbol,
            entry=price,
            quantity=quantity,
            cost_basis=total_cost,
            entry_commission=commission,
        )
        self.balance -= total_cost
        self.total_commissions += commission

    def close_position(self, price, commission):
        if self.position is None:
            raise ValueError("No open position to close")
        position = self.position
        proceeds = price * position.quantity
        net_proceeds = proceeds - commission
        profit = net_proceeds - position.cost_basis
        self.balance += net_proceeds
        self.total_commissions += commission
        self.realized_pnl += profit
        self.position = None
        return position, profit

    def has_position(self):
        return self.position is not None

    def equity(self, price=None):
        if self.position is None:
            return self.balance
        if price is None:
            price = self.position.entry
        return self.balance + self.position.quantity * price
