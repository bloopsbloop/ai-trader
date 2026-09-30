from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.cross_asset_dataset import (
    CROSS_ASSET_FEATURES, MARKET_CONTEXT_FEATURES, TARGET_COLUMNS,
    CrossAssetConfig, CrossAssetDatasetBuilder, add_cross_sectional_features,
    add_future_targets, chronological_group_split, discover_universe,
    validate_dataset,
)


def base_rows(hours=40):
    rows = []
    for asset_index, symbol in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
        for index, timestamp in enumerate(pd.date_range("2025-01-01", periods=hours, freq="1h", tz="UTC")):
            close = 100 + asset_index * 10 + index * (1 + asset_index * .1)
            rows.append({
                "timestamp": timestamp, "symbol": symbol, "close": close,
                "_high": close * 1.01, "_low": close * .99,
                "return_1_1h": .001 * (asset_index + 1),
                "momentum_4_1h": .004 * (asset_index + 1),
                "volume_change_1h": .02 * (asset_index + 1),
                "volatility_20_1h": .01 + asset_index * .002,
                "rsi_14_1h": 45 + asset_index * 5,
                "ema_distance_20_1h": -.01 + asset_index * .01,
                "source_timestamp_15m": timestamp,
                "source_timestamp_1h": timestamp,
                "source_timestamp_4h": timestamp.floor("4h"),
            })
    return pd.DataFrame(rows)


def test_cross_asset_ranks_relative_strength_and_market_context():
    result = add_cross_sectional_features(base_rows())
    last = result[result["timestamp"] == result["timestamp"].max()].set_index("symbol")
    assert last.loc["SOLUSDT", "return_rank_1h"] == 1
    assert last.loc["BTCUSDT", "relative_strength_vs_btc"] == 0
    assert last["eligible_universe_size"].eq(3).all()
    assert last["market_breadth"].eq(1).all()
    assert last["median_universe_rsi"].eq(50).all()
    assert np.isclose(last["cross_sectional_return_zscore"].mean(), 0)


def test_future_labels_costs_rank_excursion_and_no_trade():
    config = CrossAssetConfig(horizon_hours=4, meaningful_net_threshold=1)
    featured = add_cross_sectional_features(base_rows())
    result = add_future_targets(featured, config)
    first = result[result["timestamp"] == result["timestamp"].min()]
    assert np.allclose(first["future_net_return_4h"], first["future_gross_return_4h"] - config.round_trip_cost)
    assert first["is_top_1"].sum() == 1
    assert first["is_top_3"].sum() == 3
    assert first["no_trade_preferred"].eq(1).all()
    assert (first["future_max_favorable_move"] > first["future_max_adverse_move"]).all()


def test_future_price_mutation_changes_targets_only():
    config = CrossAssetConfig(horizon_hours=4)
    original = add_cross_sectional_features(base_rows())
    mutated_input = base_rows()
    cutoff = mutated_input["timestamp"].min()
    future = (mutated_input["symbol"] == "ETHUSDT") & (mutated_input["timestamp"] == cutoff + pd.Timedelta(hours=4))
    mutated_input.loc[future, ["close", "_high", "_low"]] *= 1.5
    mutated = add_cross_sectional_features(mutated_input)
    feature_columns = [name for name in CROSS_ASSET_FEATURES + MARKET_CONTEXT_FEATURES if name in original]
    before = original[(original["symbol"] == "ETHUSDT") & (original["timestamp"] == cutoff)].iloc[0]
    after = mutated[(mutated["symbol"] == "ETHUSDT") & (mutated["timestamp"] == cutoff)].iloc[0]
    assert np.allclose(before[feature_columns].astype(float), after[feature_columns].astype(float), equal_nan=True)
    old_target = add_future_targets(original, config)
    new_target = add_future_targets(mutated, config)
    old_value = old_target[(old_target["symbol"] == "ETHUSDT") & (old_target["timestamp"] == cutoff)]["future_net_return_4h"].iloc[0]
    new_value = new_target[(new_target["symbol"] == "ETHUSDT") & (new_target["timestamp"] == cutoff)]["future_net_return_4h"].iloc[0]
    assert new_value != old_value


def test_chronological_group_split_is_group_safe_and_purged():
    result = add_future_targets(add_cross_sectional_features(base_rows(80)), CrossAssetConfig())
    split = chronological_group_split(result)
    assert split.groupby("timestamp")["split"].nunique().max() == 1
    periods = {name: group for name, group in split.groupby("split")}
    assert periods["train"]["target_timestamp"].max() < periods["validation"]["timestamp"].min()
    assert periods["validation"]["target_timestamp"].max() < periods["test"]["timestamp"].min()


def test_validation_rejects_future_source_and_target_feature():
    features = ["return_1_1h"] + CROSS_ASSET_FEATURES + MARKET_CONTEXT_FEATURES
    featured = add_cross_sectional_features(base_rows()).dropna(subset=features)
    frame = chronological_group_split(add_future_targets(featured, CrossAssetConfig()))
    assert validate_dataset(frame, features)["passed"]
    leaked = frame.copy()
    leaked.loc[leaked.index[0], "source_timestamp_15m"] = leaked.loc[leaked.index[0], "timestamp"] + pd.Timedelta(minutes=15)
    assert "future_15m_candle_in_features" in validate_dataset(leaked, features)["issues"]
    assert "target_in_feature_schema" in validate_dataset(frame, features + [TARGET_COLUMNS[0]])["issues"]
    duplicated = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    assert "duplicate_timestamp_symbol" in validate_dataset(duplicated, features)["issues"]


def write_candles(path: Path, start, periods, frequency, multiplier=1):
    times = pd.date_range(start, periods=periods, freq=frequency, tz="UTC")
    close = 100 + np.arange(periods) * .1 * multiplier
    pd.DataFrame({"open_time": times, "open": close - .05, "high": close + .2,
                  "low": close - .2, "close": close, "volume": 1000 + np.arange(periods)}).to_csv(path, index=False)


def test_universe_requires_all_timeframes(tmp_path):
    for timeframe, periods, frequency in (("15m", 100, "15min"), ("1h", 25, "1h"), ("4h", 7, "4h")):
        write_candles(tmp_path / f"BTCUSDT_{timeframe}.csv", "2025-01-01", periods, frequency)
    write_candles(tmp_path / "ETHUSDT_1h.csv", "2025-01-01", 25, "1h")
    audit = discover_universe(tmp_path)
    assert audit.set_index("symbol").loc["BTCUSDT", "included"]
    assert not audit.set_index("symbol").loc["ETHUSDT", "included"]
    assert "missing_15m" in audit.set_index("symbol").loc["ETHUSDT", "exclusion_reason"]


def test_dataset_builder_and_schema(tmp_path):
    for asset_index, symbol in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT"), start=1):
        for timeframe, periods, frequency in (("15m", 1600, "15min"), ("1h", 400, "1h"), ("4h", 100, "4h")):
            write_candles(tmp_path / f"{symbol}_{timeframe}.csv", "2025-01-01", periods, frequency, asset_index)
    config = CrossAssetConfig(start="2025-01-05", end="2025-01-16", history_dir=str(tmp_path))
    frame, features, metadata, coverage = CrossAssetDatasetBuilder(config).build()
    assert not frame.empty
    assert metadata["dataset_version"] == "cross-asset-v1"
    assert set(metadata["symbols"]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert not set(features) & set(TARGET_COLUMNS)
    assert frame.groupby("timestamp")["symbol"].size().min() == 3
    assert coverage["included"].all()
