from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from app.optimizer.engine import StrategyOptimizer
from app.optimizer.parameter_grid import ParameterGrid


class ParameterizedStrategy:
    def __init__(self, value):
        self.value = value


class FakeBacktestEngine:
    calls = []

    def __init__(self, strategy, **config):
        self.strategy = strategy

    def run(self, candles, print_report=False):
        candle_values = tuple(candles)
        self.calls.append((self.strategy.value, candle_values))
        train_score = 10 if self.strategy.value == 1 else 5
        return SimpleNamespace(
            profit_percent=train_score,
            profit=float(train_score),
            max_drawdown=2.0,
            sharpe_ratio=1.0,
            profit_factor=2.0,
            win_rate=50.0,
            trades=[object(), object()],
            expectancy=1.0,
        )


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        FakeBacktestEngine.calls = []
        self.candles = list(range(10))

    def test_parameter_grid_generates_every_combination(self):
        grid = ParameterGrid({"fast": [10, 20], "slow": [50, 60, 70]})

        self.assertEqual(len(grid), 6)
        self.assertEqual(list(grid.combinations())[0], {"fast": 10, "slow": 50})

    def test_splits_are_ordered_and_disjoint(self):
        split = StrategyOptimizer.split_data(self.candles)

        self.assertEqual(split.train, [0, 1, 2, 3, 4, 5])
        self.assertEqual(split.validation, [6, 7])
        self.assertEqual(split.test, [8, 9])
        self.assertFalse(set(split.train) & set(split.validation))
        self.assertFalse(set(split.validation) & set(split.test))

    def test_optimizer_tests_all_combinations_and_never_uses_test_for_selection(self):
        optimizer = StrategyOptimizer(backtest_engine_class=FakeBacktestEngine)

        result = optimizer.optimize(ParameterizedStrategy, {"value": [1, 2]}, self.candles, export=False, print_report=False)

        self.assertEqual(len(result.tested_combinations), 2)
        self.assertEqual(result.best_parameters, {"value": 1})
        test_calls = [value for value, candles in FakeBacktestEngine.calls if candles == (8, 9)]
        self.assertEqual(test_calls, [1])
        self.assertEqual(result.validation_metrics.total_return, 10)

    def test_optimizer_exports_csv_and_json(self):
        with TemporaryDirectory() as directory:
            optimizer = StrategyOptimizer(backtest_engine_class=FakeBacktestEngine, output_dir=directory)
            optimizer.optimize(ParameterizedStrategy, {"value": [1, 2]}, self.candles, print_report=False)

            self.assertTrue((Path(directory) / "parameterized_BTCUSDT_1h.csv").exists())
            self.assertTrue((Path(directory) / "parameterized_BTCUSDT_1h.json").exists())


if __name__ == "__main__":
    unittest.main()
