from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any, Iterable


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def calculate_drawdown(equity_curve: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    peak = 0.0
    result = []
    for point in equity_curve:
        equity = float(point.get("equity", 0.0))
        peak = max(peak, equity)
        drawdown = (equity / peak - 1.0) * 100 if peak else 0.0
        result.append({"time": point.get("time"), "drawdown": drawdown})
    return result


def monthly_returns(equity_curve: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    months: dict[str, tuple[float, float]] = {}
    for point in equity_curve:
        timestamp = point.get("time")
        if not timestamp:
            continue
        month = datetime.fromisoformat(timestamp).strftime("%Y-%m")
        equity = float(point.get("equity", 0.0))
        if month not in months:
            months[month] = (equity, equity)
        else:
            months[month] = (months[month][0], equity)
    return [
        {"month": month, "return": round((end / start - 1.0) * 100, 10) if start else 0.0}
        for month, (start, end) in sorted(months.items())
    ]


def closed_trade_rows(trades: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    entry = None
    rows = []
    for trade in trades:
        if trade.get("side") == "BUY":
            entry = trade
        elif trade.get("side") == "SELL" and entry is not None:
            started = datetime.fromisoformat(entry["timestamp"])
            ended = datetime.fromisoformat(trade["timestamp"])
            rows.append({
                "Entry": entry["timestamp"], "Exit": trade["timestamp"],
                "PnL": float(trade.get("realized_pnl", 0.0)),
                "Fees": float(entry.get("commission", 0.0)) + float(trade.get("commission", 0.0)),
                "Duration": str(ended - started), "Duration hours": (ended - started).total_seconds() / 3600,
                "Signal": "Strategy SELL", "Entry price": entry.get("price"), "Exit price": trade.get("price"),
            })
            entry = None
    return rows


def alerts(metrics: dict[str, Any], flags: Iterable[str] = ()) -> list[str]:
    warnings = list(flags)
    if int(metrics.get("trades", metrics.get("number_of_trades", 0))) < 5:
        warnings.append("too_few_trades")
    if float(metrics.get("max_drawdown", 0.0)) > 20:
        warnings.append("high_drawdown")
    if float(metrics.get("expectancy", 0.0)) < 0:
        warnings.append("negative_expectancy")
    return sorted(set(warnings))


def load_research_rows(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or project_root() / "data" / "research"
    rows = []
    for path in sorted(root.glob("*_*/report.json")):
        report = __import__("json").loads(path.read_text(encoding="utf-8"))
        benchmark = report.get("benchmark", {})
        for strategy in report.get("strategies", []):
            metrics = strategy.get("test_metrics", {})
            rows.append({
                "symbol": report.get("symbol"), "timeframe": report.get("timeframe"),
                "strategy": strategy.get("strategy"), "test_return": metrics.get("total_return", 0.0),
                "test_max_drawdown": metrics.get("max_drawdown", 0.0),
                "test_sharpe": metrics.get("sharpe_ratio", 0.0),
                "test_profit_factor": metrics.get("profit_factor", 0.0),
                "test_expectancy": metrics.get("expectancy", 0.0),
                "trades": metrics.get("number_of_trades", 0),
                "parameter_stability": strategy.get("parameter_stability"),
                "overfitting_flags": strategy.get("overfitting_flags", []),
                "buy_hold_return": benchmark.get("return", 0.0),
                "buy_hold_max_drawdown": benchmark.get("max_drawdown", 0.0),
                "buy_hold_sharpe": benchmark.get("sharpe", 0.0),
                "walk_forward_returns": strategy.get("walk_forward_validation_returns", []),
                "parameters": strategy.get("final_parameters", {}),
            })
    return rows


def optimizer_rows(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or project_root() / "data" / "research"
    results = []
    for path in root.glob("*_*/optimization/*.json"):
        try:
            payload = __import__("json").loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for row in payload.get("tested_combinations", []):
            score = row.get("score", 0.0)
            if isfinite(score):
                results.append({**row, "source": str(path)})
    return sorted(results, key=lambda item: item.get("score", 0.0), reverse=True)


def parameter_heatmap(rows: Iterable[dict[str, Any]]) -> dict[tuple[Any, Any], float]:
    rows = list(rows)
    parameter_names = sorted({key for row in rows for key in row.get("parameters", {})})
    if len(parameter_names) < 2:
        return {}
    first, second = parameter_names[:2]
    values: dict[tuple[Any, Any], list[float]] = defaultdict(list)
    for row in rows:
        parameters = row.get("parameters", {})
        if first in parameters and second in parameters:
            values[(parameters[first], parameters[second])].append(float(row.get("score", 0.0)))
    return {key: sum(scores) / len(scores) for key, scores in values.items()}
