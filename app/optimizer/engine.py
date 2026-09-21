from dataclasses import dataclass
from pathlib import Path

from app.backtest.engine import BacktestEngine
from app.optimizer.metrics import BacktestMetrics, WeightedScore
from app.optimizer.parameter_grid import ParameterGrid
from app.optimizer.result import CombinationResult, OptimizationResult


@dataclass(frozen=True)
class DatasetSplit:
    train: list
    validation: list
    test: list


class StrategyOptimizer:
    def __init__(
        self,
        backtest_config=None,
        scoring_weights=None,
        minimum_trade_count=1,
        top_n=10,
        output_dir=None,
        backtest_engine_class=BacktestEngine,
    ):
        if top_n < 1:
            raise ValueError("top_n must be at least one")
        self.backtest_config = dict(backtest_config or {})
        self.scoring = WeightedScore(scoring_weights, minimum_trade_count)
        self.top_n = top_n
        project_root = Path(__file__).resolve().parents[2]
        self.output_dir = Path(output_dir or project_root / "data" / "optimization")
        self.backtest_engine_class = backtest_engine_class

    @staticmethod
    def split_data(candles, train_fraction=0.60, validation_fraction=0.20):
        candle_list = list(candles)
        if len(candle_list) < 3:
            raise ValueError("At least three candles are required for train/validation/test splitting")
        if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1 or train_fraction + validation_fraction >= 1:
            raise ValueError("Split fractions must be positive and leave data for the test set")
        train_end = max(1, int(len(candle_list) * train_fraction))
        validation_end = max(train_end + 1, train_end + int(len(candle_list) * validation_fraction))
        validation_end = min(validation_end, len(candle_list) - 1)
        return DatasetSplit(candle_list[:train_end], candle_list[train_end:validation_end], candle_list[validation_end:])

    def optimize(
        self,
        strategy_class,
        parameter_grid,
        candles,
        backtest_config=None,
        symbol="BTCUSDT",
        interval="1h",
        train_fraction=0.60,
        validation_fraction=0.20,
        export=True,
        print_report=True,
    ):
        data_split = self.split_data(candles, train_fraction, validation_fraction)
        config = {**self.backtest_config, **(backtest_config or {}), "symbol": symbol}
        tested = []
        for parameters in ParameterGrid(parameter_grid).combinations():
            metrics = self._run(strategy_class, parameters, data_split.train, config)
            tested.append(CombinationResult(parameters, metrics, self.scoring.calculate(metrics)))
        tested.sort(key=lambda result: result.score, reverse=True)
        if tested[0].score == float("-inf"):
            raise ValueError("No parameter combination meets the minimum trade count")
        best = tested[0]
        validation_metrics = self._run(strategy_class, best.parameters, data_split.validation, config)
        test_metrics = self._run(strategy_class, best.parameters, data_split.test, config)
        result = OptimizationResult(
            strategy_name=strategy_class.__name__,
            best_parameters=best.parameters,
            train_metrics=best.train_metrics,
            validation_metrics=validation_metrics,
            test_metrics=test_metrics,
            score=best.score,
            tested_combinations=tested,
            top_combinations=tested[:self.top_n],
        )
        if export:
            filename = f"{strategy_class.__name__.replace('Strategy', '').lower()}_{symbol.upper()}_{interval}"
            result.export_csv(self.output_dir / f"{filename}.csv")
            result.export_json(self.output_dir / f"{filename}.json")
        if print_report:
            result.print_report()
        return result

    def _run(self, strategy_class, parameters, candles, config):
        strategy = strategy_class(**parameters)
        engine = self.backtest_engine_class(strategy=strategy, **config)
        return BacktestMetrics.from_statistics(engine.run(candles, print_report=False))
