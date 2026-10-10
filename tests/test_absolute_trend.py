import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from app.research.absolute_trend import (BAR, _prepare_output, _window_return, aggregate,
                                         block_summaries, buy_and_hold, channel_signals, daily_returns,
                                         drawdown_metrics, paired_block_bootstrap, simulate_sleeve,
                                         validate_history)


def candles(n=130, gap_at=None, start="2024-01-01"):
    times = pd.date_range(start, periods=n, freq="4h", tz="UTC").tolist()
    if gap_at is not None:
        times[gap_at:] = [time + BAR for time in times[gap_at:]]
    close = [100.0] * n
    return pd.DataFrame({"open_time": times, "open": close, "high": close, "low": close,
                         "close": close, "volume": [1.0] * n})


def combine(*results):
    combined = type(results[0])(results[-1].state)
    for result in results:
        combined.equity += result.equity; combined.signals += result.signals
        combined.fills += result.fills; combined.trades += result.trades
    return combined


def assert_same_events(whole, chunked):
    for field in ("signals", "fills", "trades", "equity"):
        assert getattr(chunked, field) == getattr(whole, field)
    assert chunked.state.cash == pytest.approx(whole.state.cash)
    assert chunked.state.qty == pytest.approx(whole.state.qty)


def test_channels_exclude_current_candle_and_future():
    frame = candles(125); frame.loc[120, ["open", "high", "close"]] = [101, 110, 101]
    reference = channel_signals(frame)
    assert reference.loc[120, "entry"]
    changed = frame.copy(); changed.loc[121:, "high"] = 10000
    pd.testing.assert_series_equal(reference.loc[:120, "entry"], channel_signals(changed).loc[:120, "entry"])


def test_close_signal_and_equity_timestamps_then_next_open_fill():
    frame = candles(123); frame.loc[120, "close"] = 101; frame.loc[121, "open"] = 102
    result = simulate_sleeve(frame, "X", 100, finalize=False)
    signal, fill = result.signals[0], result.fills[0]
    assert signal["bar_close_time"] == frame.loc[120, "open_time"] + BAR
    assert signal["signal_available_time"] == fill["fill_time"] == frame.loc[121, "open_time"]
    assert result.equity[1]["equity_mark_time"] == frame.loc[0, "open_time"] + BAR


def test_initial_equity_and_buy_hold_first_day_costs():
    frame = candles(7)
    result = buy_and_hold(frame, "X", 100, frame.open_time.iloc[0], .01, .01)
    assert result.equity[0]["equity"] == 100 and result.equity[0]["event"] == "initial"
    returns = daily_returns(aggregate([result]))
    assert len(returns) == 1 and returns.iloc[0] < 0
    assert [fill["side"] for fill in result.fills] == ["BUY", "SELL"]


def test_gap_cancels_pending_order():
    frame = candles(123, gap_at=121); frame.loc[120, "close"] = 101
    result = simulate_sleeve(frame, "X", 100, finalize=False)
    assert result.signals and not result.fills and result.state.pending is None


def test_costs_conservation_and_terminal_liquidation_once():
    frame = candles(123); frame.loc[120, "close"] = 101; frame.loc[121:, "open"] = 100
    result = simulate_sleeve(frame, "X", 100, commission=.01, slippage=.01)
    assert result.state.qty == 0 and result.state.cash >= 0
    assert len(result.fills) == 2 and sum(fill["terminal"] for fill in result.fills) == 1
    assert result.state.commission == pytest.approx(sum(fill["commission"] for fill in result.fills))


def test_mark_to_market_drawdown_includes_initial_capital():
    drawdown, duration = drawdown_metrics(pd.DataFrame({"equity": [100, 90, 120, 90, 110]}))
    assert drawdown == pytest.approx(-.25) and duration == 2


def test_window_boundaries_include_first_increment_and_reconcile_geometrically():
    times = pd.date_range("2024-01-01", periods=5, freq="4h", tz="UTC")
    equity = pd.DataFrame({"time": times, "equity": [100, 110, 99, 118.8, 130.68]})
    first = _window_return(equity, times[0], times[1] - BAR)
    second = _window_return(equity, times[1], times[3])
    assert first == pytest.approx(.1)
    assert (1 + first) * (1 + second) == pytest.approx(equity.equity.iloc[-1] / 100)


