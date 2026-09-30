"""Train the research-only cross-asset Opportunity Ranker v1."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd

from app.ml.cross_asset_dataset import PROJECT_ROOT
from app.ml.opportunity import (DEFAULT_PARAMETERS, OpportunityRanker, assert_feature_separation,
                                clean, feature_sets, file_hash, ranked_predictions,
                                ranking_metrics, regression_metrics, select_threshold,
                                symbol_features)

DEFAULT_DATASET = PROJECT_ROOT / "data" / "ml" / "cross_asset" / "cross_asset_v1.csv.gz"
DEFAULT_METADATA = PROJECT_ROOT / "data" / "ml" / "cross_asset" / "metadata.json"
DEFAULT_REGISTRY = PROJECT_ROOT / "data" / "models" / "opportunity_ranker_v1"


def load_data(dataset=DEFAULT_DATASET, metadata_path=DEFAULT_METADATA):
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if metadata["dataset_version"] != "cross-asset-v1":
        raise ValueError("Opportunity Ranker v1 requires cross-asset-v1")
    frame = pd.read_csv(dataset, parse_dates=["timestamp", "target_timestamp", "source_open_time",
                                              "source_timestamp_15m", "source_timestamp_1h", "source_timestamp_4h"])
    return frame, metadata


def train_candidate(frame, dataset_metadata, parameters=None):
    """Select symbol identity and threshold using Train/Validation only."""
    base_features = feature_sets(dataset_metadata)["full"]
    assert_feature_separation(base_features, dataset_metadata)
    train = frame[frame["split"] == "train"].copy()
    validation = frame[frame["split"] == "validation"].copy()
    candidates = []
    for include_symbol in (False, True):
        train_view = symbol_features(train) if include_symbol else train
        validation_view = symbol_features(validation) if include_symbol else validation
        features = base_features + ([f"symbol_{symbol}" for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")] if include_symbol else [])
        model = OpportunityRanker(features, parameters).fit(train_view)
        train_prediction = model.predict(train_view); validation_prediction = model.predict(validation_view)
        validation_ranked = ranked_predictions(validation_view, validation_prediction)
        ranking = ranking_metrics(validation_ranked)
        selected, thresholds = select_threshold(validation_ranked, minimum_trades=100)
        candidates.append({"include_symbol_identity": include_symbol, "features": features, "model": model,
                           "train_regression": regression_metrics(train_view["future_net_return_4h"], train_prediction),
                           "validation_regression": regression_metrics(validation_view["future_net_return_4h"], validation_prediction),
                           "validation_ranking": ranking, "threshold": selected["threshold"],
                           "threshold_selection": clean(selected), "threshold_candidates": clean(thresholds)})
    selected = max(candidates, key=lambda item: (item["validation_ranking"]["top_1_accuracy"],
                                                  item["validation_ranking"]["average_spearman"],
                                                  item["threshold_selection"]["score"],
                                                  not item["include_symbol_identity"]))
    return selected, candidates


def train_and_save(dataset=DEFAULT_DATASET, dataset_metadata_path=DEFAULT_METADATA,
                   registry=DEFAULT_REGISTRY, force=False, parameters=None):
    registry = Path(registry)
    if registry.exists() and any(registry.iterdir()) and not force:
        raise FileExistsError(f"Registry already exists: {registry}")
    registry.mkdir(parents=True, exist_ok=True)
    frame, dataset_metadata = load_data(dataset, dataset_metadata_path)
    selected, candidates = train_candidate(frame, dataset_metadata, parameters)
    model_path = selected["model"].save(registry / "model.joblib")
    (registry / "feature_names.json").write_text(json.dumps(selected["features"], indent=2), encoding="utf-8")
    (registry / "parameters.json").write_text(json.dumps(selected["model"].parameters, indent=2), encoding="utf-8")
    (registry / "threshold.json").write_text(json.dumps({"threshold": selected["threshold"],
                                                           "selected_on": "validation"}, indent=2), encoding="utf-8")
    comparison = [{key: value for key, value in item.items() if key not in {"model", "features", "threshold_candidates"}}
                  for item in candidates]
    metadata = {"model_version": "opportunity_ranker_v1", "model_family": OpportunityRanker.family,
                "created_at": datetime.now(timezone.utc).isoformat(), "research_only": True,
                "not_for_batch3_control": True, "universe_size_limitations": True,
                "dataset_version": dataset_metadata["dataset_version"], "dataset_sha256": file_hash(dataset),
                "target": "future_net_return_4h", "features": selected["features"],
                "feature_count": len(selected["features"]), "symbol_identity": selected["include_symbol_identity"],
                "parameters": selected["model"].parameters, "decision_threshold": selected["threshold"],
                "threshold_selected_on": "M13.1 validation only", "minimum_validation_trades": 100,
                "periods": dataset_metadata["periods"], "train_regression": selected["train_regression"],
                "validation_regression": selected["validation_regression"],
                "validation_ranking": selected["validation_ranking"], "symbol_identity_comparison": comparison,
                "test_evaluated_during_training": False, "model_sha256": file_hash(model_path)}
    (registry / "metadata.json").write_text(json.dumps(clean(metadata), indent=2), encoding="utf-8")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train research-only Opportunity Ranker v1")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET)); parser.add_argument("--dataset-metadata", default=str(DEFAULT_METADATA))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY)); parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    result = train_and_save(args.dataset, args.dataset_metadata, args.registry, args.force, DEFAULT_PARAMETERS)
    print(json.dumps(clean({"model_version": result["model_version"], "model_family": result["model_family"],
                            "features": result["feature_count"], "symbol_identity": result["symbol_identity"],
                            "threshold": result["decision_threshold"], "validation_ranking": result["validation_ranking"],
                            "registry": str(Path(args.registry).resolve()), "model_sha256": result["model_sha256"]}), indent=2))


if __name__ == "__main__": main()
