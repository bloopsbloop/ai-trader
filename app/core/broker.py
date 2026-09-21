from app.core.commission import PercentageCommission
from app.models.order import Order, OrderSide, OrderStatus


class Broker:
    def __init__(self, commission_rate=0.001, risk_manager=None, maker_commission_rate=None):
        maker_rate = commission_rate if maker_commission_rate is None else maker_commission_rate
        self.commission = PercentageCommission(maker_rate, commission_rate)
        self.risk_manager = risk_manager
        self.orders = []

    def buy(self, portfolio, price, amount, symbol="BTCUSDT", is_maker=False):
        quantity = amount / price if price > 0 else 0.0
        order = Order(symbol=symbol, side=OrderSide.BUY, price=price, quantity=quantity)
        order.commission = self.commission.calculate(order.notional, is_maker) if quantity > 0 else 0.0
        rejection_reason = self._validate_buy(portfolio, order)
        if rejection_reason:
            return self._reject(order, rejection_reason)

        portfolio.open_position(symbol, price, quantity, order.commission)
        self.orders.append(order)
        return order

    def sell(self, portfolio, price, is_maker=False):
        position = portfolio.position
        quantity = position.quantity if position is not None and price > 0 else 0.0
        order = Order(symbol=position.symbol if position else "BTCUSDT", side=OrderSide.SELL, price=price, quantity=quantity)
        rejection_reason = self._validate_sell(portfolio, order)
        if rejection_reason:
            return self._reject(order, rejection_reason)

        order.entry_price = position.entry
        order.commission = self.commission.calculate(order.notional, is_maker)
        _, order.realized_pnl = portfolio.close_position(price, order.commission)
        self.orders.append(order)
        return order

    def _validate_buy(self, portfolio, order):
        if order.price <= 0 or order.quantity <= 0:
            return "Price and amount must be positive"
        if self.risk_manager is None:
            return None
        return self.risk_manager.validate_buy(portfolio, order.notional, order.commission)

    def _validate_sell(self, portfolio, order):
        if order.price <= 0:
            return "Price must be positive"
        if self.risk_manager is None:
            return None if portfolio.has_position() else "No open position to sell"
        return self.risk_manager.validate_sell(portfolio)

    def _reject(self, order, reason):
        order.status = OrderStatus.REJECTED
        order.rejection_reason = reason
        order.commission = 0.0
        self.orders.append(order)
        return order
