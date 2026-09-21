from datetime import datetime, timedelta
from tempfile import TemporaryDirectory

import pytest

from app.backtest.engine import BacktestEngine
from app.indicators.atr import ATR
from app.indicators.ema import EMA
from app.indicators.macd import MACD
from app.indicators.rsi import RSI
from app.models.candle import Candle
from app.models.signal import Signal
from app.optimizer import StrategyComparator, StrategyOptimizer
from app.strategies.base import BaseStrategy
from app.strategies.ema_rsi_strategy import EMARSIStrategy
from app.strategies.ema_rsi_atr_strategy import EMARSIATRStrategy
from app.strategies.rsi_strategy import RSIStrategy


def candles(prices):
    return [Candle(datetime(2024, 1, 1) + timedelta(hours=index), price, price + 1, price - 1, price, 1)
            for index, price in enumerate(prices)]


class RecordingStrategy(BaseStrategy):
    observed = []

    def analyze(self, prices):
        self.observed.append(tuple(prices))
        return Signal.BUY


def test_indicators_known_values_and_warmup():
    assert RSI.calculate([1, 2, 3], 3) is None
    assert RSI.calculate([1, 2, 3, 4], 3) == 100
    assert RSI.calculate([4, 3, 2, 1], 3) == 0
    assert ATR.calculate(candles([10, 11, 12]), 3) is None
    assert ATR.calculate(candles([10, 11, 12]), 2) == 2
    assert MACD.calculate([5] * 7, 2, 4, 2).histogram == 0
    assert MACD.calculate([1, 2, 3], 2, 4, 2) is None
    assert EMA.calculate(candles([1, 2, 3]), 2) == pytest.approx(2.5)


def test_strategy_context_sees_only_past_and_warmup_does_not_trade():
    strategy = RecordingStrategy()
    strategy.observed = []
    history = candles([10, 11, 12])
    evaluation = candles([13, 14])
    engine = BacktestEngine(strategy, position_size=0.1, slippage=0, taker_commission=0)
    engine.run(evaluation, print_report=False, warmup_candles=history)
    assert strategy.observed[0] == (10, 11, 12, 13)
    assert all(15 not in observed for observed in strategy.observed)
    assert len(engine.equity_curve) == 2
    assert len(engine.trades) == 1
    assert engine.trades[0].entry_time == evaluation[0].open_time


def test_walk_forward_splits_leave_final_test_untouched():
    windows, test_start = StrategyOptimizer.walk_forward_splits(range(30), 10, 4, 4)
    assert test_start == 24
    assert windows == [(0, 10, 10, 14), (4, 14, 14, 18), (8, 18, 18, 22)]
    assert all(train_end == validation_start and validation_end <= test_start
               for _, train_end, validation_start, validation_end in windows)


def test_new_strategies_can_be_optimized_and_compared():
    values = candles([100 + index % 5 for index in range(45)])
    with TemporaryDirectory() as directory:
        optimizer = StrategyOptimizer(minimum_trade_count=0, output_dir=directory,
                                      backtest_config={"position_size": 0.1})
        result = optimizer.optimize(RSIStrategy, {"rsi_period": [2, 3]}, values,
                                    export=False, print_report=False)
        assert len(result.tested_combinations) == 2
        comparison = StrategyComparator(output_dir=directory).compare(
            [RSIStrategy, (EMARSIStrategy, {"fast": 2, "slow": 5}),
             (EMARSIATRStrategy, {"fast": 2, "slow": 5, "atr_period": 2})],
            values, print_report=False)
        assert len(comparison.rows) == 3
        assert "Expectancy" in comparison.format_table()


def test_walk_forward_and_stability_export():
    values = candles([100 + index % 7 for index in range(60)])
    with TemporaryDirectory() as directory:
        optimizer = StrategyOptimizer(minimum_trade_count=0, output_dir=directory)
        result = optimizer.walk_forward(RSIStrategy, {"rsi_period": [2, 3]}, values,
                                        train_size=20, validation_size=8, step_size=8)
        assert result.windows
        assert result.parameter_stability["neighbors"]
        from pathlib import Path
        assert list(Path(directory).glob("*_walk_forward.json"))
        assert list(Path(directory).glob("*_walk_forward.csv"))


def test_final_test_is_evaluated_once_after_parameter_selection():
    from tests.test_optimizer import FakeBacktestEngine, ParameterizedStrategy

    FakeBacktestEngine.calls = []
    optimizer = StrategyOptimizer(minimum_trade_count=0, backtest_engine_class=FakeBacktestEngine)
    result = optimizer.walk_forward(ParameterizedStrategy, {"value": [1, 2]}, list(range(30)),
                                    train_size=10, validation_size=4, step_size=4, export=False)
    final_calls = [parameters for parameters, data in FakeBacktestEngine.calls if data == tuple(range(24, 30))]
    assert final_calls == [result.final_parameters["value"]]


def test_parameter_stability_checks_numeric_neighbors_on_training_data():
    from tests.test_optimizer import FakeBacktestEngine, ParameterizedStrategy

    optimizer = StrategyOptimizer(minimum_trade_count=0, backtest_engine_class=FakeBacktestEngine)
    stability = optimizer.parameter_stability(ParameterizedStrategy, {"value": 20}, list(range(10)))
    checked = {item["parameters"]["value"] for item in stability["neighbors"]}
    assert checked == {18, 22}
    assert stability["parameter_stability"] == 1.0
