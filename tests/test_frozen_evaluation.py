from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.research.absolute_trend import simulate_sleeve
from app.research.frozen_evaluation import (
    END,
    LAMBDA,
    START,
    WARMUP_START,
    assemble_strategy_frame,
    bootstrap,
    calendar_blocks,
    classify_claim,
    daily_reconciliation,
    overall_status,
    paired_difference,
    paths,
    require_absent,
    normalized_output,
    validate_archive_frame,
)


def candles(times: pd.DatetimeIndex, close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({"open_time": times, "open": close, "high": close + 1,
                         "low": close - 1, "close": close, "volume": 10.0})


def full_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    warm = candles(pd.date_range(WARMUP_START, START, freq="4h", inclusive="left"))
    evaluation = candles(pd.date_range(START, END, freq="4h", inclusive="left"))
    return warm, evaluation


def test_exactly_120_warmup_and_frozen_evaluation_grid() -> None:
    warm, evaluation = full_frames()
    combined = assemble_strategy_frame(warm, evaluation)
    assert len(warm) == 120 and len(evaluation) == 1638 and len(combined) == 1758
    with pytest.raises(ValueError, match="exactly the frozen 120"):
        assemble_strategy_frame(warm.iloc[1:], evaluation)


def test_flat_start_does_not_inherit_warmup_pending_signal() -> None:
    warm, evaluation = full_frames()
    warm.loc[warm.index[-1], ["open", "high", "low", "close"]] = [100, 151, 99, 150]
    frame = pd.concat([warm, evaluation.iloc[:2]], ignore_index=True)
    result = simulate_sleeve(frame, "BTCUSDT", 1000, 0.0004, 0.0005,
                             trade_start=START, trade_end=START + pd.Timedelta(hours=4))
    assert result.equity[0]["time"] == START
    assert result.fills == []
    assert result.signals == []


def test_first_signal_and_fill_follow_availability() -> None:
    warm, evaluation = full_frames()
    evaluation = evaluation.iloc[:2].copy()
    evaluation.loc[evaluation.index[0], ["open", "high", "low", "close"]] = [100, 111, 99, 110]
    evaluation.loc[evaluation.index[1], ["open", "high", "low", "close"]] = [112, 113, 108, 111]
    frame = pd.concat([warm, evaluation], ignore_index=True)
    result = simulate_sleeve(frame, "BTCUSDT", 1000, 0.0004, 0.0005,
                             trade_start=START, trade_end=START + pd.Timedelta(hours=4))
    assert result.signals[0]["signal_available_time"] == START + pd.Timedelta(hours=4)
    assert result.fills[0]["fill_time"] == START + pd.Timedelta(hours=4)
    assert result.fills[0]["side"] == "BUY"
    assert result.fills[-1]["side"] == "SELL" and result.fills[-1]["terminal"] is True
    assert result.state.commission == pytest.approx(sum(x["notional"] * 0.0004 for x in result.fills))


def test_daily_factor_reconciliation_covers_all_273_days() -> None:
    times = pd.date_range(START, END, freq="4h")
    values = 1000 * np.exp(np.linspace(0, 0.1, len(times)))
    equity = pd.DataFrame({"time": times, "equity": values,
                           "position_value": values, "exposure": 1.0})
    daily, error = daily_reconciliation(equity)
    assert len(daily) == 273
    assert daily.index[0] == pd.Timestamp("2026-01-02T00:00:00Z")
    assert daily.index[-1] == END
    assert error == pytest.approx(0, abs=1e-12)


def test_paired_endpoints_must_match_exactly() -> None:
    index = pd.date_range("2026-01-02", periods=5, freq="D", tz="UTC")
    left = pd.Series(np.arange(5) / 100, index=index)
    right = pd.Series(0.0, index=index)
    assert paired_difference(left, right).index.equals(index)
    with pytest.raises(ValueError, match="match exactly"):
        paired_difference(left, right.iloc[1:])


def test_calendar_blocks_and_registered_quantiles() -> None:
    index = pd.date_range(START + pd.Timedelta(days=1), END, freq="D")
    difference = pd.Series(np.sin(np.arange(len(index))) / 1000, index=index)
    blocks = calendar_blocks(difference, 28)
    assert len(blocks) == 10
    assert blocks.iloc[:9]["count"].tolist() == [28] * 9
    assert blocks.iloc[-1]["count"] == 21 and not bool(blocks.iloc[-1]["complete"])
    result = bootstrap(blocks, repetitions=5000, seed=42)
    assert result["ci_97_5_lower"] <= result["ci_95_lower"]
    assert result["ci_97_5_upper"] >= result["ci_95_upper"]


def test_lambda_and_decision_precedence_are_frozen() -> None:
    assert LAMBDA == 0.5923903262274034
    a = classify_claim("A", 0.001, 0.0001, 0.05)
    b = classify_claim("B", 0.001, 0.0001, 0.05)
    assert overall_status(a, b, 0.001, 0.001, 0.02, 0.0002) == "PROMISING_CANDIDATE_UNCERTAIN_PROVENANCE"
    assert overall_status(a, b, 0.001, 0.001, 0.02, 0.0002, "DATA_QUALITY_BLOCKED") == "DATA_QUALITY_BLOCKED"
    assert overall_status(a, "INCONCLUSIVE_INCREMENTAL", 0.001, -0.0001, 0.02, 0.0002) == "NO_EVIDENCE_FOR_COMBINED_HYPOTHESIS"


def test_microsecond_archive_schema_and_close_relation() -> None:
    opens = pd.date_range(START, periods=2, freq="4h")
    open_us = np.array([stamp.value // 1000 for stamp in opens], dtype="int64")
    frame = pd.DataFrame({
        0: open_us, 1: [100, 101], 2: [102, 103], 3: [99, 100], 4: [101, 102], 5: [10, 11],
        6: open_us + 14_400_000_000 - 1, 7: [1000, 1100], 8: [5, 6],
        9: [4, 5], 10: [400, 500], 11: [0, 0],
    })
    frame.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                     "quote_asset_volume", "number_of_trades", "taker_buy_base_asset_volume",
                     "taker_buy_quote_asset_volume", "ignore"]
    valid = validate_archive_frame(frame, "synthetic")
    assert valid.open_time_utc.tolist() == list(opens)
    normalized = normalized_output(valid)
    assert normalized.columns.is_unique
    assert normalized.columns.tolist() == ["open_time", "open", "high", "low", "close", "volume",
                                           "close_time", "quote_asset_volume", "number_of_trades",
                                           "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]
    assert normalized.open_time.tolist() == list(opens)
    broken = frame.copy(); broken.loc[0, "open_time"] //= 1000
    with pytest.raises(ValueError, match="microsecond"):
        validate_archive_frame(broken, "broken")


def test_evaluation_and_acquisition_namespaces_are_overwrite_protected(tmp_path: Path) -> None:
    target = paths(tmp_path)
    target["holdout"].mkdir(parents=True)
    target["evaluation"].mkdir(parents=True)
    with pytest.raises(FileExistsError, match="refusing to overwrite acquisition"):
        require_absent(target["holdout"], "acquisition namespace")
    with pytest.raises(FileExistsError, match="refusing to overwrite one-time"):
        require_absent(target["evaluation"], "one-time evaluation")
