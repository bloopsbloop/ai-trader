from datetime import datetime, timezone

from app.dashboard.analytics import alerts, calculate_drawdown, closed_trade_rows, monthly_returns, parameter_heatmap
from app.dashboard.exports import csv_bytes, html_report, json_bytes


def test_equity_analytics_calculates_drawdown_and_monthly_returns():
    curve = [
        {"time": "2024-01-01T00:00:00+00:00", "equity": 100},
        {"time": "2024-01-15T00:00:00+00:00", "equity": 120},
        {"time": "2024-02-01T00:00:00+00:00", "equity": 90},
        {"time": "2024-02-28T00:00:00+00:00", "equity": 99},
    ]
    assert calculate_drawdown(curve)[2]["drawdown"] == -25
    assert monthly_returns(curve) == [{"month": "2024-01", "return": 20.0}, {"month": "2024-02", "return": 10.0}]


def test_trade_rows_pair_entries_with_exits_and_sum_fees():
    rows = closed_trade_rows([
        {"timestamp": "2024-01-01T00:00:00+00:00", "side": "BUY", "price": 100, "commission": 1},
        {"timestamp": "2024-01-01T03:00:00+00:00", "side": "SELL", "price": 110, "commission": 2, "realized_pnl": 7},
    ])
    assert rows[0]["PnL"] == 7
    assert rows[0]["Fees"] == 3
    assert rows[0]["Duration hours"] == 3


def test_alerts_and_optimizer_heatmap_are_diagnostic_and_configurable():
    assert alerts({"trades": 2, "max_drawdown": 25, "expectancy": -1}) == ["high_drawdown", "negative_expectancy", "too_few_trades"]
    heatmap = parameter_heatmap([
        {"parameters": {"fast": 10, "slow": 50}, "score": 1},
        {"parameters": {"fast": 10, "slow": 50}, "score": 3},
    ])
    assert heatmap == {(10, 50): 2}


def test_exports_are_downloadable_text_payloads():
    rows = [{"Entry": "2024-01-01", "PnL": 4}]
    assert b"Entry" in csv_bytes(rows)
    assert b"metrics" in json_bytes({"metrics": {"PnL": 4}})
    assert b"<table" in html_report("Report", {"PnL": 4}, rows)
