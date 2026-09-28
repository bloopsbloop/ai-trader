from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.dataset import (DatasetBuilder, DatasetConfig, LeakageSafePreprocessor,
                            chronological_split, walk_forward_splits)
from app.ml.features import INTERVAL_MINUTES, candle_features, merge_multitimeframe
from app.ml.news_features import NEWS_FEATURES, aggregate_news


def candles(count=160, minutes=15, future_jump=0):
    timestamps = pd.date_range("2024-01-01", periods=count, freq=f"{minutes}min", tz="UTC")
    close = 100 + np.arange(count) * .05 + np.sin(np.arange(count) / 4)
    close[-1] += future_jump
    return pd.DataFrame({"open_time": timestamps, "open": close - .1, "high": close + 1,
                         "low": close - 1, "close": close,
                         "volume": 1000 + (np.arange(count) % 17) * 10})


def write_history(directory, symbol="BTCUSDT", jump=0):
    directory.mkdir(parents=True, exist_ok=True)
    for timeframe, minutes in INTERVAL_MINUTES.items():
        count = {"15m": 2400, "1h": 650, "4h": 180}[timeframe]
        candles(count, minutes, jump if timeframe == "15m" else 0).to_csv(directory / f"{symbol}_{timeframe}.csv", index=False)


def config(tmp_path, with_news=False):
    return DatasetConfig(symbols=("BTCUSDT",), base_timeframe="15m", horizon_candles=4,
                         with_news=with_news, start=None, end=None,
                         history_dir=str(tmp_path / "history"), news_db=str(tmp_path / "missing.db"))


def test_features_do_not_use_future_candles():
    original, changed = candles(), candles(future_jump=1000)
    before = candle_features(original, "15m").iloc[-2]
    after = candle_features(changed, "15m").iloc[-2]
    pd.testing.assert_series_equal(before, after)
    assert candle_features(original, "15m").iloc[-1]["return_1"] != candle_features(changed, "15m").iloc[-1]["return_1"]


def test_gaps_restart_features_and_duplicates_are_rejected():
    source = candles()
    result = candle_features(source.drop(index=50), "15m")
    first_after_gap = result[result.source_open_time == source.open_time.iloc[51]].iloc[0]
    assert np.isnan(first_after_gap["return_1"])
    with pytest.raises(ValueError, match="Duplicate"):
        candle_features(pd.concat([source, source.iloc[[50]]]), "15m")


def test_multitimeframe_join_uses_only_closed_bars():
    frames = {tf: candle_features(candles({"15m": 160, "1h": 80, "4h": 50}[tf], minutes), tf)
              for tf, minutes in INTERVAL_MINUTES.items()}
    merged = merge_multitimeframe(frames, "15m").dropna()
    for timeframe in INTERVAL_MINUTES:
        assert (pd.to_datetime(merged[f"source_timestamp_{timeframe}"], utc=True) <= merged["timestamp"]).all()


def test_labels_use_future_but_features_do_not(tmp_path):
    write_history(tmp_path / "history")
    first, features, _ = DatasetBuilder(config(tmp_path)).build()
    source = pd.read_csv(tmp_path / "history/BTCUSDT_15m.csv")
    change_index = 900
    source.loc[change_index, ["open", "high", "low", "close"]] *= 2
    source.to_csv(tmp_path / "history/BTCUSDT_15m.csv", index=False)
    second, _, _ = DatasetBuilder(config(tmp_path)).build()
    target_time = pd.Timestamp(source.loc[change_index, "open_time"], tz="UTC") + pd.Timedelta(minutes=15)
    sample_time = target_time - pd.Timedelta(minutes=60)
    old = first[first.timestamp == sample_time].iloc[0]
    new = second[second.timestamp == sample_time].iloc[0]
    assert old[features].equals(new[features])
    assert old.future_net_return != new.future_net_return


def test_chronological_split_purges_cross_boundary_targets():
    times = pd.date_range("2024-01-01", periods=100, freq="h", tz="UTC")
    frame = pd.DataFrame({"timestamp": times, "target_timestamp": times + pd.Timedelta(hours=4)})
    split = chronological_split(frame)
    train, validation, test = (split[split.split == name] for name in ("train", "validation", "test"))
    assert train.timestamp.max() < validation.timestamp.min() < test.timestamp.min()
    assert train.target_timestamp.max() < validation.timestamp.min()
    assert validation.target_timestamp.max() < test.timestamp.min()


