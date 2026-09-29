"""Research-only MARKET_ONLY versus WITH_NEWS_V2 comparison."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.dataset import PROJECT_ROOT
from app.ml.evaluate import _clean, _write_csv, walk_forward_evaluation
from app.ml.metrics import classification_metrics, simulate_trades
from app.ml.news_features import COVERAGE_FEATURES, NEWS_FEATURES_V2
from app.ml.train import DEFAULT_THRESHOLDS, file_hash, load_dataset, train_candidate

DATASETS = PROJECT_ROOT / "data" / "ml" / "datasets"
DEFAULT_MARKET = DATASETS / "ml_v1_1h_without_news.csv.gz"
DEFAULT_MARKET_META = DATASETS / "ml_v1_1h_without_news.metadata.json"
DEFAULT_NEWS = DATASETS / "ml_v1_1h_with_news_v2_full.csv.gz"
DEFAULT_NEWS_META = DATASETS / "ml_v1_1h_with_news_v2_full.metadata.json"
DEFAULT_FILTERED = DATASETS / "ml_v1_1h_with_news_v2_coverage_filtered.csv.gz"
DEFAULT_FILTERED_META = DATASETS / "ml_v1_1h_with_news_v2_coverage_filtered.metadata.json"
DEFAULT_FROZEN_META = PROJECT_ROOT / "data" / "models" / "ml_v1_market" / "metadata.json"
DEFAULT_REGISTRY = PROJECT_ROOT / "data" / "models" / "ml_v1_news_research"
DEFAULT_REPORTS = PROJECT_ROOT / "data" / "ml" / "reports" / "news_research"
KEYS = ["timestamp", "symbol", "target_timestamp"]
MODEL_VERSION = "ml_v1_news_research"


def research_artifact_flags():
    return {"research_only": True, "news_archive_readiness": "PARTIAL",
            "not_for_batch3_control": True}


def _key_index(frame):
    return pd.MultiIndex.from_frame(frame[KEYS].astype(str))


def validate_same_period(market, news):
    if len(market) != len(news) or not _key_index(market).equals(_key_index(news)):
        raise ValueError("MARKET_ONLY and WITH_NEWS_FULL do not have identical sample rows")
    for name in ("label", "future_net_return", "split"):
        left, right = market[name].reset_index(drop=True), news[name].reset_index(drop=True)
        if not left.equals(right):
            raise ValueError(f"Comparison mismatch in {name}")
    return True


def align_market_subset(market, subset):
    indexed = market.copy(); indexed.index = _key_index(indexed)
    keys = _key_index(subset)
    if not keys.isin(indexed.index).all():
        raise ValueError("Coverage-filtered rows are not a subset of MARKET_ONLY")
    return indexed.loc[keys].reset_index(drop=True)


def feature_sets(market_metadata, news_metadata):
    market = list(market_metadata["features"])
    full = list(news_metadata["features"])
    if full[:len(market)] != market:
        raise ValueError("News dataset does not preserve the market feature prefix")
    counts = [name for name in NEWS_FEATURES_V2
              if "count" in name or name in COVERAGE_FEATURES]
    return {"market_only": market, "market_plus_counts_coverage": market + counts,
            "market_plus_full_news": full}


def _metadata_with_features(metadata, features):
    return {**metadata, "features": list(features), "feature_count": len(features)}


def fit_variant(frame, metadata, features, parameters, seed=42, importance=False):
    result = train_candidate(frame, _metadata_with_features(metadata, features), seed,
                             DEFAULT_THRESHOLDS, (parameters,), weight_options=(True,),
                             compute_importance=importance)
    result["model"].model_version = MODEL_VERSION
    return result


def evaluate_variant(frame, result):
    output, probabilities = {}, {}
    for split in ("train", "validation", "test"):
        rows = frame[frame["split"] == split]
        probability = result["model"].predict_proba(rows)
        probabilities[split] = probability
        output[split] = {"classification": classification_metrics(
            rows["label"], probability, result["threshold"])}
        if split == "test":
            output[split]["trading"] = simulate_trades(rows, probability, result["threshold"])
    return output, probabilities


def paired_predictions(test, market_probability, news_probability, significance=.02):
    result = test[["timestamp", "target_timestamp", "symbol", "label", "future_net_return",
                   "eligible_news_count_recent", "news_coverage_available"]].copy()
    result["market_probability"] = market_probability
    result["news_probability"] = news_probability
    result["probability_delta"] = result["news_probability"] - result["market_probability"]
    absolute = result["probability_delta"].abs()
    result["absolute_delta_bucket"] = pd.cut(
        absolute, [-np.inf, .02, .05, .10, np.inf], right=False,
        labels=["<0.02", "0.02-0.05", "0.05-0.10", ">=0.10"]).astype(str)
    result["direction"] = np.where(result["probability_delta"] >= significance, "increased",
                                   np.where(result["probability_delta"] <= -significance,
                                            "decreased", "almost_nothing"))
    return result


def paired_summary(paired):
    rows = []
    for grouping in ("absolute_delta_bucket", "direction"):
        for name, group in paired.groupby(grouping, observed=True):
            rows.append({"grouping": grouping, "group": str(name), "rows": len(group),
                         "mean_delta": float(group["probability_delta"].mean()),
                         "mean_absolute_delta": float(group["probability_delta"].abs().mean()),
                         "positive_label_rate": float(group["label"].mean()),
                         "mean_future_net_return": float(group["future_net_return"].mean()),
                         "news_active_rows": int((group["eligible_news_count_recent"] > 0).sum())})
    return rows


def _safe_metrics(rows, probability, threshold, minimum=30):
    if len(rows) < minimum or rows["label"].nunique() < 2:
        return {"status": "NOT_ENOUGH_DATA", "rows": len(rows)}
    return {"status": "OK", "rows": len(rows),
            "classification": classification_metrics(rows["label"], probability, threshold),
            "trading": simulate_trades(rows, probability, threshold)}


def segmented_analysis(test, market_probability, news_probability, market_threshold, news_threshold):
    result = []
    masks = {"news_active": test["eligible_news_count_recent"] > 0,
             "news_inactive_valid_coverage": ((test["eligible_news_count_recent"] == 0) &
                                               test["row_news_eligible_for_training"].astype(bool))}
    for segment, mask in masks.items():
        positions = np.flatnonzero(mask.to_numpy())
        for variant, probability, threshold in (
                ("MARKET_ONLY", market_probability, market_threshold),
                ("WITH_NEWS_FULL", news_probability, news_threshold)):
            metrics = _safe_metrics(test.loc[mask], probability[positions], threshold)
            result.append({"segment": segment, "variant": variant, **metrics})
    return result


def symbol_analysis(test, market_probability, news_probability, market_threshold, news_threshold):
    result = []
    for symbol, group in test.groupby("symbol", observed=True):
        positions = test.index.get_indexer(group.index)
        active = int((group["eligible_news_count_recent"] > 0).sum())
        for variant, probability, threshold in (("MARKET_ONLY", market_probability, market_threshold),
                                                 ("WITH_NEWS_FULL", news_probability, news_threshold)):
            result.append({"symbol": symbol, "variant": variant, "news_active_rows": active,
                           "coverage_eligible_rows": int(group["row_news_eligible_for_training"].sum()),
                           **_safe_metrics(group, probability[positions], threshold)})
    return result


def event_analysis(test, market_probability, news_probability, market_threshold, news_threshold):
    groups = {"upgrade": ("event_upgrade_count_24h",),
              "hack_exploit": ("event_hack_count_24h", "event_exploit_count_24h"),
              "regulation": ("event_regulation_count_24h",), "ETF": ("event_ETF_count_24h",),
              "exchange": ("event_exchange_count_24h",), "macro": ("event_macro_count_24h",),
              "other": ("event_other_count_24h",)}
    result = []
    for event, columns in groups.items():
        mask = test[list(columns)].sum(axis=1) > 0
        positions = np.flatnonzero(mask.to_numpy())
        for variant, probability, threshold in (("MARKET_ONLY", market_probability, market_threshold),
                                                 ("WITH_NEWS_FULL", news_probability, news_threshold)):
            result.append({"event": event, "variant": variant,
                           **_safe_metrics(test.loc[mask], probability[positions], threshold, minimum=50)})
    return result


def compare_walk_forward(market_rows, news_rows):
    by_market = {row["fold"]: row for row in market_rows}
    result = []
    for news in news_rows:
        market = by_market[news["fold"]]
        result.append({"fold": news["fold"], "market_roc_auc": market["classification"]["roc_auc"],
                       "news_roc_auc": news["classification"]["roc_auc"],
                       "market_pr_auc": market["classification"]["pr_auc"],
                       "news_pr_auc": news["classification"]["pr_auc"],
                       "market_threshold": market["threshold"], "news_threshold": news["threshold"],
                       "market_net_return": market["trading"]["net_return"],
                       "news_net_return": news["trading"]["net_return"],
                       "market_trades": market["trading"]["trade_count"],
                       "news_trades": news["trading"]["trade_count"],
                       "market_profit_factor": market["trading"]["profit_factor"],
                       "news_profit_factor": news["trading"]["profit_factor"],
                       "market_drawdown": market["trading"]["max_drawdown"],
                       "news_drawdown": news["trading"]["max_drawdown"]})
    return result


def research_status(market_metrics, news_metrics, walk, active_rows):
    market_test, news_test = market_metrics["test"], news_metrics["test"]
    classification_better = (news_test["classification"]["roc_auc"] > market_test["classification"]["roc_auc"] and
                             news_test["classification"]["pr_auc"] > market_test["classification"]["pr_auc"])
    trading_better = news_test["trading"]["net_return"] > market_test["trading"]["net_return"]
    fold_wins = sum(row["news_roc_auc"] > row["market_roc_auc"] and
                    row["news_pr_auc"] > row["market_pr_auc"] for row in walk)
    criteria = {"test_classification_better": classification_better,
                "test_net_return_better": trading_better, "walk_forward_joint_wins": fold_wins,
                "minimum_active_test_rows_met": active_rows >= 500,
                "news_archive_readiness": "PARTIAL"}
    if classification_better and trading_better and fold_wins >= 2 and active_rows >= 500:
        status = "PROMISING_RESEARCH_SIGNAL"
    elif not classification_better and not trading_better and fold_wins <= 1:
        status = "NO_DEMONSTRATED_VALUE"
    else:
        status = "MIXED"
    return status, criteria


def _flatten_analysis(rows):
    flat = []
    for row in rows:
        base = {key: value for key, value in row.items() if key not in {"classification", "trading"}}
        for key, value in row.get("classification", {}).items():
            if key != "confusion_matrix": base[f"classification_{key}"] = value
        for key, value in row.get("trading", {}).items():
            if key != "exposure_note": base[f"trading_{key}"] = value
        flat.append(_clean(base))
    return flat


def run_research(market_path=DEFAULT_MARKET, market_meta_path=DEFAULT_MARKET_META,
                 news_path=DEFAULT_NEWS, news_meta_path=DEFAULT_NEWS_META,
                 filtered_path=DEFAULT_FILTERED, filtered_meta_path=DEFAULT_FILTERED_META,
                 frozen_meta_path=DEFAULT_FROZEN_META, registry=DEFAULT_REGISTRY,
                 reports=DEFAULT_REPORTS, seed=42):
    market, market_meta = load_dataset(market_path, market_meta_path)
    news, news_meta = load_dataset(news_path, news_meta_path)
    filtered, filtered_meta = load_dataset(filtered_path, filtered_meta_path)
    validate_same_period(market, news)
    market_filtered = align_market_subset(market, filtered)
    features = feature_sets(market_meta, news_meta)
    frozen = json.loads(Path(frozen_meta_path).read_text(encoding="utf-8"))
    parameters = dict(frozen["selected_configuration"]["parameters"])

    fitted = {
        "MARKET_ONLY": fit_variant(market, market_meta, features["market_only"], parameters, seed),
        "WITH_NEWS_FULL": fit_variant(news, news_meta, features["market_plus_full_news"], parameters, seed, True),
        "WITH_NEWS_COVERAGE_FILTERED": fit_variant(filtered, filtered_meta, features["market_plus_full_news"], parameters, seed),
        "MARKET_FILTERED_REFERENCE": fit_variant(market_filtered, filtered_meta, features["market_only"], parameters, seed),
        "MARKET_PLUS_COUNTS_COVERAGE": fit_variant(news, news_meta, features["market_plus_counts_coverage"], parameters, seed),
    }
    frames = {"MARKET_ONLY": market, "WITH_NEWS_FULL": news,
              "WITH_NEWS_COVERAGE_FILTERED": filtered, "MARKET_FILTERED_REFERENCE": market_filtered,
              "MARKET_PLUS_COUNTS_COVERAGE": news}
    metrics, probabilities = {}, {}
    for name, result in fitted.items():
        metrics[name], probabilities[name] = evaluate_variant(frames[name], result)

    market_test = market[market["split"] == "test"].reset_index(drop=True)
    news_test = news[news["split"] == "test"].reset_index(drop=True)
    paired = paired_predictions(news_test, probabilities["MARKET_ONLY"]["test"],
                                probabilities["WITH_NEWS_FULL"]["test"])
    segments = segmented_analysis(news_test, probabilities["MARKET_ONLY"]["test"],
                                  probabilities["WITH_NEWS_FULL"]["test"],
                                  fitted["MARKET_ONLY"]["threshold"], fitted["WITH_NEWS_FULL"]["threshold"])
    symbols = symbol_analysis(news_test, probabilities["MARKET_ONLY"]["test"],
                              probabilities["WITH_NEWS_FULL"]["test"],
                              fitted["MARKET_ONLY"]["threshold"], fitted["WITH_NEWS_FULL"]["threshold"])
    events = event_analysis(news_test, probabilities["MARKET_ONLY"]["test"],
                            probabilities["WITH_NEWS_FULL"]["test"],
                            fitted["MARKET_ONLY"]["threshold"], fitted["WITH_NEWS_FULL"]["threshold"])

    market_model_meta = {"selected_configuration": fitted["MARKET_ONLY"]["selected_configuration"], "seed": seed}
    news_model_meta = {"selected_configuration": fitted["WITH_NEWS_FULL"]["selected_configuration"], "seed": seed}
    market_walk, market_walk_aggregate = walk_forward_evaluation(market, market_meta, market_model_meta)
    news_walk, news_walk_aggregate = walk_forward_evaluation(news, news_meta, news_model_meta)
    walk = compare_walk_forward(market_walk, news_walk)
    active_rows = int((news_test["eligible_news_count_recent"] > 0).sum())
    status, status_criteria = research_status(metrics["MARKET_ONLY"], metrics["WITH_NEWS_FULL"], walk, active_rows)

    importance = fitted["WITH_NEWS_FULL"]["feature_importance"]
    news_importance = [row | {"feature_group": "news"} for row in importance if row["feature"] in NEWS_FEATURES_V2]
    market_importance = [row | {"feature_group": "market"} for row in importance if row["feature"] not in NEWS_FEATURES_V2]
    zero_news = sum(abs(row["importance_mean"]) < 1e-8 for row in news_importance)
    threshold_selection = {}
    for name, result in fitted.items():
        validation_classification = metrics[name]["validation"]["classification"]
        confusion = validation_classification["confusion_matrix"]
        threshold_selection[name] = {
            "threshold": result["threshold"],
            "selected_on": "validation_only",
            "validation_signals": int(confusion[0][1] + confusion[1][1]),
            "validation_precision": validation_classification["precision"],
            "validation_expectancy": result["validation_trading"]["expectancy"],
            "validation_net_return": result["validation_trading"]["net_return"],
            "validation_max_drawdown": result["validation_trading"]["max_drawdown"],
            "validation_trades": result["validation_trading"]["trade_count"],
        }

    registry, reports = Path(registry), Path(reports)
    registry.mkdir(parents=True, exist_ok=True); reports.mkdir(parents=True, exist_ok=True)
    model_path = fitted["WITH_NEWS_FULL"]["model"].save(registry / "model.joblib")
    artifact_metadata = {
        **fitted["WITH_NEWS_FULL"]["model"].metadata(), **research_artifact_flags(),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "decision_threshold": fitted["WITH_NEWS_FULL"]["threshold"],
        "feature_count": len(fitted["WITH_NEWS_FULL"]["model"].feature_names()),
        "market_feature_count": len([f for f in fitted["WITH_NEWS_FULL"]["model"].feature_names() if f not in NEWS_FEATURES_V2]),
        "news_feature_count": len([f for f in fitted["WITH_NEWS_FULL"]["model"].feature_names() if f in NEWS_FEATURES_V2]),
        "dataset": {"path": str(Path(news_path).resolve()), "sha256": file_hash(news_path)},
        "metrics": metrics["WITH_NEWS_FULL"], "research_status": status,
        "test_used_for_tuning": False, "threshold_selected_on": "validation",
        "model_sha256": file_hash(model_path)}
    (registry / "metadata.json").write_text(json.dumps(_clean(artifact_metadata), indent=2), encoding="utf-8")
    (registry / "feature_names.json").write_text(json.dumps(fitted["WITH_NEWS_FULL"]["model"].feature_names(), indent=2), encoding="utf-8")

    summary = {"research_status": status, "status_criteria": status_criteria,
               "research_only": True, "news_archive_readiness": "PARTIAL",
               "same_period_verified": True, "seed": seed, "model_family": "sklearn_hist_gradient_boosting",
               "fixed_parameters": parameters,
               "row_counts": {name: len(frame) for name, frame in frames.items()},
               "candidate_feature_counts": {key: len(value) for key, value in features.items()},
               "usable_feature_counts": {name: len(result["model"].feature_names()) for name, result in fitted.items()},
               "thresholds": {name: result["threshold"] for name, result in fitted.items()},
               "threshold_selection": threshold_selection,
               "metrics": metrics, "paired_summary": paired_summary(paired),
               "news_active_analysis": segments, "symbol_analysis": symbols,
               "event_analysis": events, "walk_forward": walk,
               "walk_forward_aggregate": {"market": market_walk_aggregate, "news": news_walk_aggregate},
               "feature_importance": {"top_market": market_importance[:20], "top_news": news_importance[:20],
                                      "zero_importance_news_features": zero_news,
                                      "usable_news_features": len(news_importance)},
               "ablation": {name: metrics[name] for name in
                            ("MARKET_ONLY", "MARKET_PLUS_COUNTS_COVERAGE", "WITH_NEWS_FULL")},
               "limitations": ["News archive readiness is PARTIAL and biased toward GitHub project releases.",
                               "SOL has no active news rows in the test period.",
                               "The project-wide 2023-2025 holdout was viewed in earlier research.",
                               "Permutation importance measures predictive association, not causality."]}
    cleaned = _clean(summary)
    (reports / "comparison_summary.json").write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    paired.to_csv(reports / "paired_predictions.csv", index=False)
    _write_csv(reports / "news_active_analysis.csv", _flatten_analysis(segments))
    _write_csv(reports / "symbol_analysis.csv", _flatten_analysis(symbols))
    _write_csv(reports / "event_analysis.csv", _flatten_analysis(events))
    _write_csv(reports / "feature_importance_news.csv", [_clean(row) for row in news_importance])
    _write_csv(reports / "walk_forward_news.csv", [_clean(row) for row in walk])
    lines = ["# ML WITH_NEWS research comparison", "", f"- Status: **{status}**",
             "- Research only: true", "- News archive readiness: PARTIAL", "",
             "## Test comparison", ""]
    for name in ("MARKET_ONLY", "WITH_NEWS_FULL", "WITH_NEWS_COVERAGE_FILTERED"):
        item = metrics[name]["test"]
        lines.append(f"- {name}: ROC AUC {item['classification']['roc_auc']:.4f}, PR AUC {item['classification']['pr_auc']:.4f}, net return {item['trading']['net_return']:.4%}, trades {item['trading']['trade_count']}")
    lines += ["", "The result is evidence for this dataset only and is not a production-readiness claim."]
    (reports / "comparison_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cleaned


def main(argv=None):
    parser = argparse.ArgumentParser(description="Research-only market versus WITH_NEWS_V2 comparison")
    parser.add_argument("--reports", default=str(DEFAULT_REPORTS)); parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--seed", type=int, default=42); args = parser.parse_args(argv)
    summary = run_research(reports=args.reports, registry=args.registry, seed=args.seed)
    print(json.dumps({"status": summary["research_status"], "thresholds": summary["thresholds"],
                      "usable_feature_counts": summary["usable_feature_counts"],
                      "reports": str(Path(args.reports).resolve()), "registry": str(Path(args.registry).resolve())}, indent=2))


if __name__ == "__main__": main()
