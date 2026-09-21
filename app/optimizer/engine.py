from dataclasses import dataclass
import csv
import json
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


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    best_parameters: dict
    train_metrics: BacktestMetrics
    validation_metrics: BacktestMetrics


@dataclass
class WalkForwardResult:
    windows: list
    final_parameters: dict
    test_metrics: BacktestMetrics
    parameter_stability: dict

    def export_json(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "final_parameters": self.final_parameters,
            "test_metrics": self.test_metrics.as_dict(),
            "parameter_stability": self.parameter_stability,
            "windows": [
                {"train_start": window.train_start, "train_end": window.train_end,
                 "validation_start": window.validation_start, "validation_end": window.validation_end,
                 "best_parameters": window.best_parameters,
                 "train_metrics": window.train_metrics.as_dict(),
                 "validation_metrics": window.validation_metrics.as_dict()}
                for window in self.windows
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def export_csv(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=["train_start", "train_end", "validation_start", "validation_end", "best_parameters", "train_return", "validation_return"])
            writer.writeheader()
            for window in self.windows:
                writer.writerow({"train_start": window.train_start, "train_end": window.train_end,
                                 "validation_start": window.validation_start, "validation_end": window.validation_end,
                                 "best_parameters": json.dumps(window.best_parameters),
                                 "train_return": window.train_metrics.total_return,
                                 "validation_return": window.validation_metrics.total_return})
        return path


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
        validation_metrics = self._run(strategy_class, best.parameters, data_split.validation, config, data_split.train)
        test_metrics = self._run(strategy_class, best.parameters, data_split.test, config, data_split.train + data_split.validation)
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

    def _run(self, strategy_class, parameters, candles, config, warmup=()):
        strategy = strategy_class(**parameters)
        engine = self.backtest_engine_class(strategy=strategy, **config)
        if warmup and isinstance(engine, BacktestEngine):
            statistics = engine.run(candles, print_report=False, warmup_candles=warmup)
        else:
            statistics = engine.run(candles, print_report=False)
        return BacktestMetrics.from_statistics(statistics)

    @staticmethod
    def walk_forward_splits(candles, train_size, validation_size, step_size, anchored=False, test_fraction=0.20):
        values = list(candles)
        if train_size < 1 or validation_size < 1 or step_size < 1 or not 0 < test_fraction < 1:
            raise ValueError("Window sizes and step must be positive; test fraction must be between 0 and 1")
        test_start = int(len(values) * (1 - test_fraction))
        if train_size + validation_size > test_start:
            raise ValueError("Not enough pre-test candles for one walk-forward window")
        windows = []
        train_end = train_size
        while train_end + validation_size <= test_start:
            train_start = 0 if anchored else train_end - train_size
            windows.append((train_start, train_end, train_end, train_end + validation_size))
            train_end += step_size
        return windows, test_start

    def walk_forward(self, strategy_class, parameter_grid, candles, train_size, validation_size, step_size,
                     anchored=False, test_fraction=0.20, backtest_config=None, symbol="BTCUSDT", interval="1h",
                     export=True, stability_fraction=0.10):
        values = list(candles)
        splits, test_start = self.walk_forward_splits(values, train_size, validation_size, step_size, anchored, test_fraction)
        config = {**self.backtest_config, **(backtest_config or {}), "symbol": symbol}
        windows = []
        for train_start, train_end, validation_start, validation_end in splits:
            ranked = self._rank(strategy_class, parameter_grid, values[train_start:train_end], config)
            best = ranked[0]
            validation = self._run(strategy_class, best.parameters, values[validation_start:validation_end], config, values[:validation_start])
            windows.append(WalkForwardWindow(train_start, train_end, validation_start, validation_end,
                                             best.parameters, best.train_metrics, validation))
        final_ranked = self._rank(strategy_class, parameter_grid, values[:test_start], config)
        final_best = final_ranked[0]
        stability = self.parameter_stability(strategy_class, final_best.parameters, values[:test_start], config, stability_fraction)
        test_metrics = self._run(strategy_class, final_best.parameters, values[test_start:], config, values[:test_start])
        result = WalkForwardResult(windows, final_best.parameters, test_metrics, stability)
        if export:
            filename = f"{strategy_class.__name__.replace('Strategy', '').lower()}_{symbol.upper()}_{interval}_walk_forward"
            result.export_csv(self.output_dir / f"{filename}.csv")
            result.export_json(self.output_dir / f"{filename}.json")
        return result

    def _rank(self, strategy_class, parameter_grid, candles, config):
        ranked = []
        for parameters in ParameterGrid(parameter_grid).combinations():
            metrics = self._run(strategy_class, parameters, candles, config)
            ranked.append(CombinationResult(parameters, metrics, self.scoring.calculate(metrics)))
        ranked.sort(key=lambda item: item.score, reverse=True)
        if ranked[0].score == float("-inf"):
            raise ValueError("No parameter combination meets the minimum trade count")
        return ranked

    def parameter_stability(self, strategy_class, parameters, candles, config=None, fraction=0.10):
        if not 0 < fraction < 1:
            raise ValueError("fraction must be between 0 and 1")
        config = config or self.backtest_config
        baseline = self.scoring.calculate(self._run(strategy_class, parameters, candles, config))
        neighbors = []
        for name, value in parameters.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            for direction in (-1, 1):
                candidate = dict(parameters)
                candidate[name] = max(1, value + direction * max(1, round(value * fraction))) if isinstance(value, int) else value * (1 + direction * fraction)
                if candidate[name] == value:
                    continue
                try:
                    score = self.scoring.calculate(self._run(strategy_class, candidate, candles, config))
                except ValueError:
                    continue
                neighbors.append({"parameters": candidate, "score": score})
        finite_scores = [item["score"] for item in neighbors if item["score"] != float("-inf")]
        ratio = sum(score >= baseline * 0.8 for score in finite_scores) / len(finite_scores) if finite_scores and baseline > 0 else None
        return {"baseline_score": baseline, "neighbors": neighbors, "parameter_stability": ratio}
