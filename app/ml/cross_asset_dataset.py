"""Build a leakage-safe cross-asset opportunity dataset from local candle caches.

This research-only module does not import brokers, trading runners, or live APIs.
Features at timestamp T use candles closed no later than T. Future prices are kept
exclusively in target columns.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from app.ml.features import INTERVAL_MINUTES, candle_features, market_feature_names, merge_multitimeframe

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY = PROJECT_ROOT / "data" / "history"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "ml" / "cross_asset"
DATASET_VERSION = "cross-asset-v1"
REQUIRED_TIMEFRAMES = ("15m", "1h", "4h")

CROSS_ASSET_FEATURES = [
    "return_4h", "return_24h", "return_rank_1h", "return_rank_4h",
    "return_rank_24h", "momentum_rank", "volume_change_rank",
    "volatility_rank", "rsi_rank", "relative_strength_vs_btc",
    "relative_strength_vs_market", "cross_sectional_return_zscore",
    "cross_sectional_volume_zscore",
]
MARKET_CONTEXT_FEATURES = [
    "btc_trend", "btc_volatility", "median_universe_return_1h",
    "median_universe_return_24h", "fraction_assets_above_ema",
    "cross_sectional_dispersion", "market_breadth", "median_universe_rsi",
]
TARGET_COLUMNS = [
    "future_gross_return_4h", "future_net_return_4h", "future_max_adverse_move", "future_max_favorable_move",
    "future_return_rank", "future_return_percentile", "is_top_1", "is_top_3",
    "is_positive_after_costs", "risk_adjusted_target", "no_trade_preferred",
    "best_available_net_return",
]
METADATA_COLUMNS = [
    "timestamp", "target_timestamp", "symbol", "split", "eligible_universe_size",
    "source_open_time", "source_timestamp_15m", "source_timestamp_1h", "source_timestamp_4h", "close",
]


def _utc(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


@dataclass(frozen=True)
class CrossAssetConfig:
    start: str = "2023-01-01"
    end: str = "2025-12-31T23:59:59"
    horizon_hours: int = 4
    commission: float = 0.0004
    slippage: float = 0.0005
    meaningful_net_threshold: float = 0.001
    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    minimum_coverage: float = 0.95
    history_dir: str = str(DEFAULT_HISTORY)
    output_dir: str = str(DEFAULT_OUTPUT)
    dataset_version: str = DATASET_VERSION

    def __post_init__(self):
        if self.horizon_hours < 1:
            raise ValueError("horizon_hours must be positive")
        if not 0 < self.train_fraction < 1 or not 0 < self.validation_fraction < 1:
            raise ValueError("split fractions must be between zero and one")
        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError("train + validation must leave a test period")

    @property
    def round_trip_cost(self):
        return 2 * (self.commission + self.slippage)


def discover_universe(history_dir: str | Path, start=None, end=None, minimum_coverage=.95):
    """Audit all cached symbols and select only those with every required timeframe."""
    root = Path(history_dir)
    symbols = sorted({path.stem.rsplit("_", 1)[0] for path in root.glob("*_*.csv")})
    rows = []
    for symbol in symbols:
        row = {"symbol": symbol}
        coverages, firsts, lasts, reasons = [], [], [], []
        for timeframe in REQUIRED_TIMEFRAMES:
            path = root / f"{symbol}_{timeframe}.csv"
            row[f"has_{timeframe}"] = path.exists()
            if not path.exists():
                row[f"coverage_{timeframe}"] = 0.0
                row[f"missing_{timeframe}"] = None
                reasons.append(f"missing_{timeframe}")
                continue
            candles = pd.read_csv(path, usecols=["open_time"])
            times = pd.to_datetime(candles["open_time"], utc=True).drop_duplicates().sort_values()
            if start is not None:
                times = times[times >= _utc(start)]
            if end is not None:
                times = times[times <= _utc(end)]
            if times.empty:
                row[f"coverage_{timeframe}"] = 0.0
                row[f"missing_{timeframe}"] = None
                reasons.append(f"no_{timeframe}_rows_in_period")
                continue
            seconds = INTERVAL_MINUTES[timeframe] * 60
            expected = int((times.iloc[-1] - times.iloc[0]).total_seconds() // seconds) + 1
            missing = expected - len(times)
            coverage = len(times) / expected
            row[f"first_{timeframe}"] = times.iloc[0].isoformat()
            row[f"last_{timeframe}"] = times.iloc[-1].isoformat()
            row[f"missing_{timeframe}"] = int(missing)
            row[f"coverage_{timeframe}"] = float(coverage)
            coverages.append(coverage)
            firsts.append(times.iloc[0])
            lasts.append(times.iloc[-1])
            if coverage < minimum_coverage:
                reasons.append(f"low_coverage_{timeframe}")
        row["first_common"] = max(firsts).isoformat() if len(firsts) == 3 else None
        row["last_common"] = min(lasts).isoformat() if len(lasts) == 3 else None
        row["coverage_pct"] = 100 * min(coverages) if len(coverages) == 3 else 0.0
        row["included"] = len(coverages) == 3 and min(coverages) >= minimum_coverage
        row["exclusion_reason"] = ";".join(reasons) if reasons else ""
        rows.append(row)
    return pd.DataFrame(rows)


def add_cross_sectional_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate ranks and market context independently inside each timestamp."""
    result = frame.sort_values(["timestamp", "symbol"]).copy()
    symbol_groups = result.groupby("symbol", sort=False)
    for hours in (4, 24):
        prior_close = symbol_groups["close"].shift(hours)
        prior_time = symbol_groups["timestamp"].shift(hours)
        exact = pd.to_datetime(result["timestamp"], utc=True) - pd.to_datetime(prior_time, utc=True) == pd.Timedelta(hours=hours)
        result[f"return_{hours}h"] = (result["close"] / prior_close - 1).where(exact)
    return refresh_cross_sectional_features(result)


