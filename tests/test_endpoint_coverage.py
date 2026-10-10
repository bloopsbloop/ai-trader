from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.research.endpoint_coverage import (
    block_bootstrap,
    calendar_blocks,
    coverage_record,
    paired_four_hour,
    prepare_output,
)


def equity(start: str, periods: int, growth: float = 0.01) -> pd.DataFrame:
    times = pd.date_range(start, periods=periods, freq="4h", tz="UTC")
    values = 1000 * (1 + growth) ** np.arange(periods)
    return pd.DataFrame({"time": times, "equity": values})


def fills(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["fill_time", "commission", "slippage_cost"])


def test_midnight_start_has_no_initial_partial() -> None:
    record = coverage_record(equity("2025-01-01 00:00", 7))
    assert record["initial_partial_hours"] == 0
    assert record["terminal_partial_hours"] == 0
    assert record["full_daily_intervals"] == 1
    assert record["initial_boundary_return"] == 0
    assert record["terminal_boundary_return"] == 0


def test_noon_start_reports_initial_partial_and_costs() -> None:
    ledger = equity("2025-01-01 12:00", 10)
    trades = fills([("2025-01-01 12:00Z", 0.4, 0.5), ("2025-01-02 04:00Z", 0.2, 0.3)])
    record = coverage_record(ledger, trades)
    assert record["initial_partial_hours"] == 12
    assert record["initial_boundary_return"] == pytest.approx((1.01 ** 3) - 1)
    assert record["initial_boundary_costs"] == pytest.approx(0.9)
    assert record["initial_boundary_fill_count"] == 1


def test_terminal_partial_and_terminal_costs() -> None:
    ledger = equity("2025-01-01 00:00", 10)
    trades = fills([("2025-01-02 00:00Z", 0.4, 0.5), ("2025-01-02 12:00Z", 0.2, 0.3)])
    record = coverage_record(ledger, trades)
    assert record["terminal_partial_hours"] == 12
    assert record["terminal_boundary_return"] == pytest.approx((1.01 ** 3) - 1)
    assert record["terminal_boundary_costs"] == pytest.approx(1.4)
    assert record["terminal_boundary_fill_count"] == 2


def test_reconciliation_uses_each_boundary_once() -> None:
    record = coverage_record(equity("2025-01-01 12:00", 13))
    total_factor = 1 + record["full_window_return"]
    pieces = ((1 + record["initial_boundary_return"]) *
              (1 + record["full_day_compounded_return"]) *
              (1 + record["terminal_boundary_return"]))
    assert pieces == pytest.approx(total_factor, abs=1e-14)
    assert record["reconciliation_error"] == pytest.approx(0, abs=1e-14)


def test_boundary_mark_cost_is_not_double_counted() -> None:
    ledger = equity("2025-01-01 12:00", 7)
    trades = fills([("2025-01-02 00:00Z", 0.4, 0.5)])
    record = coverage_record(ledger, trades)
    assert record["initial_boundary_costs"] == 0
    assert record["terminal_boundary_costs"] == pytest.approx(0.9)
    assert record["initial_boundary_fill_count"] + record["terminal_boundary_fill_count"] == 1


def test_paired_coverage_requires_exact_intervals() -> None:
    left = equity("2025-01-01 12:00", 10)
    right = equity("2025-01-01 12:00", 10, 0.005)
    paired = paired_four_hour(left, right)
    assert len(paired) == 9
    assert (paired.duration_hours == 4).all()
    with pytest.raises(ValueError, match="exactly matching"):
        paired_four_hour(left, right.iloc[1:].reset_index(drop=True))


def test_calendar_blocks_use_elapsed_time_and_keep_partial_block() -> None:
    left = equity("2025-01-01 12:00", 200)
    right = equity("2025-01-01 12:00", 200, 0.005)
    blocks = calendar_blocks(paired_four_hour(left, right))
    assert blocks["count"].tolist() == [168, 31]
    assert blocks.complete_28d_block.tolist() == [True, False]
    result = block_bootstrap(blocks, repetitions=100, seed=42)
    assert result["linear_daily_x6"] == pytest.approx(result["mean_4h_difference"] * 6)


def test_output_overwrite_protection(tmp_path: Path) -> None:
    target = tmp_path / "audit"
    assert prepare_output(tmp_path, target) == target.resolve()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        prepare_output(tmp_path, target)
