"""Build reproducible, chronological ML datasets from locally cached candles."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.features import (FEATURE_VERSION, INTERVAL_MINUTES, candle_features,
                             market_feature_names, merge_multitimeframe)
from app.ml.news_features import NEWS_FEATURES, aggregate_news, load_news_sqlite

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY = PROJECT_ROOT / "data" / "history"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "ml" / "datasets"
DEFAULT_NEWS_DB = PROJECT_ROOT / "data" / "ai" / "batch3.sqlite3"


def _utc_timestamp(value):
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


@dataclass(frozen=True)
class DatasetConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    base_timeframe: str = "1h"
    horizon_candles: int = 4
    commission: float = 0.0004
    slippage: float = 0.0005
    minimum_edge: float = 0.001
    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    with_news: bool = False
    start: str | None = "2023-01-01"
    end: str | None = "2025-12-31T23:59:59"
    history_dir: str = str(DEFAULT_HISTORY)
    news_db: str = str(DEFAULT_NEWS_DB)
    feature_version: str = FEATURE_VERSION

    def __post_init__(self):
        if self.base_timeframe not in INTERVAL_MINUTES:
            raise ValueError("base_timeframe must be 15m, 1h or 4h")
        if self.horizon_candles < 1:
            raise ValueError("horizon_candles must be positive")
        if not 0 < self.train_fraction < 1 or not 0 < self.validation_fraction < 1:
            raise ValueError("split fractions must be between zero and one")
        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError("train + validation must leave a test period")

    @property
    def round_trip_cost(self):
        return 2 * (self.commission + self.slippage)


def chronological_split(frame: pd.DataFrame, train_fraction=.60, validation_fraction=.20) -> pd.DataFrame:
    """Split by distinct timestamps and purge targets crossing a boundary."""
    if frame.empty:
        raise ValueError("Cannot split an empty dataset")
    timestamps = pd.Index(sorted(pd.to_datetime(frame["timestamp"], utc=True).unique()))
    train_index = max(1, int(len(timestamps) * train_fraction))
    validation_index = max(train_index + 1, int(len(timestamps) * (train_fraction + validation_fraction)))
    if validation_index >= len(timestamps):
        raise ValueError("Not enough timestamps for train/validation/test")
    validation_start, test_start = timestamps[train_index], timestamps[validation_index]
    result = frame.copy()
    timestamp = pd.to_datetime(result["timestamp"], utc=True)
    target_timestamp = pd.to_datetime(result["target_timestamp"], utc=True)
    split = np.where((timestamp < validation_start) & (target_timestamp < validation_start), "train",
                     np.where((timestamp >= validation_start) & (timestamp < test_start) & (target_timestamp < test_start), "validation",
                              np.where(timestamp >= test_start, "test", "purged")))
    result["split"] = split
    return result[result["split"] != "purged"].reset_index(drop=True)


def walk_forward_splits(frame: pd.DataFrame, folds=3, minimum_train_fraction=.40):
    """Expanding train windows and non-overlapping validation windows with purging."""
    timestamps = pd.Index(sorted(pd.to_datetime(frame["timestamp"], utc=True).unique()))
    first = max(1, int(len(timestamps) * minimum_train_fraction))
    validation_size = (len(timestamps) - first) // folds
    if validation_size < 1:
        raise ValueError("Not enough timestamps for walk-forward folds")
    stamp = pd.to_datetime(frame["timestamp"], utc=True)
    target = pd.to_datetime(frame["target_timestamp"], utc=True)
    for fold in range(folds):
        validation_start_index = first + fold * validation_size
        validation_end_index = len(timestamps) if fold == folds - 1 else validation_start_index + validation_size
        validation_start = timestamps[validation_start_index]
        validation_end = None if validation_end_index == len(timestamps) else timestamps[validation_end_index]
        train_index = frame.index[(stamp < validation_start) & (target < validation_start)].to_numpy()
        validation_mask = stamp >= validation_start
        if validation_end is not None:
            validation_mask &= stamp < validation_end
        validation_index = frame.index[validation_mask].to_numpy()
        yield {"fold": fold + 1, "train": train_index, "validation": validation_index,
               "train_end": validation_start.isoformat(),
               "validation_start": validation_start.isoformat(),
               "validation_end": validation_end.isoformat() if validation_end is not None else None}


class LeakageSafePreprocessor:
    """Train-only median imputer and feature filter; no scaling for tree models."""
    def __init__(self, max_missing_fraction=.20):
        self.max_missing_fraction = max_missing_fraction
        self.feature_names_ = []
        self.medians_ = {}
        self.fitted_period_ = None

    def fit(self, train: pd.DataFrame, feature_names: list[str]):
        if "timestamp" not in train or train.empty:
            raise ValueError("Training rows with timestamps are required")
        if "split" in train and set(train["split"]) != {"train"}:
            raise ValueError("Preprocessor may be fit on train rows only")
        selected, seen = [], {}
        for name in feature_names:
            series = pd.to_numeric(train[name], errors="coerce").replace([np.inf, -np.inf], np.nan)
            if series.isna().mean() > self.max_missing_fraction or series.nunique(dropna=True) <= 1:
                continue
            signature = hashlib.sha256(pd.util.hash_pandas_object(series.fillna(float("inf")), index=False).values.tobytes()).hexdigest()
            if signature in seen:
                continue
            seen[signature] = name
            selected.append(name)
            self.medians_[name] = float(series.median())
        if not selected:
            raise ValueError("No usable training features")
        self.feature_names_ = selected
        stamps = pd.to_datetime(train["timestamp"], utc=True)
        self.fitted_period_ = {"start": stamps.min().isoformat(), "end": stamps.max().isoformat(), "rows": len(train)}
        return self

    def transform(self, frame: pd.DataFrame):
        if not self.feature_names_:
            raise ValueError("Preprocessor is not fitted")
        missing = set(self.feature_names_) - set(frame.columns)
        if missing:
            raise ValueError(f"Feature mismatch: {sorted(missing)}")
        return pd.DataFrame({name: pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(self.medians_[name])
                             for name in self.feature_names_}, index=frame.index)

    def metadata(self):
        return {"features": self.feature_names_, "medians": self.medians_, "fitted_period": self.fitted_period_,
                "max_missing_fraction": self.max_missing_fraction, "scaling": None}


class DatasetBuilder:
    def __init__(self, config: DatasetConfig):
        self.config = config

    def _load(self, symbol, timeframe):
        path = Path(self.config.history_dir) / f"{symbol}_{timeframe}.csv"
        if not path.exists():
            raise FileNotFoundError(f"Missing cached history: {path}")
        frame = pd.read_csv(path)
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        if self.config.start:
            # Retain warm-up rows before start; filter samples after feature creation.
            warmup = _utc_timestamp(self.config.start) - pd.Timedelta(days=10)
            frame = frame[frame["open_time"] >= warmup]
        if self.config.end:
            frame = frame[frame["open_time"] <= _utc_timestamp(self.config.end)]
        return frame

    def build(self):
        rows = []
        for symbol in self.config.symbols:
            calculated = {timeframe: candle_features(self._load(symbol, timeframe), timeframe) for timeframe in INTERVAL_MINUTES}
            merged = merge_multitimeframe(calculated, self.config.base_timeframe)
            merged["symbol"] = symbol
            merged["timeframe"] = self.config.base_timeframe
            merged["future_close"] = merged["close"].shift(-self.config.horizon_candles)
            merged["target_timestamp"] = merged["timestamp"].shift(-self.config.horizon_candles)
            merged["future_net_return"] = merged["future_close"] / merged["close"] - 1 - self.config.round_trip_cost
            merged["label"] = (merged["future_net_return"] > self.config.minimum_edge).astype("Int64")
            if self.config.start:
                merged = merged[merged["timestamp"] >= _utc_timestamp(self.config.start)]
            if self.config.end:
                merged = merged[merged["timestamp"] <= _utc_timestamp(self.config.end)]
            rows.append(merged)
        frame = pd.concat(rows, ignore_index=True).dropna(subset=["future_close", "target_timestamp"])
        features = market_feature_names(frame)
        if self.config.with_news:
            news = load_news_sqlite(Path(self.config.news_db))
            frame = pd.concat([frame, aggregate_news(frame, news)], axis=1)
        frame = chronological_split(frame.sort_values(["timestamp", "symbol"]).reset_index(drop=True),
                                    self.config.train_fraction, self.config.validation_fraction)
        if self.config.with_news:
            features += [name for name in NEWS_FEATURES if name in frame]
        # Require complete market data. News zeros mean observed absence/unavailable coverage, not invented events.
        frame = frame.dropna(subset=[name for name in features if name not in NEWS_FEATURES]).reset_index(drop=True)
        metadata = self.metadata(frame, features)
        preprocessor = LeakageSafePreprocessor().fit(frame[frame["split"] == "train"], features)
        metadata["preprocessing"] = preprocessor.metadata()
        return frame, features, metadata

    def metadata(self, frame, features):
        periods = {}
        classes = {}
        for split, group in frame.groupby("split", observed=True):
            periods[split] = {"start": group["timestamp"].min().isoformat(), "end": group["timestamp"].max().isoformat(),
                              "rows": len(group)}
            classes[split] = {str(key): int(value) for key, value in group["label"].value_counts().sort_index().items()}
        news_present = self.config.with_news and bool(frame[NEWS_FEATURES].to_numpy().sum())
        walk_forward = [{"fold": item["fold"], "train_rows": len(item["train"]),
                         "validation_rows": len(item["validation"]),
                         "train_end": item["train_end"], "validation_start": item["validation_start"],
                         "validation_end": item["validation_end"]}
                        for item in walk_forward_splits(frame, folds=3)]
        return {"schema_version": 1, "feature_version": self.config.feature_version,
                "created_at": datetime.now(timezone.utc).isoformat(), "config": asdict(self.config),
                "target": {"name": "positive_future_net_return", "horizon_candles": self.config.horizon_candles,
                           "horizon_minutes": self.config.horizon_candles * INTERVAL_MINUTES[self.config.base_timeframe],
                           "round_trip_cost": self.config.round_trip_cost, "minimum_edge": self.config.minimum_edge,
                           "formula": "future_close/current_close - 1 - 2*(commission+slippage) > minimum_edge"},
                "symbols": list(self.config.symbols), "timeframes": list(INTERVAL_MINUTES),
                "base_timeframe": self.config.base_timeframe, "periods": periods,
                "walk_forward": walk_forward,
                "rows": len(frame), "class_distribution": classes,
                "feature_count": len(features), "features": features,
                "news": {"included": self.config.with_news, "historical_observations_present": news_present,
                         "limitation": None if news_present else ("No scored historical news overlaps the candle period; news columns represent zero observed events, not fabricated coverage." if self.config.with_news else None)},
                "holdout_limitation": "2023-2025 history was previously viewed by project research; test is chronologically isolated for ML v1 but is not a pristine project-wide holdout."}


def export_dataset(frame, metadata, output_dir, name):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / f"{name}.csv.gz"
    metadata_path = output / f"{name}.metadata.json"
    frame.to_csv(csv_path, index=False, compression="gzip", float_format="%.10g")
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return csv_path, metadata_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build leakage-safe Batch 3 ML datasets from local candle caches")
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--timeframe", choices=INTERVAL_MINUTES, default="1h")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2025-12-31T23:59:59")
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--with-news", action="store_true")
    parser.add_argument("--without-news", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY))
    parser.add_argument("--news-db", default=str(DEFAULT_NEWS_DB))
    args = parser.parse_args(argv)
    if args.with_news and args.without_news:
        parser.error("Choose at most one dataset variant; omission builds both")
    variants = [True, False] if not args.with_news and not args.without_news else [args.with_news]
    summaries = []
    for with_news in variants:
        config = DatasetConfig(symbols=tuple(args.symbols or DatasetConfig.symbols), base_timeframe=args.timeframe,
                               horizon_candles=args.horizon, start=args.start, end=args.end, with_news=with_news,
                               history_dir=args.history_dir, news_db=args.news_db)
        frame, features, metadata = DatasetBuilder(config).build()
        name = f"ml_v1_{args.timeframe}_{'with_news' if with_news else 'without_news'}"
        csv_path, metadata_path = export_dataset(frame, metadata, args.output, name)
        summaries.append({"variant": name, "rows": len(frame), "features": len(features),
                          "dataset": str(csv_path), "metadata": str(metadata_path),
                          "periods": metadata["periods"], "class_distribution": metadata["class_distribution"]})
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