def test_open_position_carries_across_window_boundary_without_fill_or_fee():
    frame = candles(170); frame.loc[120, "close"] = 101
    frame.loc[140:145, "close"] = [102, 103, 104, 105, 106, 107]
    frame.loc[140:145, "high"] = frame.loc[140:145, "close"]
    result = simulate_sleeve(frame, "X", 100, finalize=False)
    fills_before = list(result.fills); fees_before = result.state.commission
    equity = pd.DataFrame(result.equity)
    boundary = frame.loc[140, "open_time"]
    assert _window_return(equity, boundary, frame.loc[145, "open_time"]) != 0
    assert result.fills == fills_before and result.state.commission == fees_before


def _chunk_case(frame, split):
    whole = simulate_sleeve(frame, "X", 100)
    first = simulate_sleeve(frame.iloc[:split], "X", 100, finalize=False)
    second = simulate_sleeve(frame.iloc[split:], "X", 100, state=first.state, finalize=True)
    assert_same_events(whole, combine(first, second))


def test_chunked_entry_immediately_after_boundary_and_short_first_chunk():
    frame = candles(150); frame.loc[120, "close"] = 101
    _chunk_case(frame, 120)


def test_chunked_pending_entry_crosses_boundary():
    frame = candles(150); frame.loc[120, "close"] = 101; frame.loc[121, "open"] = 102
    _chunk_case(frame, 121)


def test_chunked_exit_immediately_after_boundary():
    frame = candles(210); frame.loc[120:179, ["open", "high", "low", "close"]] = [101, 101, 101, 101]
    frame.loc[180, ["open", "high", "low", "close"]] = [99, 99, 99, 99]
    _chunk_case(frame, 180)


def test_chunked_gap_at_boundary_expires_pending():
    frame = candles(150, gap_at=121); frame.loc[120, "close"] = 101
    _chunk_case(frame, 121)


def test_overwrite_protection_for_old_and_filled_new_namespace(tmp_path):
    old = tmp_path / "data/research/m17_1_absolute_trend_v1"; old.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="immutable"):
        _prepare_output(tmp_path, old)
    new = tmp_path / "data/research/m17_1_review_corrections_v1"; new.mkdir()
    (new / "sentinel").write_text("keep")
    with pytest.raises(FileExistsError, match="refusing"):
        _prepare_output(tmp_path, new)
    assert (new / "sentinel").read_text() == "keep"


def test_paired_daily_alignment_is_strict_and_blocks_keep_partial_count():
    index = pd.date_range("2024-01-01", periods=16, freq="D", tz="UTC")
    left, right = pd.Series(.001, index=index), pd.Series(0.0, index=index)
    result = paired_block_bootstrap(left, right, 7, 100, 42)
    assert result["bootstrap_positive_fraction"] == 1
    assert [row["count"] for row in block_summaries(left, right, 7)] == [7, 7, 2]
    with pytest.raises(ValueError, match="matching"):
        paired_block_bootstrap(left, right.iloc[:-1], 7)


def test_history_validation_rejects_invalid_ohlc_and_reports_gaps():
    frame = candles(10, gap_at=5); assert validate_history(frame)["gap_count"] == 1
    frame.loc[3, "low"] = 101
    with pytest.raises(ValueError, match="bounds"):
        validate_history(frame)


def test_module_is_research_only():
    text = (Path(__file__).parents[1] / "app/research/absolute_trend.py").read_text(encoding="utf-8")
    assert "PaperBroker" not in text and "RiskManager" not in text


def test_frozen_artifacts_match_review_baseline_hashes():
    root = Path(__file__).parents[1]
    baseline = json.loads((root / "docs/M17_1R_BASELINE_HASHES.json").read_text(encoding="utf-8"))
    for section in ("ohlcv", "m17_1_original", "m16_2_key", "m17_1_protocol"):
        for relative, expected in baseline[section].items():
            assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
