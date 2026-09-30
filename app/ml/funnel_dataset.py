"""Build the research-only, leakage-safe regime and candidate funnel datasets.

This module transforms the frozen cross_asset_v2 dataset. It does not train a
model and has no dependency on brokers, runners, Telegram, or live APIs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "ml" / "cross_asset_v2" / "cross_asset_v2.csv.gz"
DEFAULT_SOURCE_METADATA = PROJECT_ROOT / "data" / "ml" / "cross_asset_v2" / "metadata.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "ml" / "funnel_v1"
DEFAULT_RANKINGS = PROJECT_ROOT / "data" / "ml" / "reports" / "opportunity_ranker_v2" / "test_rankings.csv"

DATASET_VERSION = "funnel-dataset-v1"
MEANINGFUL_THRESHOLD = 0.001
STRONG_THRESHOLD = 0.005
MEDIUM_UPPER_THRESHOLD = 0.010
ROUND_TRIP_COST = 0.0018

ADDITIONAL_CANDIDATE_FEATURES = [
    "asset_vs_universe_momentum",
    "volatility_relative_to_universe",
    "trend_alignment_score",
    "momentum_alignment_score",
    "volume_confirmation_score",
    "breakout_proximity_1h",
]

REGIME_FEATURE_COLUMNS = [
    "btc_return_1h", "btc_return_4h", "btc_return_24h", "btc_trend",
    "btc_volatility", "btc_rsi", "btc_atr_fraction", "btc_ema_position",
    "median_universe_return_1h", "median_universe_return_4h",
    "median_universe_return_24h", "fraction_assets_above_ema",
    "median_universe_rsi", "cross_sectional_dispersion", "market_breadth",
    "average_universe_volatility", "positive_return_fraction_1h",
    "volume_expansion_fraction", "median_volume_zscore",
    "top_bottom_return_dispersion_24h",
]

REGIME_TARGET_COLUMNS = [
    "any_profitable_asset_4h", "strong_opportunity_exists_4h",
    "broad_positive_regime", "best_available_net_return", "best_net_return",
    "second_best_net_return", "median_net_return", "best_minus_median",
    "best_minus_second", "number_profitable_assets", "fraction_profitable_assets",
    "future_cross_sectional_dispersion", "regime_category",
    "actual_best_symbol", "ideal_candidate_count", "candidate_set_should_be_empty",
]

CANDIDATE_LABEL_COLUMNS = [
    "candidate_positive", "candidate_strong", "candidate_top_half",
    "candidate_top_2", "ideal_candidate",
]


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    raise TypeError(f"Cannot JSON serialize {type(value)!r}")


def load_source(path: str | Path = DEFAULT_SOURCE) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in ["timestamp", "target_timestamp", "source_open_time",
                   "source_timestamp_15m", "source_timestamp_1h", "source_timestamp_4h"]:
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def add_candidate_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add six point-in-time filters using only row/current-universe data at T."""
    result = frame.copy()
    groups = result.groupby("timestamp", sort=False)
    median_momentum = groups["momentum_4_1h"].transform("median")
    median_volatility = groups["volatility_20_1h"].transform("median").replace(0, np.nan)
    result["asset_vs_universe_momentum"] = result["momentum_4_1h"] - median_momentum
    result["volatility_relative_to_universe"] = (
        result["volatility_20_1h"] / median_volatility
    ).fillna(1.0)
    result["trend_alignment_score"] = sum(
        (result[f"ema_distance_20_{tf}"] > 0).astype(int) for tf in ("15m", "1h", "4h")
    )
    result["momentum_alignment_score"] = sum(
        (result[f"momentum_4_{tf}"] > 0).astype(int) for tf in ("15m", "1h", "4h")
    )
    result["volume_confirmation_score"] = sum(
        (result[f"volume_zscore_20_{tf}"] > 0).astype(int) for tf in ("15m", "1h", "4h")
    )
    # distance_recent_high is <= 0; 1.0 means at the rolling high.
    result["breakout_proximity_1h"] = 1.0 + result["distance_recent_high_20_1h"]
    return result


