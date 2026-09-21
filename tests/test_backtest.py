from datetime import datetime, timedelta
import unittest

from app.backtest.engine import BacktestEngine
from app.backtest.report import Report
from app.backtest.statistics import EquityPoint, Statistics
from app.models.candle import Candle
from app.models.signal import Signal
from app.models.trade import Trade


class SequenceStrategy:
    def __init__(self, signals):
        self.signals = iter(signals)

    def analyze(self, prices):
        return next(self.signals, Signal.WAIT)


def candle(index, close, high=None, low=None):
    return Candle(
        open_time=datetime(2024, 1, 1) + timedelta(hours=index),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=1.0,
    )


class BacktestEngineTests(unittest.TestCase):
    def test_take_profit_creates_trade_and_equity_curve(self):
        engine = BacktestEngine(
            SequenceStrategy([Signal.BUY, Signal.WAIT]),
            position_size=1.0,
            max_position_fraction=1.0,
            take_profit=0.10,
            maker_commission=0.0,
            taker_commission=0.0,
            slippage=0.0,
        )

        stats = engine.run([candle(0, 100), candle(1, 108, high=112, low=105)], print_report=False)

        self.assertEqual(len(engine.equity_curve), 2)
        self.assertEqual(len(stats.trades), 1)
        self.assertEqual(stats.trades[0].exit_reason, "take_profit")
        self.assertAlmostEqual(stats.end_balance, 1100.0)
        self.assertEqual(stats.profit_factor, float("inf"))

    def test_trailing_stop_uses_high_water_mark(self):
        engine = BacktestEngine(
            SequenceStrategy([Signal.BUY, Signal.WAIT, Signal.WAIT]),
            position_size=1.0,
            max_position_fraction=1.0,
            trailing_stop=0.10,
            maker_commission=0.0,
            taker_commission=0.0,
            slippage=0.0,
        )

        stats = engine.run(
            [candle(0, 100), candle(1, 118, high=120, low=115), candle(2, 110, high=118, low=107)],
            print_report=False,
        )

        self.assertEqual(stats.trades[0].exit_reason, "trailing_stop")
        self.assertEqual(stats.trades[0].exit, 108.0)

    def test_statistics_calculates_drawdown_profit_factor_and_expectancy(self):
        trades = [Trade(100, 120, 20, 20), Trade(100, 70, -30, -30)]
        curve = [
            EquityPoint("first", 100),
            EquityPoint("peak", 120),
            EquityPoint("trough", 90),
            EquityPoint("last", 110),
        ]
        stats = Statistics(trades, 100, 110, curve)

        self.assertAlmostEqual(stats.profit_factor, 20 / 30)
        self.assertEqual(stats.average_win, 20)
        self.assertEqual(stats.average_loss, 30)
        self.assertEqual(stats.expectancy, -5)
        self.assertEqual(stats.max_drawdown, 25)
        self.assertIn("BACKTEST REPORT", Report.render(stats))


if __name__ == "__main__":
    unittest.main()
