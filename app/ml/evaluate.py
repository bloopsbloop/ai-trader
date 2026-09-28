"""Evaluate the registered ML v1 on untouched test data and walk-forward folds."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.dataset import LeakageSafePreprocessor, PROJECT_ROOT, walk_forward_splits
from app.ml.metrics import (buy_hold_baseline, classification_metrics, select_threshold,
                            simulate_trades)
from app.ml.model import HistGradientBoostingDecisionModel, balanced_sample_weights
from app.ml.train import (DEFAULT_DATASET, DEFAULT_DATASET_METADATA, DEFAULT_REGISTRY,
                          DEFAULT_THRESHOLDS, file_hash, load_dataset)

DEFAULT_REPORTS = PROJECT_ROOT / "data" / "ml" / "reports"


def _clean(value):
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(value) else None
    return value


def _preprocessor(rows, features):
    fitting = rows.copy()
    fitting["split"] = "train"
    return LeakageSafePreprocessor().fit(fitting, features)


def heuristic_probability(frame):
    trend = sum((frame[f"ema_distance_20_{tf}"] > 0).astype(float) for tf in ("15m", "1h", "4h")) / 3
    momentum = np.clip(.5 + frame["momentum_4_1h"].to_numpy(dtype=float) * 10, 0, 1)
    volume = np.clip((frame["volume_zscore_20_15m"].to_numpy(dtype=float) + 2) / 4, 0, 1)
    market = .5 * trend.to_numpy() + .3 * momentum + .2 * volume
    confirmed = (trend.to_numpy() == 1) & (frame["volume_zscore_20_15m"].to_numpy(dtype=float) >= .1)
    return np.where(confirmed, market, np.minimum(market, .74))


def trend_probability(frame):
    signals = [(frame[f"ema_distance_20_{tf}"] > 0).astype(float) for tf in ("15m", "1h", "4h")]
    signals.append((frame["momentum_4_1h"] > 0).astype(float))
    return sum(signals).to_numpy() / len(signals)


def evaluate_baselines(train, test):
    prior = float(train["label"].mean())
    configurations = {"heuristic_batch3_market": (heuristic_probability(test), .75),
                      "simple_trend": (trend_probability(test), .75),
                      "class_prior_no_skill": (np.full(len(test), prior), .5)}
    result = {}
    for name, (probability, threshold) in configurations.items():
        result[name] = {"classification": classification_metrics(test["label"], probability, threshold),
                        "trading": simulate_trades(test, probability, threshold)}
    result["buy_hold"] = {"classification": None, "trading": buy_hold_baseline(test)}
    return result


def walk_forward_evaluation(frame, dataset_metadata, model_metadata, thresholds=DEFAULT_THRESHOLDS):
    features = list(dataset_metadata["features"])
    parameters = dict(model_metadata["selected_configuration"]["parameters"])
    weighted = model_metadata["selected_configuration"]["class_weight"] == "balanced"
    seed = model_metadata["seed"]
    results = []
    for split in walk_forward_splits(frame, folds=3):
        outer_train = frame.loc[split["train"]].copy()
        outer_validation = frame.loc[split["validation"]].copy()
        times = sorted(pd.to_datetime(outer_train["timestamp"], utc=True).unique())
        boundary = times[int(len(times) * .8)]
        stamps = pd.to_datetime(outer_train["timestamp"], utc=True)
        targets = pd.to_datetime(outer_train["target_timestamp"], utc=True)
        inner_train = outer_train[(stamps < boundary) & (targets < boundary)].copy()
        inner_validation = outer_train[stamps >= boundary].copy()
        inner_preprocessor = _preprocessor(inner_train, features)
        inner_model = HistGradientBoostingDecisionModel(inner_preprocessor.feature_names_, inner_preprocessor.medians_, parameters, seed)
        inner_model.fit(inner_train, inner_train["label"], balanced_sample_weights(inner_train["label"]) if weighted else None)
        inner_probability = inner_model.predict_proba(inner_validation)
        selected, _ = select_threshold(inner_validation, inner_probability, inner_validation["label"], thresholds,
                                       minimum_trades=min(100, max(10, len(inner_validation) // 100)))
        outer_preprocessor = _preprocessor(outer_train, features)
        outer_model = HistGradientBoostingDecisionModel(outer_preprocessor.feature_names_, outer_preprocessor.medians_, parameters, seed)
        outer_model.fit(outer_train, outer_train["label"], balanced_sample_weights(outer_train["label"]) if weighted else None)
        probability = outer_model.predict_proba(outer_validation)
        threshold = selected["threshold"]
        results.append({"fold": split["fold"], "train_rows": len(outer_train),
                        "validation_rows": len(outer_validation), "threshold": threshold,
                        "classification": classification_metrics(outer_validation["label"], probability, threshold),
                        "trading": simulate_trades(outer_validation, probability, threshold),
                        "threshold_selected_on": {"start": inner_validation["timestamp"].min().isoformat(),
                                                  "end": inner_validation["timestamp"].max().isoformat(),
                                                  "rows": len(inner_validation)},
                        "evaluated_period": {"start": outer_validation["timestamp"].min().isoformat(),
                                             "end": outer_validation["timestamp"].max().isoformat()}})
    numeric = ("roc_auc", "pr_auc", "precision", "recall", "f1", "brier")
    trading = ("net_return", "realized_pnl", "trade_count", "win_rate", "profit_factor", "expectancy", "max_drawdown", "fees", "turnover")
    aggregate = {"folds": len(results),
                 "classification_mean": {key: float(np.mean([row["classification"][key] for row in results])) for key in numeric},
                 "trading_mean": {key: float(np.mean([row["trading"][key] for row in results])) for key in trading},
                 "trading_total_pnl": float(sum(row["trading"]["realized_pnl"] for row in results)),
                 "trades_total": int(sum(row["trading"]["trade_count"] for row in results))}
    return results, aggregate


def _flatten_row(name, classification, trading):
    row = {"name": name}
    if classification:
        row.update({f"ml_{key}": value for key, value in classification.items() if key != "confusion_matrix"})
    if trading:
        row.update({f"trading_{key}": value for key, value in trading.items() if key != "exposure_note"})
    return _clean(row)


def _write_csv(path, rows):
    columns = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def evaluate_and_export(registry=DEFAULT_REGISTRY, dataset=DEFAULT_DATASET,
                        dataset_metadata_path=DEFAULT_DATASET_METADATA, reports=DEFAULT_REPORTS):
    registry, reports = Path(registry), Path(reports)
    metadata_path = registry / "metadata.json"
    model_path = registry / "model.joblib"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata["dataset"]["sha256"] != file_hash(dataset):
        raise ValueError("Dataset hash differs from the training artifact")
    frame, dataset_metadata = load_dataset(dataset, dataset_metadata_path)
    model = HistGradientBoostingDecisionModel.load(model_path)
    threshold = float(metadata["decision_threshold"])
    train = frame[frame["split"] == "train"]
    test = frame[frame["split"] == "test"]
    probability = model.predict_proba(test)
    test_metrics = classification_metrics(test["label"], probability, threshold)
    test_trading = simulate_trades(test, probability, threshold)
    baselines = evaluate_baselines(train, test)
    training_config = json.loads((registry / "training_config.json").read_text(encoding="utf-8"))
    walk, walk_aggregate = walk_forward_evaluation(frame, dataset_metadata, metadata,
                                                   tuple(training_config["threshold_grid"]))
    comparison = {"ml_beats_heuristic_net_return": test_trading["net_return"] > baselines["heuristic_batch3_market"]["trading"]["net_return"],
                  "ml_beats_trend_net_return": test_trading["net_return"] > baselines["simple_trend"]["trading"]["net_return"],
                  "ml_beats_buy_hold_net_return": test_trading["net_return"] > baselines["buy_hold"]["trading"]["net_return"],
                  "comparison_note": "Buy & Hold uses equal-weight symbols sharing 10% total exposure; ML uses one 10% position at a time."}
    summary = {"model_version": metadata["model_version"], "model_family": metadata["model_family"],
               "decision_threshold": threshold, "dataset_rows": len(frame), "feature_count": len(model.feature_names()),
               "periods": dataset_metadata["periods"], "class_distribution": dataset_metadata["class_distribution"],
               "train_metrics": metadata["metrics"]["train"],
               "validation_metrics": metadata["metrics"]["validation"],
               "test_metrics": test_metrics, "test_trading": test_trading,
               "baselines": baselines, "comparison": comparison,
               "walk_forward": walk, "walk_forward_aggregate": walk_aggregate,
               "calibration": metadata["calibration_selection"],
               "feature_importance_top20": metadata["feature_importance"][:20],
               "historical_news_available": False,
               "limitations": [dataset_metadata["holdout_limitation"],
                               "No historical news overlaps the dataset; no WITH_NEWS model or performance claim was produced.",
                               "Trading simulation is a fixed-horizon, one-position, 10%-allocation approximation; it omits order-book depth and partial fills."]}
    reports.mkdir(parents=True, exist_ok=True)
    cleaned = _clean(summary)
    (reports / "ml_v1_market_summary.json").write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    rows = [_flatten_row("ML_V1_MARKET", test_metrics, test_trading)] + [
        _flatten_row(name, values["classification"], values["trading"]) for name, values in baselines.items()]
    _write_csv(reports / "ml_v1_market_summary.csv", rows)
    walk_rows = [_flatten_row(f"fold_{row['fold']}", row["classification"], row["trading"]) |
                 {"threshold": row["threshold"], "train_rows": row["train_rows"], "validation_rows": row["validation_rows"]}
                 for row in walk]
    _write_csv(reports / "walk_forward.csv", walk_rows)
    _write_csv(reports / "feature_importance.csv", [_clean(row) for row in metadata["feature_importance"]])
    lines = ["# ML_V1_MARKET evaluation", "", f"- Model: {metadata['model_family']}",
             f"- Threshold selected on validation: {threshold:.2f}",
             f"- Test ROC AUC: {test_metrics['roc_auc']:.4f}", f"- Test PR AUC: {test_metrics['pr_auc']:.4f}",
             f"- Test net return: {test_trading['net_return']:.4%}", f"- Test trades: {test_trading['trade_count']}",
             f"- Test max drawdown: {test_trading['max_drawdown']:.4%}", "", "## Baseline comparison", ""]
    for name, values in baselines.items():
        lines.append(f"- {name}: net return {values['trading']['net_return']:.4%}, trades {values['trading']['trade_count']}")
    lines += ["", "## Conclusions", "", f"- Beat heuristic by net return: {comparison['ml_beats_heuristic_net_return']}",
              f"- Beat trend by net return: {comparison['ml_beats_trend_net_return']}",
              f"- Beat comparable-exposure Buy & Hold by net return: {comparison['ml_beats_buy_hold_net_return']}",
              "- Feature importance is predictive importance, not causality.",
              "- No WITH_NEWS model was trained because historical news coverage is unavailable."]
    (reports / "ml_v1_market_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    metadata["metrics"]["test"] = test_metrics
    metadata["test_trading"] = test_trading
    metadata["walk_forward"] = walk
    metadata["walk_forward_aggregate"] = walk_aggregate
    metadata["baselines"] = baselines
    metadata["comparison"] = comparison
    metadata["test_evaluated_during_training"] = False
    metadata["test_evaluated_by"] = "app.ml.evaluate"
    metadata_path.write_text(json.dumps(_clean(metadata), indent=2), encoding="utf-8")
    return cleaned


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate registered ML_V1_MARKET and export reports")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--dataset-metadata", default=str(DEFAULT_DATASET_METADATA))
    parser.add_argument("--reports", default=str(DEFAULT_REPORTS))
    args = parser.parse_args(argv)
    summary = evaluate_and_export(args.registry, args.dataset, args.dataset_metadata, args.reports)
    print(json.dumps({"model_version": summary["model_version"], "threshold": summary["decision_threshold"],
                      "test_metrics": summary["test_metrics"], "test_trading": summary["test_trading"],
                      "comparison": summary["comparison"], "reports": str(Path(args.reports).resolve())}, indent=2))


if __name__ == "__main__":
    main()
