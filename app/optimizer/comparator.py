import csv
import json
from dataclasses import dataclass
from pathlib import Path

from app.backtest.engine import BacktestEngine
from app.optimizer.metrics import BacktestMetrics, WeightedScore


@dataclass(frozen=True)
class StrategyConfiguration:
    strategy_class: type
    parameters: dict


@dataclass(frozen=True)
class ComparisonRow:
    strategy: str
    parameters: dict
    metrics: BacktestMetrics
    score: float


class ComparisonResult:
    def __init__(self, rows):
        self.rows = rows

    def format_table(self):
        header = "Strategy             Return%  Drawdown%  Sharpe  PF     Win%  Expectancy  Trades  Score"
        lines = [header, "-" * len(header)]
        for row in self.rows:
            metrics = row.metrics
            lines.append(f"{row.strategy:<20} {metrics.total_return:>7.2f}  {metrics.max_drawdown:>9.2f}  {metrics.sharpe_ratio:>6.2f}  {metrics.profit_factor:>5.2f}  {metrics.win_rate:>5.1f}  {metrics.expectancy:>10.2f}  {metrics.number_of_trades:>6}  {row.score:>6.2f}")
        return "\n".join(lines)

    def export_json(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{"strategy": row.strategy, "parameters": row.parameters,
                                     "metrics": row.metrics.as_dict(), "score": row.score} for row in self.rows], indent=2), encoding="utf-8")
        return path

    def export_csv(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=["strategy", "parameters", "score", *BacktestMetrics.__dataclass_fields__])
            writer.writeheader()
            for row in self.rows:
                writer.writerow({"strategy": row.strategy, "parameters": json.dumps(row.parameters), "score": row.score, **row.metrics.as_dict()})
        return path


class StrategyComparator:
    def __init__(self, backtest_config=None, scoring_weights=None, minimum_trade_count=1, output_dir=None):
        self.backtest_config = dict(backtest_config or {})
        self.scoring = WeightedScore(scoring_weights, minimum_trade_count)
        self.output_dir = Path(output_dir or Path(__file__).resolve().parents[2] / "data" / "comparisons")

    def compare(self, strategies, candles, symbol="BTCUSDT", interval="1h", warmup_candles=(), export=True, print_report=True):
        values = list(candles)
        warmup = list(warmup_candles)
        rows = []
        for configuration in strategies:
            if isinstance(configuration, type):
                configuration = StrategyConfiguration(configuration, {})
            elif isinstance(configuration, tuple):
                configuration = StrategyConfiguration(*configuration)
            strategy = configuration.strategy_class(**configuration.parameters)
            engine = BacktestEngine(strategy, **{**self.backtest_config, "symbol": symbol})
            statistics = engine.run(values, print_report=False, warmup_candles=warmup)
            metrics = BacktestMetrics.from_statistics(statistics)
            rows.append(ComparisonRow(configuration.strategy_class.__name__, configuration.parameters,
                                      metrics, self.scoring.calculate(metrics)))
        result = ComparisonResult(rows)
        if export:
            filename = f"{symbol.upper()}_{interval}_comparison"
            result.export_csv(self.output_dir / f"{filename}.csv")
            result.export_json(self.output_dir / f"{filename}.json")
        if print_report:
            print(result.format_table())
        return result
