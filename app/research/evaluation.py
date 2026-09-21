import csv
import json
from datetime import date, timedelta
from math import sqrt
from pathlib import Path
from statistics import stdev

from app.core.historical_data import HistoricalDataProvider
from app.optimizer import StrategyOptimizer
from app.strategies.ema_strategy import EMAStrategy
from app.strategies.rsi_strategy import RSIStrategy
from app.strategies.ema_rsi_strategy import EMARSIStrategy
from app.strategies.ema_rsi_atr_strategy import EMARSIATRStrategy


PROTOCOL = {
    "EMAStrategy": (EMAStrategy, {"fast": [10, 20], "slow": [50, 80]}),
    "RSIStrategy": (RSIStrategy, {"rsi_period": [7, 14], "oversold": [30], "overbought": [70]}),
    "EMARSIStrategy": (EMARSIStrategy, {"fast": [10, 20], "slow": [50], "rsi_period": [7], "oversold": [30], "overbought": [70]}),
    "EMARSIATRStrategy": (EMARSIATRStrategy, {"fast": [10, 20], "slow": [50], "rsi_period": [7], "oversold": [30], "overbought": [70], "atr_period": [14], "stop_atr_multiplier": [2.0], "take_profit_atr_multiplier": [3.0]}),
}


def buy_and_hold(candles, periods_per_year):
    prices = [candle.close for candle in candles]
    if len(prices) < 2:
        raise ValueError("Benchmark requires at least two candles")
    peak = prices[0]
    drawdown = 0.0
    for price in prices:
        peak = max(peak, price)
        drawdown = max(drawdown, (peak - price) / peak * 100)
    returns = [current / previous - 1 for previous, current in zip(prices, prices[1:])]
    volatility = stdev(returns) if len(returns) > 1 else 0.0
    sharpe = sum(returns) / len(returns) / volatility * sqrt(periods_per_year) if volatility else 0.0
    return {"return": (prices[-1] / prices[0] - 1) * 100, "max_drawdown": drawdown, "sharpe": sharpe}


def diagnostic_flags(train, validation, test, stability):
    flags = []
    if train.total_return > 0 and validation.total_return < 0:
        flags.append("train_good_validation_bad")
    if validation.total_return > 0 and test.total_return < 0:
        flags.append("validation_good_test_bad")
    if test.number_of_trades < 5:
        flags.append("too_few_trades")
    if stability is None or stability < 0.5:
        flags.append("unstable_parameters")
    if test.max_drawdown > 20:
        flags.append("excessive_drawdown")
    if abs(test.sharpe_ratio) > 4:
        flags.append("suspiciously_high_sharpe")
    return flags


def market_regimes(candles, boundary):
    periods = {
        "2021_bull": (date(2021, 1, 1), date(2021, 12, 31)),
        "2022_bear": (date(2022, 1, 1), date(2022, 12, 31)),
        "2023_2024_recovery": (date(2023, 1, 1), date(2024, 12, 31)),
        "2025_recent": (date(2025, 1, 1), boundary),
    }
    result = {}
    for name, (start, end) in periods.items():
        selected = [candle for candle in candles if start <= candle.open_time.date() <= end]
        if len(selected) > 1:
            result[name] = {"start": selected[0].open_time.isoformat(), "end": selected[-1].open_time.isoformat(),
                            "market_return": (selected[-1].close / selected[0].close - 1) * 100}
    return result


