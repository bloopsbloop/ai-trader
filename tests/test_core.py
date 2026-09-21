import unittest

from app.core.broker import Broker
from app.core.portfolio import Portfolio
from app.core.risk_manager import RiskManager
from app.models.order import OrderStatus


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.portfolio = Portfolio(1000.0)
        self.broker = Broker(
            commission_rate=0.001,
            risk_manager=RiskManager(
                risk_percent=0.5,
                max_position_fraction=0.5,
                max_daily_loss_fraction=0.1,
            ),
        )

    def test_buy_updates_cash_position_and_commission(self):
        order = self.broker.buy(self.portfolio, price=50000.0, amount=500.0)

        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.commission, 0.5)
        self.assertEqual(self.portfolio.balance, 499.5)
        self.assertEqual(self.portfolio.position.quantity, 0.01)

    def test_sell_realizes_profit_after_both_commissions(self):
        self.broker.buy(self.portfolio, price=50000.0, amount=500.0)
        order = self.broker.sell(self.portfolio, price=55000.0)

        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertAlmostEqual(order.realized_pnl, 48.95)
        self.assertIsNone(self.portfolio.position)

    def test_risk_rejects_oversized_order(self):
        order = self.broker.buy(self.portfolio, price=50000.0, amount=600.0)

        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(order.rejection_reason, "Order exceeds the maximum position size")
        self.assertEqual(self.portfolio.balance, 1000.0)

    def test_broker_applies_maker_commission_when_requested(self):
        broker = Broker(
            commission_rate=0.001,
            maker_commission_rate=0.0002,
            risk_manager=RiskManager(risk_percent=0.5, max_position_fraction=0.5),
        )

        order = broker.buy(self.portfolio, price=50000.0, amount=500.0, is_maker=True)

        self.assertEqual(order.commission, 0.1)


if __name__ == "__main__":
    unittest.main()