def refresh_cross_sectional_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Recalculate cross-sectional values after the point-in-time eligible set changes."""
    result = frame.sort_values(["timestamp", "symbol"]).copy()
    stale = [name for name in CROSS_ASSET_FEATURES[2:] + MARKET_CONTEXT_FEATURES + ["eligible_universe_size"] if name in result]
    if stale:
        result = result.drop(columns=stale)
    groups = result.groupby("timestamp", sort=False)
    rank_inputs = {
        "return_rank_1h": "return_1_1h", "return_rank_4h": "return_4h",
        "return_rank_24h": "return_24h", "momentum_rank": "momentum_4_1h",
        "volume_change_rank": "volume_change_1h", "volatility_rank": "volatility_20_1h",
        "rsi_rank": "rsi_14_1h",
    }
    for output, source in rank_inputs.items():
        result[output] = groups[source].rank(method="average", pct=True)

    def zscore(series):
        deviation = series.std(ddof=0)
        return pd.Series(0.0, index=series.index) if deviation == 0 else (series - series.mean()) / deviation

    result["cross_sectional_return_zscore"] = groups["return_1_1h"].transform(zscore)
    result["cross_sectional_volume_zscore"] = groups["volume_change_1h"].transform(zscore)
    market_return = groups["return_24h"].transform("median")
    result["relative_strength_vs_market"] = result["return_24h"] - market_return
    btc = result[result["symbol"] == "BTCUSDT"][["timestamp", "return_24h", "ema_distance_20_1h", "volatility_20_1h"]]
    btc = btc.rename(columns={"return_24h": "_btc_return", "ema_distance_20_1h": "btc_trend",
                              "volatility_20_1h": "btc_volatility"})
    result = result.merge(btc, on="timestamp", how="left", validate="many_to_one")
    result["relative_strength_vs_btc"] = result["return_24h"] - result["_btc_return"]
    result["median_universe_return_1h"] = groups["return_1_1h"].transform("median").to_numpy()
    result["median_universe_return_24h"] = market_return.to_numpy()
    result["fraction_assets_above_ema"] = groups["ema_distance_20_1h"].transform(lambda value: (value > 0).mean()).to_numpy()
    result["cross_sectional_dispersion"] = groups["return_1_1h"].transform(lambda value: value.std(ddof=0)).to_numpy()
    result["market_breadth"] = groups["return_1_1h"].transform(lambda value: (value > 0).mean()).to_numpy()
    result["median_universe_rsi"] = groups["rsi_14_1h"].transform("median").to_numpy()
    result["eligible_universe_size"] = groups["symbol"].transform("size").to_numpy()
    return result.drop(columns="_btc_return")


def add_future_targets(frame: pd.DataFrame, config: CrossAssetConfig) -> pd.DataFrame:
    """Append target-only future returns, excursion and cross-sectional opportunity labels."""
    result = frame.sort_values(["symbol", "timestamp"]).copy()
    grouped = result.groupby("symbol", sort=False)
    result["target_timestamp"] = grouped["timestamp"].shift(-config.horizon_hours)
    future_close = grouped["close"].shift(-config.horizon_hours)
    highs = pd.concat([grouped["_high"].shift(-step) for step in range(1, config.horizon_hours + 1)], axis=1)
    lows = pd.concat([grouped["_low"].shift(-step) for step in range(1, config.horizon_hours + 1)], axis=1)
    exact = pd.to_datetime(result["target_timestamp"], utc=True) == pd.to_datetime(result["timestamp"], utc=True) + pd.Timedelta(hours=config.horizon_hours)
    result["future_gross_return_4h"] = (future_close / result["close"] - 1).where(exact)
    result["future_net_return_4h"] = (result["future_gross_return_4h"] - config.round_trip_cost).where(exact)
    result["future_max_favorable_move"] = (highs.max(axis=1) / result["close"] - 1).where(exact)
    result["future_max_adverse_move"] = (lows.min(axis=1) / result["close"] - 1).where(exact)
    volatility = result["volatility_20_1h"].clip(lower=1e-6)
    result["risk_adjusted_target"] = result["future_net_return_4h"] / volatility
    valid = result.dropna(subset=["future_net_return_4h", "future_max_adverse_move", "future_max_favorable_move"])
    valid_times = valid.groupby("timestamp")["symbol"].size()
    feature_times = result.groupby("timestamp")["symbol"].size()
    complete_times = valid_times[valid_times == feature_times.reindex(valid_times.index)].index
    result = result[result["timestamp"].isin(complete_times)].copy()
    future_groups = result.groupby("timestamp", sort=False)
    result["future_return_rank"] = future_groups["future_net_return_4h"].rank(method="first", ascending=False).astype(int)
    result["future_return_percentile"] = future_groups["future_net_return_4h"].rank(method="average", pct=True)
    result["is_top_1"] = (result["future_return_rank"] == 1).astype(int)
    result["is_top_3"] = (result["future_return_rank"] <= 3).astype(int)
    result["is_positive_after_costs"] = (result["future_net_return_4h"] > 0).astype(int)
    result["best_available_net_return"] = future_groups["future_net_return_4h"].transform("max")
    result["no_trade_preferred"] = (result["best_available_net_return"] < config.meaningful_net_threshold).astype(int)
    return result.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def chronological_group_split(frame: pd.DataFrame, train_fraction=.60, validation_fraction=.20) -> pd.DataFrame:
    """Assign every row at a decision timestamp to one split and purge crossing labels."""
    timestamps = pd.Index(sorted(pd.to_datetime(frame["timestamp"], utc=True).unique()))
    train_index = max(1, int(len(timestamps) * train_fraction))
    validation_index = max(train_index + 1, int(len(timestamps) * (train_fraction + validation_fraction)))
    if validation_index >= len(timestamps):
        raise ValueError("Not enough timestamps for train/validation/test")
    validation_start, test_start = timestamps[train_index], timestamps[validation_index]
    stamp = pd.to_datetime(frame["timestamp"], utc=True)
    target = pd.to_datetime(frame["target_timestamp"], utc=True)
    result = frame.copy()
    result["split"] = np.where(
        (stamp < validation_start) & (target < validation_start), "train",
        np.where((stamp >= validation_start) & (stamp < test_start) & (target < test_start), "validation",
                 np.where(stamp >= test_start, "test", "purged")),
    )
    return result[result["split"] != "purged"].reset_index(drop=True)


def validate_dataset(frame: pd.DataFrame, features: list[str]) -> dict:
    issues = []
    if frame.duplicated(["timestamp", "symbol"]).any():
        issues.append("duplicate_timestamp_symbol")
    if set(features) & set(TARGET_COLUMNS + ["target_timestamp"]):
        issues.append("target_in_feature_schema")
    if frame[features].isna().any().any():
        issues.append("missing_feature_values")
    if not np.isfinite(frame[features].to_numpy(dtype=float)).all():
        issues.append("non_finite_feature_values")
    for timeframe in REQUIRED_TIMEFRAMES:
        source = pd.to_datetime(frame[f"source_timestamp_{timeframe}"], utc=True)
        decision = pd.to_datetime(frame["timestamp"], utc=True)
        if (source > decision).any():
            issues.append(f"future_{timeframe}_candle_in_features")
        if ((decision - source) >= pd.Timedelta(minutes=INTERVAL_MINUTES[timeframe])).any():
            issues.append(f"stale_{timeframe}_candle_in_features")
    if not (pd.to_datetime(frame["target_timestamp"], utc=True) - pd.to_datetime(frame["timestamp"], utc=True)).eq(pd.Timedelta(hours=4)).all():
        issues.append("invalid_target_horizon")
    if not np.isfinite(frame[TARGET_COLUMNS].to_numpy(dtype=float)).all():
        issues.append("invalid_target_values")
    if frame.groupby("timestamp")["split"].nunique().max() != 1:
        issues.append("timestamp_group_crosses_split")
    if not frame.groupby("timestamp")["is_top_1"].sum().eq(1).all():
        issues.append("top_1_not_unique")
    observed = frame.groupby("timestamp")["symbol"].size()
    stored = frame.groupby("timestamp")["eligible_universe_size"].first()
    if not observed.eq(stored).all():
        issues.append("eligible_universe_size_mismatch")
    return {"passed": not issues, "issues": issues, "rows": len(frame),
            "timestamps": int(frame["timestamp"].nunique()), "duplicate_rows": int(frame.duplicated(["timestamp", "symbol"]).sum())}


class CrossAssetDatasetBuilder:
    def __init__(self, config: CrossAssetConfig):
        self.config = config

    def _load(self, symbol, timeframe):
        frame = pd.read_csv(Path(self.config.history_dir) / f"{symbol}_{timeframe}.csv")
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        warmup = _utc(self.config.start) - pd.Timedelta(days=10)
        return frame[(frame["open_time"] >= warmup) & (frame["open_time"] <= _utc(self.config.end))].copy()

    def build(self):
        coverage = discover_universe(self.config.history_dir, self.config.start, self.config.end, self.config.minimum_coverage)
        symbols = coverage.loc[coverage["included"], "symbol"].tolist()
        if "BTCUSDT" not in symbols or len(symbols) < 2:
            raise ValueError("Cross-asset research requires BTCUSDT and at least one comparison asset")
        rows, usable = [], {}
        for symbol in symbols:
            raw = {timeframe: self._load(symbol, timeframe) for timeframe in REQUIRED_TIMEFRAMES}
            calculated = {timeframe: candle_features(raw[timeframe], timeframe) for timeframe in REQUIRED_TIMEFRAMES}
            merged = merge_multitimeframe(calculated, "1h")
            base = raw["1h"][["open_time", "high", "low"]].copy()
            base["timestamp"] = base["open_time"] + pd.Timedelta(hours=1)
            merged = merged.merge(base[["timestamp", "high", "low"]], on="timestamp", how="left", validate="one_to_one")
            merged = merged.rename(columns={"high": "_high", "low": "_low"})
            merged["symbol"] = symbol
            merged = merged[(merged["timestamp"] >= _utc(self.config.start)) & (merged["timestamp"] <= _utc(self.config.end))]
            usable[symbol] = {"first_usable": merged["timestamp"].min().isoformat(), "last_usable": merged["timestamp"].max().isoformat()}
            rows.append(merged)
        frame = pd.concat(rows, ignore_index=True).sort_values(["timestamp", "symbol"])
        base_market_features = [name for name in market_feature_names(frame) if not name.startswith("_")]
        required_for_cross = base_market_features + ["close", "_high", "_low"]
        frame = frame.dropna(subset=required_for_cross).reset_index(drop=True)
        fresh = pd.Series(True, index=frame.index)
        for timeframe in REQUIRED_TIMEFRAMES:
            age = pd.to_datetime(frame["timestamp"], utc=True) - pd.to_datetime(frame[f"source_timestamp_{timeframe}"], utc=True)
            fresh &= (age >= pd.Timedelta(0)) & (age < pd.Timedelta(minutes=INTERVAL_MINUTES[timeframe]))
        frame = frame[fresh].reset_index(drop=True)
        frame = add_cross_sectional_features(frame)
        features = base_market_features + CROSS_ASSET_FEATURES + MARKET_CONTEXT_FEATURES
        frame = frame.dropna(subset=features).reset_index(drop=True)
        # Any asset removed for an unavailable feature must also be removed from
        # the contemporaneous ranking universe before ranks/context are finalized.
        btc_times = frame.loc[frame["symbol"] == "BTCUSDT", "timestamp"]
        frame = refresh_cross_sectional_features(frame[frame["timestamp"].isin(btc_times)])
        frame = frame.dropna(subset=features).reset_index(drop=True)
        frame = add_future_targets(frame, self.config)
        frame = chronological_group_split(frame, self.config.train_fraction, self.config.validation_fraction)
        frame = frame.drop(columns=["_high", "_low"])
        usable = {symbol: {"first_usable": group["timestamp"].min().isoformat(),
                           "last_usable": group["timestamp"].max().isoformat()}
                  for symbol, group in frame.groupby("symbol", sort=True)}
        validation = validate_dataset(frame, features)
        if not validation["passed"]:
            raise ValueError(f"Dataset validation failed: {validation['issues']}")
        metadata = self._metadata(frame, features, coverage, usable, validation)
        return frame, features, metadata, coverage

    def _metadata(self, frame, features, coverage, usable, validation):
        periods, class_distribution = {}, {}
        for split, group in frame.groupby("split", sort=False):
            no_trade_by_time = group.groupby("timestamp")["no_trade_preferred"].first()
            periods[split] = {"start": group["timestamp"].min().isoformat(), "end": group["timestamp"].max().isoformat(),
                              "target_end": group["target_timestamp"].max().isoformat(), "rows": len(group),
                              "timestamps": int(group["timestamp"].nunique())}
            class_distribution[split] = {
                "top_1": int(group["is_top_1"].sum()), "positive_after_costs": int(group["is_positive_after_costs"].sum()),
                "top_3": int(group["is_top_3"].sum()),
                "positive_after_costs_rate": float(group["is_positive_after_costs"].mean()),
                "no_trade_timestamps": int(no_trade_by_time.sum()),
                "no_trade_timestamp_rate": float(no_trade_by_time.mean()),
            }
        included = coverage[coverage["included"]]["symbol"].tolist()
        excluded = coverage[~coverage["included"]][["symbol", "exclusion_reason"]].to_dict("records")
        market_features = [name for name in features if name not in CROSS_ASSET_FEATURES + MARKET_CONTEXT_FEATURES]
        return {
            "dataset_version": DATASET_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True, "config": asdict(self.config), "symbols": included,
            "excluded_symbols": excluded, "timeframes": list(REQUIRED_TIMEFRAMES), "decision_grid": "1h closed candles",
            "point_in_time_universe": {"rule": "all required timeframes present, coverage threshold met, all feature values available at T",
                                       "usable_periods": usable, "minimum_size": int(frame["eligible_universe_size"].min()),
                                       "maximum_size": int(frame["eligible_universe_size"].max())},
            "features": {"all": features, "count": len(features), "market": market_features,
                         "market_count": len(market_features), "cross_asset": CROSS_ASSET_FEATURES,
                         "cross_asset_count": len(CROSS_ASSET_FEATURES), "market_context": MARKET_CONTEXT_FEATURES,
                         "market_context_count": len(MARKET_CONTEXT_FEATURES),
                         "rank_definition": "feature rank is cross-sectional percentile in [0,1], higher value means higher raw metric"},
            "targets": {"columns": TARGET_COLUMNS, "horizon_hours": self.config.horizon_hours,
                        "round_trip_cost": self.config.round_trip_cost,
                        "net_return_formula": "close(T+4h)/close(T)-1-2*(commission+slippage)",
                        "mae_mfe_window": "next four closed 1h candles after T",
                        "risk_adjusted_formula": "future_net_return_4h / max(volatility_20_1h, 1e-6)",
                        "no_trade_rule": f"best_available_net_return < {self.config.meaningful_net_threshold}"},
            "schema": {"metadata_columns": METADATA_COLUMNS, "feature_columns": features,
                       "target_columns": TARGET_COLUMNS, "future_prices_exported": False},
            "rows": len(frame), "timestamps": int(frame["timestamp"].nunique()), "periods": periods,
            "class_distribution": class_distribution, "validation": validation,
            "limitations": ["Only symbols with complete local 15m/1h/4h caches are included.",
                            "The three-asset universe is too small for stable production cross-sectional ranking.",
                            "Dataset construction is research-only; no model is trained or connected to Batch 3."],
        }


def export_artifacts(frame, metadata, coverage, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    dataset = output / "cross_asset_v1.csv.gz"
    frame.to_csv(dataset, index=False, compression="gzip", float_format="%.10g")
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    point_in_time = frame.groupby("timestamp", sort=True).agg(
        eligible_symbols=("symbol", lambda values: ",".join(sorted(values))),
        universe_size=("symbol", "size"),
    ).reset_index()
    point_in_time.to_csv(output / "universe.csv", index=False)
    coverage.to_csv(output / "symbol_coverage.csv", index=False)
    coverage_payload = {"dataset_version": DATASET_VERSION, "symbols": coverage.to_dict("records")}
    (output / "coverage.json").write_text(json.dumps(coverage_payload, indent=2), encoding="utf-8")
    frame.groupby("split", group_keys=False).head(5).to_csv(output / "sample_rows.csv", index=False)
    return {"dataset": str(dataset), "metadata": str(output / "metadata.json"),
            "coverage": str(output / "coverage.json"), "universe": str(output / "universe.csv"),
            "symbol_coverage": str(output / "symbol_coverage.csv"),
            "sample_rows": str(output / "sample_rows.csv")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the local, research-only cross-asset-v1 dataset")
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2025-12-31T23:59:59")
    parser.add_argument("--minimum-coverage", type=float, default=.95)
    args = parser.parse_args(argv)
    config = CrossAssetConfig(start=args.start, end=args.end, history_dir=args.history_dir,
                              output_dir=args.output, minimum_coverage=args.minimum_coverage)
    started = perf_counter()
    frame, features, metadata, coverage = CrossAssetDatasetBuilder(config).build()
    metadata["build_runtime_seconds"] = round(perf_counter() - started, 3)
    paths = export_artifacts(frame, metadata, coverage, args.output)
    universe = frame.groupby("timestamp")["symbol"].size()
    print(json.dumps({"dataset_version": DATASET_VERSION, "rows": len(frame), "features": len(features),
                      "symbols": metadata["symbols"], "timestamps": metadata["timestamps"],
                      "average_universe_size": float(universe.mean()), "minimum_universe_size": int(universe.min()),
                      "maximum_universe_size": int(universe.max()), "date_coverage": metadata["point_in_time_universe"]["usable_periods"],
                      "periods": metadata["periods"], "label_distribution": metadata["class_distribution"],
                      "build_runtime_seconds": metadata["build_runtime_seconds"], "files": paths}, indent=2))


if __name__ == "__main__":
    main()
