import csv
import json
from collections import Counter
from pathlib import Path

from app.backtest.engine import BacktestEngine
from app.core.historical_data import HistoricalDataProvider
from app.optimizer.metrics import BacktestMetrics
from app.paper.experiments import batch_2_experiments
from app.paper.runner import STRATEGIES


RESEARCH_START = "2023-01-01"
RESEARCH_END = "2025-12-31"


def data_quality(candles, interval):
    expected = {"15m": 900, "1h": 3600, "4h": 14400}[interval]
    timestamps = [candle.open_time for candle in candles]
    gaps = [int((later - earlier).total_seconds() / expected) - 1 for earlier, later in zip(timestamps, timestamps[1:])]
    return {"candles": len(candles), "ascending": timestamps == sorted(timestamps),
            "duplicates": len(timestamps) - len(set(timestamps)), "missing_candles": sum(max(0, gap) for gap in gaps)}


def run_batch2_research(provider=None, output_dir=None):
    provider = provider or HistoricalDataProvider()
    output = Path(output_dir or Path(__file__).resolve().parents[2] / "data" / "research" / "batch_2")
    output.mkdir(parents=True, exist_ok=True)
    cache, quality, rows = {}, {}, []
    for experiment in batch_2_experiments():
        print(f"Evaluating {experiment.symbol} {experiment.timeframe} {experiment.strategy}", flush=True)
        key = (experiment.symbol, experiment.timeframe)
        if key not in cache:
            cache[key] = provider.get_history(*key, RESEARCH_START, RESEARCH_END)
            quality[f"{key[0]}_{key[1]}"] = data_quality(cache[key], key[1])
        candles = cache[key]
        split = int(len(candles) * 0.7)
        strategy_class = STRATEGIES[experiment.strategy]
        lookback = 60 if experiment.strategy == "SupertrendStrategy" else 120
        config = dict(symbol=experiment.symbol, slippage=experiment.slippage, position_size=experiment.position_size,
                      max_position_fraction=experiment.max_position_fraction,
                      periods_per_year=365 * {"15m": 96, "1h": 24, "4h": 6}[experiment.timeframe], indicator_lookback=lookback)
        train = BacktestMetrics.from_statistics(BacktestEngine(strategy_class(**experiment.strategy_parameters), **config).run(candles[:split], False))
        validation = BacktestMetrics.from_statistics(BacktestEngine(strategy_class(**experiment.strategy_parameters), **config).run(candles[split:], False, candles[max(0, split - lookback):split]))
        rows.append({"experiment_id": experiment.experiment_id, "symbol": experiment.symbol, "timeframe": experiment.timeframe,
                     "strategy": experiment.strategy, "parameters": dict(experiment.strategy_parameters),
                     "train": train.as_dict(), "validation": validation.as_dict()})
    (output / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (output / "data_quality.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=("experiment_id", "symbol", "timeframe", "strategy", "validation_return", "validation_drawdown", "validation_trades"))
        writer.writeheader()
        for row in rows:
            writer.writerow({"experiment_id": row["experiment_id"], "symbol": row["symbol"], "timeframe": row["timeframe"], "strategy": row["strategy"],
                             "validation_return": row["validation"]["total_return"], "validation_drawdown": row["validation"]["max_drawdown"], "validation_trades": row["validation"]["number_of_trades"]})
    methodology = "# Batch 2 research\n\nDevelopment: 2023-01-01 through 2025-12-31. First 70% train, final 30% chronological validation with warm-up. No M6.5 final holdout or 2026 candles are used for selection.\n"
    (output / "summary.md").write_text(methodology, encoding="utf-8")
    write_selection(rows, output / "selected_experiments.json")
    return rows


def write_selection(rows, path):
    selected = [row for row in rows if row["validation"]["number_of_trades"] >= 3 and row["validation"]["max_drawdown"] <= 10]
    Path(path).write_text(json.dumps(selected, indent=2), encoding="utf-8")
    return selected


if __name__ == "__main__":
    print(f"Evaluated {len(run_batch2_research())} Batch 2 configurations")
