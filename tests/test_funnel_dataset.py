from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ml.funnel_dataset import (
    ADDITIONAL_CANDIDATE_FEATURES,
    CANDIDATE_LABEL_COLUMNS,
    REGIME_FEATURE_COLUMNS,
    REGIME_TARGET_COLUMNS,
    add_candidate_features,
    add_candidate_labels,
    add_horizon_targets,
    baseline_funnel,
    build_regime_dataset,
    dispersion_analysis,
    horizon_diagnostic,
    validate_safety,
)


def _frame(hours=12):
    rows = []
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    start = pd.Timestamp("2025-01-01", tz="UTC")
    for hour in range(hours):
        stamp = start + pd.Timedelta(hours=hour)
        split = "train" if hour < 6 else "test"
        future = [0.012, 0.006, -0.002, 0.0005] if hour % 4 == 0 else (
            [0.004, 0.002, -0.001, -0.003] if hour % 4 == 1 else [-0.002, -0.003, -0.004, -0.005]
        )
        for i, symbol in enumerate(symbols):
            row = {
                "timestamp": stamp, "target_timestamp": stamp + pd.Timedelta(hours=4),
                "symbol": symbol, "split": split, "eligible_universe_size": len(symbols),
                "close": 100 + hour * 2 + i, "future_net_return_4h": future[i],
                "future_return_rank": int(pd.Series(future).rank(method="first", ascending=False).iloc[i]),
                "return_1_1h": .001 * (i - 1), "return_4h": .002 * i,
                "return_24h": .004 * i, "volatility_20_1h": .01 + .002 * i,
                "rsi_14_1h": 45 + i * 5, "atr_fraction_14_1h": .01,
                "return_rank_24h": (i + 1) / len(symbols),
                "supertrend_state_1h": 1 if i < 2 else 0,
                "ema_distance_20_1h": .01 if i < 2 else -.01,
                "volume_zscore_20_1h": i - 1.5, "momentum_4_1h": .003 * i,
                "distance_recent_high_20_1h": -.01 * i,
            }
            for tf in ("15m", "4h"):
                row[f"ema_distance_20_{tf}"] = .01 if (i + hour) % 2 == 0 else -.01
                row[f"momentum_4_{tf}"] = .002 * (i - 1)
                row[f"volume_zscore_20_{tf}"] = i - 1
            rows.append(row)
    return pd.DataFrame(rows)


def _candidate(hours=12):
    return add_candidate_labels(add_candidate_features(_frame(hours)))


def test_timestamp_level_regime_aggregation_and_labels():
    regime = build_regime_dataset(_candidate())
    assert len(regime) == 12
    assert regime.loc[0, "actual_best_symbol"] == "BTCUSDT"
    assert regime.loc[0, "regime_category"] == "STRONG"
    assert regime.loc[1, "regime_category"] == "WEAK"
    assert regime.loc[2, "regime_category"] == "NO_OPPORTUNITY"
    assert regime.loc[0, "best_minus_second"] == pytest.approx(.006)


def test_candidate_labels_and_empty_set():
    candidate = _candidate()
    first = candidate[candidate["timestamp"] == candidate["timestamp"].min()].set_index("symbol")
    assert first.loc["BTCUSDT", "candidate_strong"] == 1
    assert first.loc["ETHUSDT", "candidate_top_2"] == 1
    assert first["ideal_candidate"].sum() == 2
    no_opportunity = candidate[candidate["timestamp"] == candidate["timestamp"].sort_values().unique()[2]]
    assert no_opportunity["candidate_set_should_be_empty"].eq(1).all()
    assert no_opportunity["ideal_candidate"].sum() == 0


def test_trend_momentum_volume_alignment_are_point_in_time():
    candidate = add_candidate_features(_frame(1)).set_index("symbol")
    assert candidate.loc["BTCUSDT", "trend_alignment_score"] == 3
    assert candidate.loc["SOLUSDT", "momentum_alignment_score"] == 3
    assert candidate.loc["BNBUSDT", "volume_confirmation_score"] == 3
    assert set(ADDITIONAL_CANDIDATE_FEATURES).issubset(candidate.columns)


def test_group_safe_split_and_primary_purge_are_preserved():
    candidate = _candidate()
    regime = build_regime_dataset(candidate)
    assert candidate.groupby("timestamp")["split"].nunique().max() == 1
    assert regime.groupby("timestamp")["split"].nunique().max() == 1
    assert (candidate["target_timestamp"] - candidate["timestamp"]).eq(pd.Timedelta(hours=4)).all()


def test_feature_target_separation():
    candidate = _candidate()
    regime = build_regime_dataset(candidate)
    base_features = [c for c in candidate.columns if c.startswith("return_1_")]
    validation = validate_safety(candidate, regime, base_features + ADDITIONAL_CANDIDATE_FEATURES)
    assert validation["passed"]
    assert not set(REGIME_FEATURE_COLUMNS) & set(REGIME_TARGET_COLUMNS)
    assert not set(ADDITIONAL_CANDIDATE_FEATURES) & set(CANDIDATE_LABEL_COLUMNS)


def test_baseline_funnel_metrics_and_oracle():
    result = baseline_funnel(_candidate()).set_index("baseline")
    assert result.loc["all_assets", "winner_retained_rate"] == 1
    assert result.loc["all_assets", "average_candidate_set_size"] == 4
    assert result.loc["no_assets", "empty_set_rate"] == 1
    assert result.loc["oracle_winner_impossible", "winner_retained_rate"] == 1
    assert result.loc["oracle_winner_impossible", "impossible_future_oracle"]


def test_dispersion_analysis_uses_train_cut_points(tmp_path):
    regime = build_regime_dataset(_candidate())
    result = dispersion_analysis(regime, tmp_path / "missing.csv")
    assert set(result["metric"]) == {"best_minus_second", "best_minus_median", "future_cross_sectional_dispersion"}
    assert set(result["split"]) == {"train", "test"}


def test_multi_horizon_targets_require_exact_time_and_same_split():
    candidate = add_horizon_targets(_candidate(20))
    train_last = candidate[candidate["timestamp"] == pd.Timestamp("2025-01-01 05:00", tz="UTC")]
    assert train_last["future_net_return_2h"].isna().all()
    assert train_last["future_net_return_8h"].isna().all()
    diagnostic = horizon_diagnostic(candidate)
    assert set(diagnostic["horizon_hours"]) == {2, 4, 8}
    assert diagnostic["winner_persistence_vs_4h"].between(0, 1).all()


def test_changing_future_prices_changes_targets_not_features_at_t():
    original = _frame(9)
    original["split"] = "train"
    changed = original.copy()
    future_rows = changed["timestamp"] >= changed["timestamp"].min() + pd.Timedelta(hours=8)
    changed.loc[future_rows, "close"] *= 3
    original_features = add_candidate_features(original)
    changed_features = add_candidate_features(changed)
    at_t = original["timestamp"].eq(original["timestamp"].min())
    pd.testing.assert_frame_equal(
        original_features.loc[at_t, ADDITIONAL_CANDIDATE_FEATURES].reset_index(drop=True),
        changed_features.loc[at_t, ADDITIONAL_CANDIDATE_FEATURES].reset_index(drop=True),
    )
    original_targets = add_horizon_targets(add_candidate_labels(original_features))
    changed_targets = add_horizon_targets(add_candidate_labels(changed_features))
    assert not np.allclose(
        original_targets.loc[at_t, "future_net_return_8h"],
        changed_targets.loc[at_t, "future_net_return_8h"],
        equal_nan=True,
    )
