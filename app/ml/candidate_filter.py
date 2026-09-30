"""Train and evaluate the research-only high-recall candidate filter v1."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss

from app.ml.funnel_dataset import (
    DEFAULT_OUTPUT as FUNNEL_DIR,
    MEANINGFUL_THRESHOLD,
    baseline_funnel,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = FUNNEL_DIR / "candidate_dataset.csv.gz"
DEFAULT_REGIME = FUNNEL_DIR / "regime_dataset.csv.gz"
DEFAULT_METADATA = FUNNEL_DIR / "metadata.json"
DEFAULT_REGISTRY = PROJECT_ROOT / "data" / "models" / "candidate_filter_v1"
DEFAULT_REPORTS = PROJECT_ROOT / "data" / "ml" / "reports" / "candidate_filter_v1"
RANKER_V2_SCORES = PROJECT_ROOT / "data" / "ml" / "reports" / "opportunity_ranker_v2" / "test_rankings.csv"

LABELS = ("candidate_top_2", "ideal_candidate", "candidate_positive")
PARAMETER_GRID = (
    {"learning_rate": .05, "max_iter": 60, "max_leaf_nodes": 15, "min_samples_leaf": 120,
     "l2_regularization": 1., "early_stopping": False, "random_state": 42},
    {"learning_rate": .08, "max_iter": 80, "max_leaf_nodes": 31, "min_samples_leaf": 180,
     "l2_regularization": 2., "early_stopping": False, "random_state": 42},
)
THRESHOLD_GRID = np.round(np.linspace(.025, .975, 39), 3)
STATUS_CRITERIA = {
    "USEFUL_FUNNEL": "winner retention >= best simple baseline + 15pp, strong >0.5% retention >=80%, reduction >=35%, minimum symbol recall >=50%, and >=2/3 walk-forward folds winner retention >=70%",
    "WEAK": "winner retention above best simple baseline, average set <5, and strong >0.5% retention >=65%",
    "FAILED": "otherwise",
}


def clean(value):
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, list): return [clean(v) for v in value]
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating,)): return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)): return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)): return value.isoformat()
    return value


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_data(dataset=DEFAULT_DATASET, regime_path=DEFAULT_REGIME, metadata_path=DEFAULT_METADATA):
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if metadata["dataset_version"] != "funnel-dataset-v1":
        raise ValueError("candidate_filter_v1 requires funnel-dataset-v1")
    frame = pd.read_csv(dataset, parse_dates=["timestamp", "target_timestamp"])
    regime = pd.read_csv(regime_path, parse_dates=["timestamp"])
    context = regime[["timestamp", "regime_category", "best_net_return", "best_minus_second",
                      "best_minus_median", "future_cross_sectional_dispersion"]]
    frame = frame.merge(context, on="timestamp", how="left", validate="many_to_one")
    return frame, metadata


def feature_sets(metadata):
    full = metadata["schema"]["candidate_feature_columns"]
    additions = set(metadata["schema"]["additional_candidate_features"])
    base = [name for name in full if name not in additions]
    return {"base_81": base, "full_87": full}


def assert_feature_safety(features, metadata):
    targets = set(metadata["schema"]["candidate_target_columns"])
    columns = set(features)
    forbidden = [c for c in columns if c in targets or c.startswith(("future_", "candidate_", "ideal_"))]
    if forbidden:
        raise ValueError(f"Target leakage in feature schema: {forbidden}")
    if any(c in columns for c in ("timestamp", "target_timestamp", "symbol", "split", "regime_category")):
        raise ValueError("Metadata in feature schema")


class CandidateFilter:
    family = "HistGradientBoostingClassifier"

    def __init__(self, features, label, parameters=None):
        self.features = list(features)
        self.label = label
        self.parameters = dict(parameters or PARAMETER_GRID[0])
        self.model = HistGradientBoostingClassifier(**self.parameters)

    def fit(self, frame):
        self.model.fit(frame[self.features], frame[self.label].astype(int))
        return self

    def predict_proba(self, frame):
        return self.model.predict_proba(frame[self.features])[:, 1]

    def save(self, path):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"features": self.features, "label": self.label,
                     "parameters": self.parameters, "model": self.model}, path)
        return path

    @classmethod
    def load(cls, path):
        payload = joblib.load(path)
        result = cls(payload["features"], payload["label"], payload["parameters"])
        result.model = payload["model"]
        return result


def scored(frame, probabilities, threshold):
    result = frame.copy()
    result["candidate_probability"] = np.asarray(probabilities, dtype=float)
    result["candidate_keep"] = (result["candidate_probability"] >= threshold).astype(int)
    return result


def funnel_metrics(frame, probabilities, threshold, label):
    work = scored(frame, probabilities, threshold)
    groups = work.groupby("timestamp", sort=False)
    sizes = groups["candidate_keep"].sum()
    universe = groups["symbol"].size()
    best = groups["future_net_return_4h"].max()
    opportunity = best > MEANINGFUL_THRESHOLD
    strong_05 = best > .005
    strong_10 = best > .010
    winner = work[work["future_return_rank"] == 1].set_index("timestamp")
    retained = winner["candidate_keep"].astype(bool)
    selected = work[work["candidate_keep"] == 1]
    positives = work[label].astype(bool)
    top2_profitable = work["candidate_top_2"].astype(bool)
    return {
        "winner_retained_rate": float(retained.reindex(opportunity[opportunity].index).mean()),
        "strong_winner_retained_rate_0_5pct": float(retained.reindex(strong_05[strong_05].index).mean()),
        "strong_winner_retained_rate_1pct": float(retained.reindex(strong_10[strong_10].index).mean()),
        "top2_profitable_recall": float(work.loc[top2_profitable, "candidate_keep"].mean()),
        "candidate_precision": float(selected[label].mean()) if len(selected) else 0.,
        "candidate_recall": float(work.loc[positives, "candidate_keep"].mean()) if positives.any() else 0.,
        "average_candidate_set_size": float(sizes.mean()),
        "median_candidate_set_size": float(sizes.median()),
        "p90_candidate_set_size": float(sizes.quantile(.9)),
        "empty_candidate_set_rate": float((sizes == 0).mean()),
        "full_universe_set_rate": float((sizes == universe).mean()),
        "universe_reduction": float(1 - sizes.sum() / universe.sum()),
        "timestamps": int(len(sizes)), "selected_rows": int(work["candidate_keep"].sum()),
    }


def select_threshold(validation, probabilities, label, grid=THRESHOLD_GRID):
    rows = []
    for threshold in grid:
        metrics = funnel_metrics(validation, probabilities, float(threshold), label)
        rows.append({"threshold": float(threshold), **metrics})
    eligible = [row for row in rows if row["average_candidate_set_size"] <= 4]
    pool = eligible or rows
    winner = max(pool, key=lambda r: (r["winner_retained_rate"],
                                      r["strong_winner_retained_rate_0_5pct"],
                                      r["candidate_precision"],
                                      -r["average_candidate_set_size"], r["threshold"]))
    return winner, rows


def select_configuration(frame, metadata, parameter_grid=PARAMETER_GRID):
    train = frame[frame["split"] == "train"]
    validation = frame[frame["split"] == "validation"]
    candidates = []
    for label in LABELS:
        for feature_name, features in feature_sets(metadata).items():
            assert_feature_safety(features, metadata)
            for parameter_id, parameters in enumerate(parameter_grid, 1):
                model = CandidateFilter(features, label, parameters).fit(train)
                probability = model.predict_proba(validation)
                chosen, thresholds = select_threshold(validation, probability, label)
                candidates.append({"label": label, "feature_set": feature_name, "features": features,
                                   "parameter_id": parameter_id, "parameters": dict(parameters), "model": model,
                                   "threshold": chosen["threshold"], "validation": chosen,
                                   "validation_brier": float(brier_score_loss(validation[label], probability)),
                                   "threshold_candidates": thresholds})
    winner = max(candidates, key=lambda x: (x["validation"]["winner_retained_rate"],
                                            x["validation"]["strong_winner_retained_rate_0_5pct"],
                                            x["validation"]["candidate_precision"],
                                            -x["validation"]["average_candidate_set_size"],
                                            x["feature_set"] == "full_87"))
    return winner, candidates


def set_distribution(work):
    sizes = work.groupby("timestamp")["candidate_keep"].sum()
    counts = sizes.value_counts().reindex(range(8), fill_value=0)
    return pd.DataFrame({"candidate_set_size": range(8), "timestamps": counts.values,
                         "frequency": counts.values / len(sizes)})


def symbol_results(work, label):
    rows = []
    for symbol, part in work.groupby("symbol"):
        selected = part[part["candidate_keep"] == 1]
        winners = part[part["future_return_rank"] == 1]
        rows.append({"symbol": symbol, "positive_label_count": int(part[label].sum()),
                     "recall": float(part.loc[part[label] == 1, "candidate_keep"].mean()),
                     "precision": float(selected[label].mean()) if len(selected) else 0.,
                     "keep_rate": float(part["candidate_keep"].mean()),
                     "winner_retained_rate": float(winners["candidate_keep"].mean()) if len(winners) else np.nan,
                     "winner_timestamps": len(winners)})
    return pd.DataFrame(rows)


def regime_results(work):
    rows = []
    train_dispersion_cut = work.loc[work["split"] == "train", "future_cross_sectional_dispersion"].median()
    work = work.copy()
    work["dispersion_band"] = np.where(work["future_cross_sectional_dispersion"] <= train_dispersion_cut, "LOW", "HIGH")
    work["separation_band"] = np.where(work["best_minus_second"] <= .003, "LOW", "HIGH")
    for dimension in ("regime_category", "dispersion_band", "separation_band"):
        for name, part in work.groupby(dimension):
            groups = part.groupby("timestamp")
            sizes = groups["candidate_keep"].sum()
            winner = part[part["future_return_rank"] == 1]
            meaningful = winner["best_net_return"] > MEANINGFUL_THRESHOLD
            rows.append({"dimension": dimension, "group": name, "timestamps": part["timestamp"].nunique(),
                         "average_candidate_set_size": sizes.mean(),
                         "winner_retained_rate": winner.loc[meaningful, "candidate_keep"].mean(),
                         "candidate_precision": part.loc[part["candidate_keep"] == 1, "candidate_positive"].mean()})
    return pd.DataFrame(rows)


def simple_baselines(test, label):
    rows = []
    base = baseline_funnel(test)
    for _, row in base.iterrows():
        name = row["baseline"]
        if name == "top_2_momentum": mask = test.groupby("timestamp")["momentum_4_1h"].rank(method="first", ascending=False) <= 2
        elif name == "top_3_relative_strength": mask = test.groupby("timestamp")["return_rank_24h"].rank(method="first", ascending=False) <= 3
        elif name == "above_ema": mask = test["ema_distance_20_1h"] > 0
        elif name == "all_assets": mask = pd.Series(True, index=test.index)
        elif name == "oracle_winner_impossible": mask = (test["future_return_rank"] == 1) & (test["best_net_return"] > MEANINGFUL_THRESHOLD)
        else: continue
        metrics = funnel_metrics(test, mask.astype(float), .5, label)
        rows.append({"method": name, "impossible_future_oracle": name == "oracle_winner_impossible", **metrics})
    return pd.DataFrame(rows)


def downstream_diagnostic(work, ranker_scores=RANKER_V2_SCORES):
    def selection_accuracy(frame, score):
        selected = frame[frame["candidate_keep"] == 1].copy()
        if selected.empty: return 0., 0
        idx = selected.groupby("timestamp")[score].idxmax()
        chosen = selected.loc[idx]
        return float((chosen["future_return_rank"] == 1).mean()), int(chosen["timestamp"].nunique())
    filtered_momentum, n1 = selection_accuracy(work, "momentum_4_1h")
    all_idx = work.groupby("timestamp")["momentum_4_1h"].idxmax()
    all_momentum = float((work.loc[all_idx, "future_return_rank"] == 1).mean())
    rows = [{"rule": "highest_momentum", "candidate_filtered_top1": filtered_momentum,
             "all_universe_top1": all_momentum, "decided_timestamps": n1}]
    path = Path(ranker_scores)
    if path.exists():
        scores = pd.read_csv(path, usecols=["timestamp", "symbol", "predicted_net_return"])
        scores["timestamp"] = pd.to_datetime(scores["timestamp"], utc=True)
        merged = work.merge(scores, on=["timestamp", "symbol"], how="left", validate="one_to_one")
        filtered, n2 = selection_accuracy(merged, "predicted_net_return")
        all_idx = merged.groupby("timestamp")["predicted_net_return"].idxmax()
        rows.append({"rule": "frozen_opportunity_ranker_v2", "candidate_filtered_top1": filtered,
                     "all_universe_top1": float((merged.loc[all_idx, "future_return_rank"] == 1).mean()),
                     "decided_timestamps": n2})
    return pd.DataFrame(rows)


def winner_misses(work, features):
    winners = work[(work["future_return_rank"] == 1) & (work["best_net_return"] > MEANINGFUL_THRESHOLD)]
    misses = winners[winners["candidate_keep"] == 0].copy()
    candidate_sets = work[work["candidate_keep"] == 1].groupby("timestamp")["symbol"].apply(lambda x: ",".join(x))
    misses["candidate_set"] = misses["timestamp"].map(candidate_sets).fillna("")
    snapshot = [c for c in ["momentum_4_1h", "return_24h", "ema_distance_20_1h", "rsi_14_1h",
                             "trend_alignment_score", "volume_confirmation_score"] if c in features]
    misses["winner_feature_snapshot"] = misses[snapshot].apply(lambda r: json.dumps(clean(r.to_dict())), axis=1)
    return misses[["timestamp", "symbol", "future_net_return_4h", "candidate_probability", "decision_threshold",
                   "winner_feature_snapshot", "candidate_set", "regime_category", "best_minus_second", "best_minus_median"]].rename(
                       columns={"symbol": "actual_winner", "future_net_return_4h": "winner_future_return",
                                "candidate_probability": "winner_candidate_probability", "decision_threshold": "threshold"})


def false_positives(work, label):
    false = work[(work["candidate_keep"] == 1) & (work["future_net_return_4h"] <= 0)].copy()
    return false[["timestamp", "symbol", "future_net_return_4h", "candidate_probability", "regime_category", label]]


def manual_validation(work):
    sizes = work.groupby("timestamp")["candidate_keep"].sum()
    winner = work[work["future_return_rank"] == 1].set_index("timestamp")
    selectors = {
        "NO_OPPORTUNITY": winner["regime_category"].eq("NO_OPPORTUNITY"),
        "STRONG": winner["regime_category"].eq("STRONG"),
        "winner_retained": winner["candidate_keep"].eq(1) & winner["best_net_return"].gt(MEANINGFUL_THRESHOLD),
        "winner_dropped": winner["candidate_keep"].eq(0) & winner["best_net_return"].gt(MEANINGFUL_THRESHOLD),
        "zero_candidates": winner.index.to_series().map(sizes).eq(0),
        "three_or_four_candidates": winner.index.to_series().map(sizes).isin([3, 4]),
    }
    checks = []
    for case, mask in selectors.items():
        eligible = winner.loc[mask]
        if eligible.empty:
            checks.append({"case": case, "available": False})
            continue
        stamp = eligible.index[len(eligible) // 2]
        rows = work[work["timestamp"] == stamp]
        checks.append({"case": case, "available": True, "timestamp": stamp,
                       "threshold": float(rows["decision_threshold"].iloc[0]),
                       "actual_winner": rows.loc[rows["future_return_rank"] == 1, "symbol"].iloc[0],
                       "candidate_set": rows.loc[rows["candidate_keep"] == 1, "symbol"].tolist(),
                       "probabilities": rows.set_index("symbol")["candidate_probability"].to_dict(),
                       "threshold_logic_verified": bool(((rows["candidate_probability"] >= rows["decision_threshold"]).astype(int) == rows["candidate_keep"]).all())})
    return {"passed": all(item.get("threshold_logic_verified", True) for item in checks), "checks": checks}


def walk_forward(frame, features, label, parameters):
    times = np.array(sorted(pd.to_datetime(frame["timestamp"], utc=True).unique()))
    stamp = pd.to_datetime(frame["timestamp"], utc=True); target = pd.to_datetime(frame["target_timestamp"], utc=True)
    windows = [(.45, .55, .70), (.55, .70, .85), (.70, .85, 1.0)]
    rows = []
    for fold, (train_end, validation_end, evaluation_end) in enumerate(windows, 1):
        a, b = times[int(len(times) * train_end)], times[int(len(times) * validation_end)]
        c = times[min(len(times) - 1, int(len(times) * evaluation_end) - 1)]
        train = frame[(stamp < a) & (target < a)]
        validation = frame[(stamp >= a) & (stamp < b) & (target < b)]
        evaluation = frame[(stamp >= b) & (stamp <= c)]
        model = CandidateFilter(features, label, parameters).fit(train)
        threshold, _ = select_threshold(validation, model.predict_proba(validation), label)
        metrics = funnel_metrics(evaluation, model.predict_proba(evaluation), threshold["threshold"], label)
        rows.append({"fold": fold, "train_rows": len(train), "validation_rows": len(validation),
                     "evaluation_rows": len(evaluation), "threshold": threshold["threshold"],
                     "evaluation_start": evaluation["timestamp"].min(), "evaluation_end": evaluation["timestamp"].max(), **metrics})
    return pd.DataFrame(rows)


def train_and_evaluate(dataset=DEFAULT_DATASET, regime_path=DEFAULT_REGIME, metadata_path=DEFAULT_METADATA,
                       registry=DEFAULT_REGISTRY, reports=DEFAULT_REPORTS, force=False,
                       parameter_grid=PARAMETER_GRID):
    registry, reports = Path(registry), Path(reports)
    if registry.exists() and any(registry.iterdir()) and not force:
        raise FileExistsError(f"Research artifact already exists: {registry}")
    registry.mkdir(parents=True, exist_ok=True); reports.mkdir(parents=True, exist_ok=True)
    frame, dataset_meta = load_data(dataset, regime_path, metadata_path)
    winner, candidates = select_configuration(frame, dataset_meta, parameter_grid)
    model_path = winner["model"].save(registry / "model.joblib")
    (registry / "features.json").write_text(json.dumps(winner["features"], indent=2), encoding="utf-8")

    label_rows, threshold_rows = [], []
    for item in candidates:
        label_rows.append({"label": item["label"], "feature_set": item["feature_set"],
                           "parameter_id": item["parameter_id"], "validation_brier": item["validation_brier"],
                           **item["validation"]})
        if item is winner:
            threshold_rows = item["threshold_candidates"]
    pd.DataFrame(label_rows).to_csv(reports / "label_comparison.csv", index=False)
    pd.DataFrame(threshold_rows).to_csv(reports / "threshold_analysis.csv", index=False)

    test = frame[frame["split"] == "test"].copy()
    test_probability = winner["model"].predict_proba(test)
    test_work = scored(test, test_probability, winner["threshold"])
    test_work["decision_threshold"] = winner["threshold"]
    test_metrics = funnel_metrics(test, test_probability, winner["threshold"], winner["label"])
    test_metrics["brier_score"] = float(brier_score_loss(test[winner["label"]], test_probability))
    distribution = set_distribution(test_work)
    symbols = symbol_results(test_work, winner["label"])
    # Recompute Test-only regime report while retaining Train-derived dispersion cut semantics.
    train_cut = frame.loc[frame["split"] == "train", "future_cross_sectional_dispersion"].median()
    test_regime = test_work.copy(); test_regime["dispersion_band"] = np.where(test_regime["future_cross_sectional_dispersion"] <= train_cut, "LOW", "HIGH")
    test_regime["separation_band"] = np.where(test_regime["best_minus_second"] <= .003, "LOW", "HIGH")
    regime_rows = []
    for dimension in ("regime_category", "dispersion_band", "separation_band"):
        for name, part in test_regime.groupby(dimension):
            winner_rows = part[part["future_return_rank"] == 1]; meaningful = winner_rows["best_net_return"] > MEANINGFUL_THRESHOLD
            regime_rows.append({"dimension": dimension, "group": name, "timestamps": part["timestamp"].nunique(),
                                "average_candidate_set_size": part.groupby("timestamp")["candidate_keep"].sum().mean(),
                                "winner_retained_rate": winner_rows.loc[meaningful, "candidate_keep"].mean(),
                                "candidate_precision": part.loc[part["candidate_keep"] == 1, winner["label"]].mean()})
    regimes = pd.DataFrame(regime_rows)
    baselines = simple_baselines(test, winner["label"])
    ml_baseline = {"method": "candidate_filter_v1", "impossible_future_oracle": False, **test_metrics}
    baselines = pd.concat([pd.DataFrame([ml_baseline]), baselines], ignore_index=True)
    downstream = downstream_diagnostic(test_work)
    misses = winner_misses(test_work, winner["features"])
    false = false_positives(test_work, winner["label"])
    manual = manual_validation(test_work)
    walk = walk_forward(frame, winner["features"], winner["label"], winner["parameters"])

    best_simple = baselines.loc[baselines["method"].isin(["top_2_momentum", "top_3_relative_strength", "above_ema"]), "winner_retained_rate"].max()
    min_symbol_recall = symbols["recall"].min()
    useful = (test_metrics["winner_retained_rate"] >= best_simple + .15 and
              test_metrics["strong_winner_retained_rate_0_5pct"] >= .80 and
              test_metrics["universe_reduction"] >= .35 and min_symbol_recall >= .50 and
              (walk["winner_retained_rate"] >= .70).sum() >= 2)
    weak = (test_metrics["winner_retained_rate"] > best_simple and
            test_metrics["average_candidate_set_size"] < 5 and
            test_metrics["strong_winner_retained_rate_0_5pct"] >= .65)
    status = "USEFUL_FUNNEL" if useful else ("WEAK" if weak else "FAILED")

    distribution.to_csv(reports / "candidate_set_distribution.csv", index=False)
    symbols.to_csv(reports / "symbol_results.csv", index=False)
    regimes.to_csv(reports / "regime_results.csv", index=False)
    walk.to_csv(reports / "walk_forward.csv", index=False)
    misses.to_csv(reports / "winner_misses.csv", index=False)
    false.to_csv(reports / "false_positives.csv", index=False)
    baselines.to_csv(reports / "baseline_comparison.csv", index=False)
    downstream.to_csv(reports / "downstream_diagnostic.csv", index=False)
    (reports / "manual_validation.json").write_text(json.dumps(clean(manual), indent=2), encoding="utf-8")
    model_sha = file_hash(model_path)
    summary = {
        "model_version": "candidate_filter_v1", "research_only": True,
        "not_for_batch3_control": True, "pipeline_stage": "candidate_filter", "status": status,
        "status_criteria": STATUS_CRITERIA, "dataset_version": "funnel-dataset-v1",
        "dataset_sha256": file_hash(dataset), "labels_tested": list(LABELS),
        "winning_label": winner["label"], "feature_sets_tested": list(feature_sets(dataset_meta)),
        "chosen_feature_set": winner["feature_set"], "features": winner["features"],
        "parameters": winner["parameters"], "candidate_threshold": winner["threshold"],
        "threshold_selection_rule": "Validation only: maximize winner retention with average set <=4; tie-break strong retention, precision, smaller set",
        "calibration": {"used": False, "reason": "secondary to recall; raw HGB probabilities retained"},
        "validation_metrics": winner["validation"], "validation_brier": winner["validation_brier"],
        "test_metrics": test_metrics, "set_size_distribution": distribution.to_dict("records"),
        "symbol_results": symbols.to_dict("records"), "regime_results": regimes.to_dict("records"),
        "walk_forward": walk.to_dict("records"), "baseline_comparison": baselines.to_dict("records"),
        "downstream_diagnostic": downstream.to_dict("records"),
        "winner_miss_count": len(misses), "false_positive_count": len(false),
        "false_positive_mean_return": float(false["future_net_return_4h"].mean()) if len(false) else None,
        "false_positive_probability_quantiles": clean(false["candidate_probability"].quantile([.1, .5, .9]).to_dict()) if len(false) else {},
        "false_positive_by_symbol": clean(false["symbol"].value_counts().to_dict()),
        "false_positive_by_regime": clean(false["regime_category"].value_counts().to_dict()),
        "manual_validation": manual,
        "periods": dataset_meta["periods"], "model_sha256": model_sha,
        "test_used_for_selection": False,
    }
    metadata = dict(summary)
    metadata.update({"created_at": datetime.now(timezone.utc).isoformat(), "model_family": CandidateFilter.family,
                     "model_file": "model.joblib", "report_path": str(reports.resolve())})
    (registry / "metadata.json").write_text(json.dumps(clean(metadata), indent=2), encoding="utf-8")
    (reports / "summary.json").write_text(json.dumps(clean(summary), indent=2), encoding="utf-8")
    (reports / "summary.md").write_text(
        f"# Candidate Filter v1\n\n- Research status: **{status}**\n- Label: `{winner['label']}`\n"
        f"- Features: `{winner['feature_set']}` ({len(winner['features'])})\n- Threshold: {winner['threshold']:.3f}\n"
        f"- Test winner retained: {test_metrics['winner_retained_rate']:.2%}\n"
        f"- Test average set: {test_metrics['average_candidate_set_size']:.3f}\n",
        encoding="utf-8")
    return clean(summary)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train/evaluate research-only candidate filter v1")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET)); parser.add_argument("--regime", default=str(DEFAULT_REGIME))
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA)); parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--reports", default=str(DEFAULT_REPORTS)); parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    result = train_and_evaluate(args.dataset, args.regime, args.metadata, args.registry, args.reports, args.force)
    print(json.dumps({k: result[k] for k in ("status", "winning_label", "chosen_feature_set", "candidate_threshold", "validation_metrics", "test_metrics", "model_sha256")}, indent=2))


if __name__ == "__main__": main()