def evaluate_dataset(symbol, interval, candles, root):
    if len(candles) < 365 * (24 if interval == "1h" else 6) * 3:
        raise ValueError(f"Fewer than three years of candles for {symbol} {interval}")
    dataset_dir = root / f"{symbol}_{interval}"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    periods_per_year = 365 * (24 if interval == "1h" else 6)
    config = {"start_balance": 1000, "position_size": 0.20, "max_position_fraction": 0.20,
              "maker_commission": 0.0002, "taker_commission": 0.0004, "slippage": 0.0005,
              "periods_per_year": periods_per_year}
    config["indicator_lookback"] = 1000
    test_start = int(len(candles) * 0.8)
    development = candles[:test_start]
    holdout = candles[test_start:]
    benchmark = buy_and_hold(holdout, periods_per_year)
    regimes = market_regimes(candles, holdout[0].open_time.date() - timedelta(days=1))
    expected_gap = timedelta(hours=1 if interval == "1h" else 4)
    gaps = sum(later.open_time - earlier.open_time > expected_gap for earlier, later in zip(candles, candles[1:]))
    train_size = int(len(development) * 0.5)
    validation_size = int(len(development) * 0.25)
    rows = []
    for name, (strategy, grid) in PROTOCOL.items():
        optimizer = StrategyOptimizer(backtest_config=config, minimum_trade_count=0,
                                      output_dir=dataset_dir / "optimization")
        development_result = optimizer.optimize(strategy, grid, development, symbol=symbol, interval=interval,
                                                export=True, print_report=False)
        walk = optimizer.walk_forward(strategy, grid, candles, train_size, validation_size,
                                      validation_size, symbol=symbol, interval=interval, export=True,
                                      stability_parameters={key for key, values in grid.items() if len(values) > 1})
        validation_scores = [window.validation_metrics.total_return for window in walk.windows]
        stability = walk.parameter_stability["parameter_stability"]
        flags = diagnostic_flags(development_result.train_metrics, development_result.validation_metrics,
                                 walk.test_metrics, stability)
        midpoint = test_start + len(holdout) // 2
        regime_metrics = {}
        for label, start_index, end_index in (("holdout_first_half", test_start, midpoint),
                                               ("holdout_second_half", midpoint, len(candles))):
            segment = candles[start_index:end_index]
            regime_metrics[label] = {
                "market_return": (segment[-1].close / segment[0].close - 1) * 100,
                "strategy": optimizer._run(strategy, walk.final_parameters, segment, {**config, "symbol": symbol},
                                           candles[:start_index]).as_dict(),
            }
        row = {
            "symbol": symbol, "timeframe": interval, "strategy": name,
            "development_parameters": development_result.best_parameters,
            "final_parameters": walk.final_parameters,
            "train_metrics": development_result.train_metrics.as_dict(),
            "validation_metrics": development_result.validation_metrics.as_dict(),
            "walk_forward_validation_returns": validation_scores,
            "walk_forward_windows": len(walk.windows),
            "test_metrics": walk.test_metrics.as_dict(),
            "parameter_stability": stability,
            "neighbor_scores": walk.parameter_stability["neighbors"],
            "overfitting_flags": flags,
            "benchmark": benchmark,
            "posthoc_holdout_segments": regime_metrics,
        }
        rows.append(row)
    report = {"symbol": symbol, "timeframe": interval, "first_candle": candles[0].open_time.isoformat(),
              "last_candle": candles[-1].open_time.isoformat(), "candles": len(candles),
              "holdout_start": holdout[0].open_time.isoformat(), "holdout_end": holdout[-1].open_time.isoformat(),
              "gap_count": gaps,
              "benchmark": benchmark, "posthoc_market_regimes": regimes, "strategies": rows}
    (dataset_dir / "report.json").write_text(json.dumps(report, indent=2, allow_nan=True), encoding="utf-8")
    (dataset_dir / "summary.md").write_text(render_dataset_report(report), encoding="utf-8")
    return rows


def render_dataset_report(report):
    lines = [f"# {report['symbol']} {report['timeframe']}", "",
             f"Data: {report['first_candle']} to {report['last_candle']} ({report['candles']} candles)",
             f"Final holdout: {report['holdout_start']} to {report['holdout_end']}", "",
             f"Gaps between candles: {report['gap_count']}", "",
             "Buy & Hold on holdout (gross, no costs): " + json.dumps(report["benchmark"]), ""]
    for row in report["strategies"]:
        lines += [f"## {row['strategy']}", "", f"Final parameters: {row['final_parameters']}",
                  f"Parameter stability: {row['parameter_stability']}",
                  f"Flags: {', '.join(row['overfitting_flags']) or 'none'}", ""]
        for stage in ("train", "validation", "test"):
            lines += [f"### {stage.title()}", "", json.dumps(row[f"{stage}_metrics"], indent=2), ""]
        lines += ["Walk-forward validation returns: " + str(row["walk_forward_validation_returns"]), ""]
        lines += ["Posthoc holdout halves: " + json.dumps(row["posthoc_holdout_segments"]), ""]
    return "\n".join(lines)


def run_research(end_date=None, evaluation_start="2023-09-01"):
    end_date = end_date or date.today() - timedelta(days=1)
    provider = HistoricalDataProvider()
    root = Path(__file__).resolve().parents[2] / "data" / "research"
    root.mkdir(parents=True, exist_ok=True)
    summary = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for interval in ("1h", "4h"):
            history = provider.get_history(symbol, interval, "2021-01-01", end_date.isoformat())
            candles = [candle for candle in history if candle.open_time.date() >= date.fromisoformat(evaluation_start)]
            print(f"Evaluating {symbol} {interval}: {len(candles)} candles", flush=True)
            rows = evaluate_dataset(symbol, interval, candles, root)
            for row in rows:
                metrics = row["test_metrics"]
                summary.append({"symbol": symbol, "timeframe": interval, "strategy": row["strategy"],
                                "test_return": metrics["total_return"], "test_max_drawdown": metrics["max_drawdown"],
                                "test_sharpe": metrics["sharpe_ratio"], "test_profit_factor": metrics["profit_factor"],
                                "test_expectancy": metrics["expectancy"], "trades": metrics["number_of_trades"],
                                "parameter_stability": row["parameter_stability"],
                                "overfitting_flags": ";".join(row["overfitting_flags"])})
            print(f"Completed {symbol} {interval}", flush=True)
    fieldnames = list(summary[0])
    with (root / "summary.csv").open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary)
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    run_research()
