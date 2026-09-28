"""Train and register ML_V1_MARKET using train/validation only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import brier_score_loss

from app.ml.dataset import LeakageSafePreprocessor, PROJECT_ROOT
from app.ml.metrics import classification_metrics, select_threshold
from app.ml.model import (HistGradientBoostingDecisionModel, SigmoidCalibrator,
                          balanced_sample_weights)

DEFAULT_DATASET = PROJECT_ROOT / "data" / "ml" / "datasets" / "ml_v1_1h_without_news.csv.gz"
DEFAULT_DATASET_METADATA = PROJECT_ROOT / "data" / "ml" / "datasets" / "ml_v1_1h_without_news.metadata.json"
DEFAULT_REGISTRY = PROJECT_ROOT / "data" / "models" / "ml_v1_market"
MODEL_VERSION = "ml_v1_market"
DEFAULT_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75)
PARAMETER_CANDIDATES = (
    {"learning_rate": .05, "max_iter": 140, "max_leaf_nodes": 15,
     "min_samples_leaf": 50, "l2_regularization": 1.0, "early_stopping": False},
    {"learning_rate": .05, "max_iter": 180, "max_leaf_nodes": 31,
     "min_samples_leaf": 70, "l2_regularization": 2.0, "early_stopping": False},
)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_dataset(path=DEFAULT_DATASET, metadata_path=DEFAULT_DATASET_METADATA):
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if metadata.get("news", {}).get("included") and not metadata["news"].get("historical_observations_present"):
        raise ValueError("Refusing ML_WITH_NEWS: historical news coverage is unavailable")
    frame = pd.read_csv(path, parse_dates=["timestamp", "target_timestamp", "source_open_time"])
    return frame, metadata


def parse_thresholds(value):
    result = tuple(float(item) for item in value.split(","))
    if not result or any(not 0 < item < 1 for item in result):
        raise ValueError("Thresholds must be comma-separated values between 0 and 1")
    return result


def _model(features, preprocessor, parameters, seed, weighted):
    return HistGradientBoostingDecisionModel(features, preprocessor.medians_, parameters, seed, MODEL_VERSION), weighted


def _fit_preprocessor(rows, candidate_features):
    fitting = rows.copy()
    fitting["split"] = "train"
    return LeakageSafePreprocessor().fit(fitting, candidate_features)


def _feature_importance(model, validation, seed):
    sample = validation.sample(min(3000, len(validation)), random_state=seed).sort_index()
    matrix = model._matrix(sample)
    result = permutation_importance(model.estimator, matrix, sample["label"].astype(int),
                                    scoring="average_precision", n_repeats=2, random_state=seed)
    rows = [{"feature": name, "importance_mean": float(mean), "importance_std": float(std)}
            for name, mean, std in zip(model.feature_names(), result.importances_mean, result.importances_std)]
    return sorted(rows, key=lambda row: row["importance_mean"], reverse=True)


def train_candidate(frame, dataset_metadata, seed=42, threshold_grid=DEFAULT_THRESHOLDS,
                    parameter_candidates=PARAMETER_CANDIDATES):
    train = frame[frame["split"] == "train"].copy()
    validation = frame[frame["split"] == "validation"].copy()
    if frame[frame["split"] == "test"].empty:
        raise ValueError("Expected an untouched test partition")
    candidate_features = list(dataset_metadata["features"])
    preprocessor = _fit_preprocessor(train, candidate_features)
    candidates = []
    best = None
    for parameters in parameter_candidates:
        for weighted in (False, True):
            model, _ = _model(preprocessor.feature_names_, preprocessor, parameters, seed, weighted)
            weights = balanced_sample_weights(train["label"]) if weighted else None
            model.fit(train, train["label"], weights)
            probabilities = model.raw_predict_proba(validation)
            selected, thresholds = select_threshold(validation, probabilities, validation["label"], threshold_grid)
            classification = classification_metrics(validation["label"], probabilities, selected["threshold"])
            objective = selected["score"] + .05 * classification["pr_auc"]
            record = {"parameters": parameters, "class_weight": "balanced" if weighted else None,
                      "objective": objective, "threshold": selected["threshold"],
                      "validation_metrics": classification, "validation_trading": selected["trading"],
                      "threshold_candidates": thresholds}
            candidates.append(record)
            if best is None or objective > best[0]:
                best = (objective, model, record)
    _, model, selected_record = best
    raw_validation = model.raw_predict_proba(validation)
    unique_times = sorted(pd.to_datetime(validation["timestamp"], utc=True).unique())
    calibration_boundary = unique_times[len(unique_times) // 2]
    calibration_fit = pd.to_datetime(validation["timestamp"], utc=True) < calibration_boundary
    calibration_check = ~calibration_fit
    calibration = {"enabled": False, "method": None, "selection_period": "validation_second_half"}
    threshold_rows = validation
    threshold_labels = validation["label"]
    final_validation_probability = raw_validation
    if calibration_fit.sum() and calibration_check.sum() and validation.loc[calibration_fit, "label"].nunique() == 2:
        calibrator = SigmoidCalibrator.fit(raw_validation[calibration_fit], validation.loc[calibration_fit, "label"], seed)
        check_raw = raw_validation[calibration_check]
        check_calibrated = calibrator.transform(check_raw)
        raw_brier = brier_score_loss(validation.loc[calibration_check, "label"], check_raw)
        calibrated_brier = brier_score_loss(validation.loc[calibration_check, "label"], check_calibrated)
        calibration.update({"raw_brier": float(raw_brier), "calibrated_brier": float(calibrated_brier),
                            "fit_end": str(calibration_boundary), "improvement_required": .001})
        if calibrated_brier + .001 < raw_brier:
            model.calibrator = calibrator
            calibration.update({"enabled": True, "method": "sigmoid"})
            final_validation_probability = model.predict_proba(validation)
            threshold_rows = validation.loc[calibration_check]
            threshold_labels = validation.loc[calibration_check, "label"]
            threshold_probability = final_validation_probability[calibration_check]
        else:
            threshold_probability = final_validation_probability
    else:
        threshold_probability = final_validation_probability
    selected_threshold, threshold_candidates = select_threshold(
        threshold_rows, threshold_probability, threshold_labels, threshold_grid,
        minimum_trades=min(100, max(10, len(threshold_rows) // 100)))
    threshold = selected_threshold["threshold"]
    train_probability = model.predict_proba(train)
    validation_probability = model.predict_proba(validation)
    importance = _feature_importance(model, validation, seed)
    return {"model": model, "preprocessor": preprocessor, "threshold": threshold,
            "metrics": {"train": classification_metrics(train["label"], train_probability, threshold),
                        "validation": classification_metrics(validation["label"], validation_probability, threshold)},
            "validation_trading": select_threshold(validation, validation_probability, validation["label"], (threshold,), 0)[0]["trading"],
            "calibration": calibration, "selection_candidates": candidates,
            "threshold_candidates": threshold_candidates, "selected_configuration": selected_record,
            "feature_importance": importance}


def train_and_save(dataset=DEFAULT_DATASET, dataset_metadata_path=DEFAULT_DATASET_METADATA,
                   registry=DEFAULT_REGISTRY, seed=42, threshold_grid=DEFAULT_THRESHOLDS, force=False,
                   parameter_candidates=PARAMETER_CANDIDATES):
    registry = Path(registry)
    model_path = registry / "model.joblib"
    if model_path.exists() and not force:
        raise FileExistsError(f"Model already exists: {model_path}; pass --force to replace it")
    frame, dataset_metadata = load_dataset(dataset, dataset_metadata_path)
    result = train_candidate(frame, dataset_metadata, seed, threshold_grid, parameter_candidates)
    registry.mkdir(parents=True, exist_ok=True)
    result["model"].save(model_path)
    metadata = {**result["model"].metadata(), "trained_at": datetime.now(timezone.utc).isoformat(),
                "decision_threshold": result["threshold"], "target_definition": dataset_metadata["target"],
                "prediction_horizon": "4 hours", "feature_version": dataset_metadata["feature_version"],
                "feature_names": result["model"].feature_names(), "preprocessing": result["preprocessor"].metadata(),
                "train_period": dataset_metadata["periods"]["train"],
                "validation_period": dataset_metadata["periods"]["validation"],
                "test_period": dataset_metadata["periods"]["test"],
                "commission": dataset_metadata["config"]["commission"],
                "slippage": dataset_metadata["config"]["slippage"],
                "class_distribution": dataset_metadata["class_distribution"],
                "metrics": result["metrics"], "validation_trading": result["validation_trading"],
                "calibration_selection": result["calibration"],
                "selection_candidates": result["selection_candidates"],
                "threshold_candidates": result["threshold_candidates"],
                "selected_configuration": result["selected_configuration"],
                "feature_importance": result["feature_importance"],
                "historical_news_available": False,
                "dataset": {"path": str(Path(dataset).resolve()), "sha256": file_hash(dataset),
                            "metadata_path": str(Path(dataset_metadata_path).resolve()),
                            "rows": dataset_metadata["rows"]},
                "test_evaluated_during_training": False,
                "holdout_limitation": dataset_metadata["holdout_limitation"]}
    metadata["model_sha256"] = file_hash(model_path)
    (registry / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (registry / "feature_names.json").write_text(json.dumps(result["model"].feature_names(), indent=2), encoding="utf-8")
    (registry / "training_config.json").write_text(json.dumps({"seed": seed, "threshold_grid": threshold_grid,
                                                                 "parameter_candidates": parameter_candidates}, indent=2), encoding="utf-8")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train ML_V1_MARKET; test remains untouched")
    parser.add_argument("--model", choices=("hist_gradient_boosting",), default="hist_gradient_boosting")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threshold-grid", default=",".join(map(str, DEFAULT_THRESHOLDS)))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--dataset-metadata", default=str(DEFAULT_DATASET_METADATA))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    args = parser.parse_args(argv)
    metadata = train_and_save(args.dataset, args.dataset_metadata, args.registry, args.seed,
                              parse_thresholds(args.threshold_grid), args.force)
    print(json.dumps({"model_version": metadata["model_version"], "model_family": metadata["model_family"],
                      "threshold": metadata["decision_threshold"], "features": len(metadata["feature_names"]),
                      "calibration": metadata["calibration_selection"],
                      "registry": str(Path(args.registry).resolve()),
                      "test_evaluated": metadata["test_evaluated_during_training"]}, indent=2))


if __name__ == "__main__":
    main()