def test_walk_forward_is_ordered_and_purged():
    times = pd.date_range("2024-01-01", periods=120, freq="h", tz="UTC")
    frame = pd.DataFrame({"timestamp": times, "target_timestamp": times + pd.Timedelta(hours=4)})
    folds = list(walk_forward_splits(frame, folds=3))
    assert len(folds) == 3
    for fold in folds:
        train, validation = frame.loc[fold["train"]], frame.loc[fold["validation"]]
        assert train.target_timestamp.max() < validation.timestamp.min()


def test_news_respects_publication_and_first_seen():
    samples = pd.DataFrame({"timestamp": [pd.Timestamp("2024-01-02T00:00Z")], "symbol": ["BTCUSDT"]})
    base = {"article_id": "a", "title": "Bitcoin", "url": "https://x/a", "sentiment": .8,
            "importance": .9, "confidence": .7, "event_type": "adoption", "symbols": ["BTCUSDT"]}
    future_seen = pd.DataFrame([{**base, "published_at": "2024-01-01T23:00Z", "first_seen_at": "2024-01-02T01:00Z"}])
    assert aggregate_news(samples, future_seen).iloc[0].sum() == 0
    eligible = future_seen.assign(first_seen_at="2024-01-01T23:30Z")
    row = aggregate_news(samples, eligible).iloc[0]
    assert row.news_count_recent == 1 and row.symbol_specific_news_count == 1 and row.event_adoption == 1


def test_with_news_and_without_news_have_same_market_rows(tmp_path):
    write_history(tmp_path / "history")
    without, without_features, _ = DatasetBuilder(config(tmp_path, False)).build()
    with_news, with_features, metadata = DatasetBuilder(config(tmp_path, True)).build()
    assert len(without) == len(with_news)
    assert not set(NEWS_FEATURES) & set(without_features)
    assert set(NEWS_FEATURES) <= set(with_features)
    assert metadata["news"]["historical_observations_present"] is False


def test_dataset_metadata_and_target_definition(tmp_path):
    write_history(tmp_path / "history")
    frame, features, metadata = DatasetBuilder(config(tmp_path)).build()
    assert metadata["rows"] == len(frame)
    assert metadata["feature_count"] == len(features)
    assert set(metadata["periods"]) == {"train", "validation", "test"}
    assert metadata["target"]["horizon_minutes"] == 60
    assert metadata["target"]["round_trip_cost"] == pytest.approx(.0018)
    assert sum(sum(values.values()) for values in metadata["class_distribution"].values()) == len(frame)


def test_preprocessor_fits_train_only_and_applies_train_median():
    frame = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC"),
                          "split": ["train"] * 3 + ["validation", "validation", "test"],
                          "useful": [1, np.nan, 3, 1000, np.nan, 2000],
                          "constant": [1] * 6, "duplicate": [1, np.nan, 3, 1000, np.nan, 2000]})
    preprocessor = LeakageSafePreprocessor(max_missing_fraction=.5).fit(frame.iloc[:3], ["useful", "constant", "duplicate"])
    assert preprocessor.feature_names_ == ["useful"]
    assert preprocessor.medians_["useful"] == 2
    assert preprocessor.transform(frame.iloc[3:]).iloc[1, 0] == 2
    assert preprocessor.fitted_period_["end"] == frame.timestamp.iloc[2].isoformat()
    with pytest.raises(ValueError, match="train rows only"):
        LeakageSafePreprocessor().fit(frame, ["useful"])


def test_dataset_is_strictly_chronological_with_valid_sources(tmp_path):
    write_history(tmp_path / "history")
    frame, _, _ = DatasetBuilder(config(tmp_path)).build()
    assert (frame.target_timestamp > frame.timestamp).all()
    for timeframe in INTERVAL_MINUTES:
        assert (pd.to_datetime(frame[f"source_timestamp_{timeframe}"], utc=True) <= frame.timestamp).all()
    assert frame.sort_values(["timestamp", "symbol"]).index.equals(frame.index)
