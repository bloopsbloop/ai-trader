from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.research.absolute_trend import _require_aligned_daily, daily_returns
from app.research.risk_calibrated_benchmark import (
    INITIAL_CAPITAL,
    SYMBOLS,
    calibrate_lambda,
    prepare_output,
    run_passive,
    run_strategy,
    volatility_fraction,
)


def frames(rows: int = 420) -> dict[str, pd.DataFrame]:
    times = pd.date_range("2023-01-01", periods=rows, freq="4h", tz="UTC")
    x = np.arange(rows)
    result = {}
    for index, symbol in enumerate(SYMBOLS):
        close = 100 + index * 3 + x * (0.025 + index * 0.001) + 2.5 * np.sin(x / (8 + index))
        open_ = close * (1 + 0.001 * np.sin(x / 3))
        high = np.maximum(open_, close) * 1.003
        low = np.minimum(open_, close) * 0.997
        result[symbol] = pd.DataFrame({"open_time": times, "open": open_, "high": high,
                                       "low": low, "close": close, "volume": 1000 + x})
    return result


def test_volatility_fraction_bounds_and_blocking() -> None:
    assert volatility_fraction(0.0, 0.2) == 0.0
    assert volatility_fraction(0.1, 0.2) == 0.5
    assert volatility_fraction(0.3, 0.2) == 1.0
    for bad in (0.0, np.nan, np.inf):
        with pytest.raises(RuntimeError, match="METHODOLOGY_BLOCKED"):
            volatility_fraction(0.1, bad)


def test_calibration_is_train_only_under_future_mutation() -> None:
    original = frames()
    start, end = original[SYMBOLS[0]].open_time.iloc[[120, 300]]
    before = calibrate_lambda(original, start, end)
    changed = {symbol: frame.copy() for symbol, frame in original.items()}
    for frame in changed.values():
        future = frame.open_time > end
        frame.loc[future, ["open", "high", "low", "close"]] *= 50
    after = calibrate_lambda(changed, start, end)
    assert before == after


def test_passive_zero_and_one_endpoints_and_cash_conservation() -> None:
    data = frames(240)
    start, end = data[SYMBOLS[0]].open_time.iloc[[120, 220]]
    empty, cash = run_passive(data, start, end, 0.0, 0.0004, 0.0005)
    assert empty == []
    assert (cash.equity == INITIAL_CAPITAL).all()
    assert (cash.position_value == 0).all()
    assert (cash.cash == INITIAL_CAPITAL).all()
    results, full = run_passive(data, start, end, 1.0, 0.0004, 0.0005)
    assert full.equity.iloc[0] == pytest.approx(INITIAL_CAPITAL)
    assert full.position_value.iloc[-1] == 0
    assert np.allclose(full.equity, full.position_value + full.cash)
    assert all([fill["side"] for fill in result.fills] == ["BUY", "SELL"] for result in results)


def test_fraction_uses_actual_notional_costs_without_rebalance() -> None:
    data = frames(240)
    start, end = data[SYMBOLS[0]].open_time.iloc[[120, 220]]
    fraction = 0.37
    results, equity = run_passive(data, start, end, fraction, 0.0004, 0.0005)
    assert equity.equity.iloc[0] == pytest.approx(INITIAL_CAPITAL)
    assert equity.position_value.iloc[-1] == 0
    assert all(len(result.fills) == 2 and len(result.trades) == 1 for result in results)
    for result in results:
        assert result.state.commission == pytest.approx(sum(fill["notional"] * 0.0004 for fill in result.fills))
    assert sum(result.fills[0]["notional"] for result in results) < INITIAL_CAPITAL * fraction


def test_strategy_aggregate_preserves_cash_identity() -> None:
    data = frames(240)
    start, end = data[SYMBOLS[0]].open_time.iloc[[120, 220]]
    _, equity = run_strategy(data, start, end, 0.0004, 0.0005)
    inferred_cash = equity.equity - equity.position_value
    assert np.isfinite(inferred_cash).all()
    assert (inferred_cash >= -1e-10).all()


def test_protocol_boundaries_are_strictly_prior() -> None:
    protocol = pd.read_json(Path(__file__).parents[1] / "docs/M17_2_PROTOCOL.json", typ="series")
    main_end = pd.Timestamp(protocol["main"]["calibration"][1])
    assert main_end < pd.Timestamp(protocol["main"]["validation"][0])
    assert main_end < pd.Timestamp(protocol["main"]["test"][0])
    for fold in protocol["walk_forward"]:
        assert pd.Timestamp(fold["calibration"][1]) < pd.Timestamp(fold["evaluation"][0])


def test_paired_daily_alignment_rejects_missing_date() -> None:
    index = pd.date_range("2025-01-01", periods=5, freq="D", tz="UTC")
    left = pd.Series(np.arange(5), index=index)
    right = left.drop(index[2])
    with pytest.raises(ValueError, match="exactly matching"):
        _require_aligned_daily(left, right)


def test_daily_grid_and_missing_exact_boundary_fail() -> None:
    data = frames(240)
    start, end = data[SYMBOLS[0]].open_time.iloc[[120, 220]]
    _, equity = run_passive(data, start, end, 0.4, 0.0004, 0.0005)
    assert len(daily_returns(equity)) > 1
    with pytest.raises(ValueError, match="exact 4h boundaries"):
        run_passive(data, start + pd.Timedelta(hours=1), end, 0.4, 0.0004, 0.0005)


def test_output_overwrite_protection(tmp_path: Path) -> None:
    target = tmp_path / "artifacts"
    assert prepare_output(tmp_path, target) == target.resolve()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        prepare_output(tmp_path, target)