def add_candidate_labels(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    future = result["future_net_return_4h"]
    universe = result["eligible_universe_size"]
    result["candidate_positive"] = (future > MEANINGFUL_THRESHOLD).astype(int)
    result["candidate_strong"] = (future > STRONG_THRESHOLD).astype(int)
    result["candidate_top_half"] = (
        (result["future_return_rank"] <= np.ceil(universe / 2)) & (future > 0)
    ).astype(int)
    result["candidate_top_2"] = ((result["future_return_rank"] <= 2) & (future > 0)).astype(int)
    # Primary research definition: up to two genuinely meaningful candidates.
    result["ideal_candidate"] = (
        (result["future_return_rank"] <= 2) & (future > MEANINGFUL_THRESHOLD)
    ).astype(int)
    groups = result.groupby("timestamp", sort=False)
    result["ideal_candidate_count"] = groups["ideal_candidate"].transform("sum").astype(int)
    best_idx = groups["future_net_return_4h"].idxmax()
    best_map = result.loc[best_idx].set_index("timestamp")["symbol"]
    result["actual_best_symbol"] = result["timestamp"].map(best_map)
    result["candidate_set_should_be_empty"] = (groups["future_net_return_4h"].transform("max") <= MEANINGFUL_THRESHOLD).astype(int)
    result["actual_best_in_candidate_set"] = (
        (result["ideal_candidate_count"] > 0) & (groups["future_net_return_4h"].transform("max") > MEANINGFUL_THRESHOLD)
    ).astype(int)
    return result


def add_horizon_targets(frame: pd.DataFrame, hours=(2, 8), cost=ROUND_TRIP_COST) -> pd.DataFrame:
    """Add diagnostic targets only; require exact time and same split to avoid boundary leakage."""
    result = frame.sort_values(["symbol", "timestamp"]).copy()
    groups = result.groupby("symbol", sort=False)
    for horizon in hours:
        future_close = groups["close"].shift(-horizon)
        future_time = groups["timestamp"].shift(-horizon)
        future_split = groups["split"].shift(-horizon)
        exact = future_time.eq(result["timestamp"] + pd.Timedelta(hours=horizon))
        same_split = future_split.eq(result["split"])
        result[f"future_net_return_{horizon}h"] = (
            future_close / result["close"] - 1 - cost
        ).where(exact & same_split)
    return result.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def build_regime_dataset(candidate: pd.DataFrame) -> pd.DataFrame:
    """Aggregate current market features and future research labels once per T."""
    current = candidate.groupby("timestamp", sort=False)
    regime = current.agg(
        split=("split", "first"),
        eligible_universe_size=("symbol", "size"),
        median_universe_return_1h=("return_1_1h", "median"),
        median_universe_return_4h=("return_4h", "median"),
        median_universe_return_24h=("return_24h", "median"),
        fraction_assets_above_ema=("ema_distance_20_1h", lambda x: float((x > 0).mean())),
        median_universe_rsi=("rsi_14_1h", "median"),
        cross_sectional_dispersion=("return_1_1h", lambda x: float(x.std(ddof=0))),
        market_breadth=("return_1_1h", lambda x: float((x > 0).mean())),
        average_universe_volatility=("volatility_20_1h", "mean"),
        positive_return_fraction_1h=("return_1_1h", lambda x: float((x > 0).mean())),
        volume_expansion_fraction=("volume_zscore_20_1h", lambda x: float((x > 0).mean())),
        median_volume_zscore=("volume_zscore_20_1h", "median"),
        top_return_24h=("return_24h", "max"),
        bottom_return_24h=("return_24h", "min"),
        best_net_return=("future_net_return_4h", "max"),
        median_net_return=("future_net_return_4h", "median"),
        number_profitable_assets=("future_net_return_4h", lambda x: int((x > 0).sum())),
        fraction_profitable_assets=("future_net_return_4h", lambda x: float((x > 0).mean())),
        future_cross_sectional_dispersion=("future_net_return_4h", lambda x: float(x.std(ddof=0))),
        ideal_candidate_count=("ideal_candidate", "sum"),
        candidate_set_should_be_empty=("candidate_set_should_be_empty", "first"),
        actual_best_symbol=("actual_best_symbol", "first"),
    ).reset_index()
    regime["top_bottom_return_dispersion_24h"] = regime.pop("top_return_24h") - regime.pop("bottom_return_24h")

    second = current["future_net_return_4h"].apply(
        lambda x: float(x.nlargest(2).iloc[-1]) if len(x) >= 2 else float(x.iloc[0])
    )
    regime["second_best_net_return"] = regime["timestamp"].map(second)
    regime["best_available_net_return"] = regime["best_net_return"]
    regime["best_minus_median"] = regime["best_net_return"] - regime["median_net_return"]
    regime["best_minus_second"] = regime["best_net_return"] - regime["second_best_net_return"]
    regime["any_profitable_asset_4h"] = (regime["best_net_return"] > MEANINGFUL_THRESHOLD).astype(int)
    regime["strong_opportunity_exists_4h"] = (regime["best_net_return"] > STRONG_THRESHOLD).astype(int)
    regime["broad_positive_regime"] = (regime["fraction_profitable_assets"] >= 0.5).astype(int)
    regime["regime_category"] = pd.cut(
        regime["best_net_return"],
        bins=[-np.inf, MEANINGFUL_THRESHOLD, STRONG_THRESHOLD, MEDIUM_UPPER_THRESHOLD, np.inf],
        labels=["NO_OPPORTUNITY", "WEAK", "MEDIUM", "STRONG"],
        right=True,
    ).astype(str)

    btc = candidate[candidate["symbol"] == "BTCUSDT"].set_index("timestamp")
    # Keep both documented trend and EMA-position fields, with the same stable source semantic.
    regime["btc_return_1h"] = regime["timestamp"].map(btc["return_1_1h"])
    regime["btc_return_4h"] = regime["timestamp"].map(btc["return_4h"])
    regime["btc_return_24h"] = regime["timestamp"].map(btc["return_24h"])
    regime["btc_trend"] = regime["timestamp"].map(btc["supertrend_state_1h"])
    regime["btc_volatility"] = regime["timestamp"].map(btc["volatility_20_1h"])
    regime["btc_rsi"] = regime["timestamp"].map(btc["rsi_14_1h"])
    regime["btc_atr_fraction"] = regime["timestamp"].map(btc["atr_fraction_14_1h"])
    regime["btc_ema_position"] = regime["timestamp"].map(btc["ema_distance_20_1h"])
    columns = ["timestamp", "split", "eligible_universe_size"] + REGIME_FEATURE_COLUMNS + REGIME_TARGET_COLUMNS
    return regime[columns].sort_values("timestamp").reset_index(drop=True)


def validate_safety(candidate: pd.DataFrame, regime: pd.DataFrame, candidate_features: list[str]) -> dict:
    forbidden_tokens = ("future_", "candidate_", "ideal_", "best_net", "second_best", "regime_category")
    issues = []
    bad_candidate = [c for c in candidate_features if c.startswith(forbidden_tokens)]
    bad_regime = [c for c in REGIME_FEATURE_COLUMNS if c.startswith(forbidden_tokens)]
    if bad_candidate or bad_regime:
        issues.append("future_or_label_field_in_features")
    if candidate.groupby("timestamp")["split"].nunique().max() != 1:
        issues.append("candidate_timestamp_crosses_split")
    if regime.groupby("timestamp")["split"].nunique().max() != 1:
        issues.append("regime_timestamp_crosses_split")
    if candidate.duplicated(["timestamp", "symbol"]).any() or regime.duplicated("timestamp").any():
        issues.append("duplicate_keys")
    if candidate[candidate_features].isna().any().any() or regime[REGIME_FEATURE_COLUMNS].isna().any().any():
        issues.append("missing_features")
    target_delta = candidate["target_timestamp"] - candidate["timestamp"]
    if not target_delta.eq(pd.Timedelta(hours=4)).all():
        issues.append("invalid_primary_horizon")
    ordered = {"train": 0, "validation": 1, "test": 2}
    split_bounds = regime.groupby("split")["timestamp"].agg(["min", "max"])
    used = [name for name in ordered if name in split_bounds.index]
    for left, right in zip(used, used[1:]):
        if split_bounds.loc[left, "max"] >= split_bounds.loc[right, "min"]:
            issues.append("non_chronological_split")
    return {
        "passed": not issues,
        "issues": issues,
        "candidate_feature_count": len(candidate_features),
        "regime_feature_count": len(REGIME_FEATURE_COLUMNS),
        "candidate_feature_target_intersection": sorted(set(candidate_features) & set(CANDIDATE_LABEL_COLUMNS)),
        "regime_feature_target_intersection": sorted(set(REGIME_FEATURE_COLUMNS) & set(REGIME_TARGET_COLUMNS)),
    }


def label_distribution(candidate: pd.DataFrame, regime: pd.DataFrame) -> dict:
    output = {"regime": {}, "candidate": {}}
    for split_name, part in [("all", regime)] + list(regime.groupby("split", sort=False)):
        output["regime"][split_name] = {
            label: {"positive": int(part[label].sum()), "negative": int(len(part) - part[label].sum()),
                    "positive_rate": float(part[label].mean())}
            for label in ["any_profitable_asset_4h", "strong_opportunity_exists_4h", "broad_positive_regime"]
        }
        output["regime"][split_name]["regime_category"] = part["regime_category"].value_counts().to_dict()
    for label in CANDIDATE_LABEL_COLUMNS:
        output["candidate"][label] = {
            "overall": {"positive": int(candidate[label].sum()), "positive_rate": float(candidate[label].mean())},
            "per_split": candidate.groupby("split")[label].agg(["sum", "mean", "count"]).to_dict("index"),
            "per_symbol": candidate.groupby("symbol")[label].agg(["sum", "mean", "count"]).to_dict("index"),
        }
    return output


def baseline_funnel(candidate: pd.DataFrame) -> pd.DataFrame:
    rows = []
    work = candidate.copy()
    momentum_rank = work.groupby("timestamp")["momentum_4_1h"].rank(method="first", ascending=False)
    relative_rank = work.groupby("timestamp")["return_rank_24h"].rank(method="first", ascending=False)
    masks = {
        "top_2_momentum": momentum_rank <= 2,
        "above_ema": work["ema_distance_20_1h"] > 0,
        "top_3_relative_strength": relative_rank <= 3,
        "all_assets": pd.Series(True, index=work.index),
        "no_assets": pd.Series(False, index=work.index),
        "oracle_winner_impossible": (work["symbol"] == work["actual_best_symbol"]) & (work["candidate_set_should_be_empty"] == 0),
    }
    opportunity = work.drop_duplicates("timestamp").set_index("timestamp")["candidate_set_should_be_empty"].eq(0)
    for name, mask in masks.items():
        selected = work[mask]
        sizes = selected.groupby("timestamp").size().reindex(opportunity.index, fill_value=0)
        retained = selected[selected["symbol"] == selected["actual_best_symbol"]]["timestamp"].drop_duplicates()
        retained_flags = pd.Series(False, index=opportunity.index)
        retained_flags.loc[retained_flags.index.isin(retained)] = True
        meaningful = opportunity[opportunity].index
        rows.append({
            "baseline": name,
            "impossible_future_oracle": name == "oracle_winner_impossible",
            "winner_retained_rate": float(retained_flags.loc[meaningful].mean()) if len(meaningful) else np.nan,
            "average_candidate_set_size": float(sizes.mean()),
            "median_candidate_set_size": float(sizes.median()),
            "empty_set_rate": float((sizes == 0).mean()),
            "candidate_precision": float(selected["candidate_positive"].mean()) if len(selected) else np.nan,
            "selected_assets_future_average_return": float(selected["future_net_return_4h"].mean()) if len(selected) else np.nan,
            "selected_rows": int(len(selected)),
        })
    return pd.DataFrame(rows)


def regime_error_analysis(regime: pd.DataFrame, rankings_path: str | Path = DEFAULT_RANKINGS) -> pd.DataFrame:
    path = Path(rankings_path)
    columns = ["analysis", "group", "timestamps", "ranker_top1_accuracy", "mean_best_return", "mean_future_dispersion"]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    rankings = pd.read_csv(path, usecols=["timestamp", "future_return_rank", "predicted_rank", "symbol"])
    rankings["timestamp"] = pd.to_datetime(rankings["timestamp"], utc=True)
    winners = rankings[rankings["predicted_rank"] == 1].copy()
    winners["ranker_correct"] = winners["future_return_rank"].eq(1)
    joined = winners.merge(regime, on="timestamp", how="inner", validate="one_to_one")
    train_cut = regime.loc[regime["split"] == "train", "future_cross_sectional_dispersion"].median()
    joined["dispersion_band"] = np.where(joined["future_cross_sectional_dispersion"] <= train_cut, "LOW", "HIGH")
    joined["market_direction"] = np.where(joined["fraction_profitable_assets"] < 0.5, "BROAD_DOWN", "BROAD_POSITIVE")
    specs = [("regime_category", "regime_category"), ("market_direction", "market_direction"),
             ("future_dispersion", "dispersion_band")]
    rows = []
    for analysis, field in specs:
        for name, part in joined.groupby(field, sort=False):
            rows.append({"analysis": analysis, "group": name, "timestamps": len(part),
                         "ranker_top1_accuracy": part["ranker_correct"].mean(),
                         "mean_best_return": part["best_net_return"].mean(),
                         "mean_future_dispersion": part["future_cross_sectional_dispersion"].mean()})
    return pd.DataFrame(rows, columns=columns)


def dispersion_analysis(regime: pd.DataFrame, rankings_path: str | Path = DEFAULT_RANKINGS) -> pd.DataFrame:
    ranker = None
    if Path(rankings_path).exists():
        raw = pd.read_csv(rankings_path, usecols=["timestamp", "future_return_rank", "predicted_rank"])
        raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
        ranker = raw[raw["predicted_rank"] == 1][["timestamp", "future_return_rank"]].copy()
        ranker["ranker_correct"] = ranker["future_return_rank"].eq(1)
    rows = []
    for metric in ["best_minus_second", "best_minus_median", "future_cross_sectional_dispersion"]:
        train = regime.loc[regime["split"] == "train", metric]
        cuts = [-np.inf, *train.quantile([.25, .5, .75]).tolist(), np.inf]
        labels = ["Q1_LOW", "Q2", "Q3", "Q4_HIGH"]
        bucket = pd.cut(regime[metric], bins=cuts, labels=labels, include_lowest=True, duplicates="drop")
        work = regime.assign(_bucket=bucket)
        if ranker is not None:
            work = work.merge(ranker[["timestamp", "ranker_correct"]], on="timestamp", how="left")
        else:
            work["ranker_correct"] = np.nan
        for (split, name), part in work.groupby(["split", "_bucket"], observed=True):
            rows.append({"metric": metric, "split": split, "bucket": str(name), "timestamps": len(part),
                         "mean_metric": part[metric].mean(), "mean_best_return": part["best_net_return"].mean(),
                         "opportunity_rate": part["any_profitable_asset_4h"].mean(),
                         "ranker_top1_accuracy": part["ranker_correct"].mean()})
    return pd.DataFrame(rows)


def horizon_diagnostic(candidate: pd.DataFrame) -> pd.DataFrame:
    rows = []
    winner_by_horizon = {}
    for horizon in (2, 4, 8):
        column = f"future_net_return_{horizon}h"
        valid = candidate.dropna(subset=[column]).copy()
        if valid.empty:
            continue
        groups = valid.groupby("timestamp", sort=False)
        best_idx = groups[column].idxmax()
        winners = valid.loc[best_idx].set_index("timestamp")["symbol"]
        winner_by_horizon[horizon] = winners
        best = groups[column].max()
        dispersion = groups[column].std(ddof=0)
        rows.append({"horizon_hours": horizon, "rows": len(valid), "timestamps": valid["timestamp"].nunique(),
                     "asset_positive_rate": (valid[column] > 0).mean(),
                     "no_opportunity_rate": (best <= MEANINGFUL_THRESHOLD).mean(),
                     "strong_opportunity_rate": (best > STRONG_THRESHOLD).mean(),
                     "mean_best_return": best.mean(), "mean_cross_sectional_dispersion": dispersion.mean(),
                     "winner_persistence_vs_4h": np.nan})
    if 4 in winner_by_horizon:
        reference = winner_by_horizon[4]
        for row in rows:
            horizon = row["horizon_hours"]
            common = reference.index.intersection(winner_by_horizon[horizon].index)
            row["winner_persistence_vs_4h"] = float((reference.loc[common] == winner_by_horizon[horizon].loc[common]).mean())
    return pd.DataFrame(rows)


def manual_validation(candidate: pd.DataFrame, regime: pd.DataFrame) -> dict:
    examples = []
    selectors = {
        "strong_opportunity": regime["regime_category"].eq("STRONG"),
        "weak_opportunity": regime["regime_category"].eq("WEAK"),
        "no_opportunity": regime["regime_category"].eq("NO_OPPORTUNITY"),
        "multiple_profitable_assets": regime["number_profitable_assets"].ge(4),
    }
    for name, mask in selectors.items():
        if not mask.any():
            continue
        row = regime[mask].iloc[len(regime[mask]) // 2]
        assets = candidate[candidate["timestamp"] == row["timestamp"]]
        examples.append({
            "case": name, "timestamp": row["timestamp"], "regime_category": row["regime_category"],
            "best_net_return": row["best_net_return"], "actual_best_symbol": row["actual_best_symbol"],
            "ideal_candidates": assets.loc[assets["ideal_candidate"] == 1, "symbol"].tolist(),
            "candidate_labels": assets.set_index("symbol")[CANDIDATE_LABEL_COLUMNS].to_dict("index"),
            "feature_time_only": True,
        })
    return {"checks": examples, "passed": len(examples) == 4}


def _periods(frame: pd.DataFrame) -> dict:
    return {name: {"start": part["timestamp"].min(), "end": part["timestamp"].max(),
                   "rows": len(part), "timestamps": part["timestamp"].nunique()}
            for name, part in frame.groupby("split", sort=False)}


def build(output_dir: str | Path = DEFAULT_OUTPUT, source_path: str | Path = DEFAULT_SOURCE,
          source_metadata_path: str | Path = DEFAULT_SOURCE_METADATA,
          rankings_path: str | Path = DEFAULT_RANKINGS) -> dict:
    started = perf_counter()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    with Path(source_metadata_path).open(encoding="utf-8") as handle:
        source_metadata = json.load(handle)
    base_features = list(source_metadata["schema"]["feature_columns"])
    candidate_features = base_features + ADDITIONAL_CANDIDATE_FEATURES

    candidate = load_source(source_path)
    candidate = add_candidate_features(candidate)
    candidate = add_candidate_labels(candidate)
    candidate = add_horizon_targets(candidate)
    regime = build_regime_dataset(candidate)
    safety = validate_safety(candidate, regime, candidate_features)
    if not safety["passed"]:
        raise ValueError(f"Funnel dataset safety validation failed: {safety['issues']}")

    distributions = label_distribution(candidate, regime)
    baselines = baseline_funnel(candidate)
    errors = regime_error_analysis(regime, rankings_path)
    dispersion = dispersion_analysis(regime, rankings_path)
    horizons = horizon_diagnostic(candidate)
    manual = manual_validation(candidate, regime)

    original_targets = list(source_metadata["schema"]["target_columns"])
    candidate_targets = original_targets + ["future_net_return_2h", "future_net_return_8h"] + CANDIDATE_LABEL_COLUMNS + [
        "ideal_candidate_count", "candidate_set_should_be_empty", "actual_best_in_candidate_set", "actual_best_symbol"
    ]
    candidate_metadata = list(source_metadata["schema"]["metadata_columns"])
    candidate_columns = list(dict.fromkeys(candidate_metadata + candidate_features + candidate_targets))
    candidate_export = candidate[candidate_columns]
    regime.to_csv(output / "regime_dataset.csv.gz", index=False, compression="gzip")
    candidate_export.to_csv(output / "candidate_dataset.csv.gz", index=False, compression="gzip")
    baselines.to_csv(output / "baseline_funnel.csv", index=False)
    errors.to_csv(output / "regime_analysis.csv", index=False)
    dispersion.to_csv(output / "dispersion_analysis.csv", index=False)
    horizons.to_csv(output / "horizon_diagnostic.csv", index=False)
    sample = pd.concat([
        regime.head(3).assign(dataset="regime"),
        candidate_export.head(3).assign(dataset="candidate"),
    ], ignore_index=True, sort=False)
    sample.to_csv(output / "sample_rows.csv", index=False)
    with (output / "label_distribution.json").open("w", encoding="utf-8") as handle:
        json.dump(distributions, handle, indent=2, default=_json_default)
    with (output / "manual_validation.json").open("w", encoding="utf-8") as handle:
        json.dump(manual, handle, indent=2, default=_json_default)

    metadata = {
        "dataset_version": DATASET_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True, "model_training_performed": False,
        "source_dataset": "cross-asset-v2", "source_path": str(Path(source_path).resolve()),
        "universe": source_metadata["symbols"], "timeframes": source_metadata["timeframes"],
        "decision_timeframe": "1h", "primary_horizon_hours": 4,
        "diagnostic_horizons_hours": [2, 4, 8],
        "cost_assumptions": {"commission_one_way": 0.0004, "slippage_one_way": 0.0005,
                             "round_trip_cost": ROUND_TRIP_COST},
        "label_definitions": {
            "meaningful_threshold": MEANINGFUL_THRESHOLD, "strong_threshold": STRONG_THRESHOLD,
            "regime_categories": {"NO_OPPORTUNITY": "best <= 0.10%", "WEAK": "0.10% < best <= 0.50%",
                                  "MEDIUM": "0.50% < best <= 1.00%", "STRONG": "best > 1.00%"},
            "broad_positive_regime": "at least 50% of eligible assets have future net return > 0",
            "candidate_top_half": "future rank in top ceil(N/2) and future net return > 0",
            "candidate_top_2": "future rank <= 2 and future net return > 0",
            "ideal_candidate": "future rank <= 2 and future net return > 0.10%",
        },
        "schema": {
            "regime_feature_columns": REGIME_FEATURE_COLUMNS, "regime_feature_count": len(REGIME_FEATURE_COLUMNS),
            "regime_target_columns": REGIME_TARGET_COLUMNS,
            "regime_metadata_columns": ["timestamp", "split", "eligible_universe_size"],
            "candidate_feature_columns": candidate_features, "candidate_feature_count": len(candidate_features),
            "additional_candidate_features": ADDITIONAL_CANDIDATE_FEATURES,
            "candidate_target_columns": candidate_targets, "candidate_metadata_columns": candidate_metadata,
        },
        "split_rules": {"type": "grouped chronological inherited from cross_asset_v2",
                        "same_timestamp_one_split": True, "primary_target_purge_hours": 4,
                        "diagnostic_horizons_require_same_split": True},
        "rows": {"regime": len(regime), "candidate": len(candidate_export)},
        "periods": {"regime": _periods(regime), "candidate": _periods(candidate_export)},
        "average_ideal_candidate_set_size": float(regime["ideal_candidate_count"].mean()),
        "empty_set_frequency": float(regime["candidate_set_should_be_empty"].mean()),
        "regime_distribution": regime["regime_category"].value_counts().to_dict(),
        "safety_validation": safety, "manual_validation": manual,
        "build_runtime_seconds": perf_counter() - started,
        "limitations": [
            "Labels are hypothetical close-to-close outcomes and exclude order-book effects beyond fixed slippage.",
            "The ideal candidate set and oracle baseline use future returns and are impossible in live inference.",
            "2h/8h targets are diagnostic only and are null where an exact same-split horizon is unavailable.",
            "Regime and candidate labels are research proposals; this milestone deliberately selects no production target.",
        ],
    }
    with (output / "metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, default=_json_default)
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build funnel-dataset-v1 from frozen cross_asset_v2")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--source-metadata", default=str(DEFAULT_SOURCE_METADATA))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--rankings", default=str(DEFAULT_RANKINGS))
    args = parser.parse_args(argv)
    metadata = build(args.output_dir, args.source, args.source_metadata, args.rankings)
    print(json.dumps({"dataset_version": metadata["dataset_version"], "rows": metadata["rows"],
                      "regime_features": metadata["schema"]["regime_feature_count"],
                      "candidate_features": metadata["schema"]["candidate_feature_count"],
                      "runtime_seconds": metadata["build_runtime_seconds"],
                      "output_dir": str(Path(args.output_dir).resolve())}, indent=2))


if __name__ == "__main__":
    main()
