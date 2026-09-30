"""Symbol-balanced research Candidate Filter v2; never imported by trading code."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from app.ml.candidate_filter import (
    CandidateFilter, DEFAULT_DATASET, DEFAULT_METADATA, DEFAULT_REGIME, PARAMETER_GRID,
    RANKER_V2_SCORES, STATUS_CRITERIA as V1_STATUS, clean, file_hash, load_data,
)
from app.ml.funnel_dataset import MEANINGFUL_THRESHOLD

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = PROJECT_ROOT / "data/models/candidate_filter_v2"
DEFAULT_REPORTS = PROJECT_ROOT / "data/ml/reports/candidate_filter_v2"
V1_REGISTRY = PROJECT_ROOT / "data/models/candidate_filter_v1"
SYMBOLS = ("BNBUSDT", "BTCUSDT", "DOGEUSDT", "ETHUSDT", "LINKUSDT", "SOLUSDT", "XRPUSDT")
IDENTITY_FEATURES = [f"symbol_is_{symbol.removesuffix('USDT').lower()}" for symbol in SYMBOLS]
LABELS = ("ideal_candidate", "candidate_top_2")
PARAMETERS = PARAMETER_GRID[0]
CONFIGURATIONS = (
    ("full_87", "none", False),
    ("full_87", "symbol_balanced", False),
    ("full_87", "symbol_class_balanced", False),
    ("full_87", "symbol_class_recency", False),
    ("relative", "symbol_class_balanced", False),
    ("full_87_identity", "symbol_class_balanced", True),
    ("full_87_identity", "symbol_class_recency", True),
)
THRESHOLD_GRID = np.round(np.linspace(.025, .975, 39), 3)


def add_symbol_identity(frame):
    result = frame.copy()
    for symbol, column in zip(SYMBOLS, IDENTITY_FEATURES):
        result[column] = (result["symbol"] == symbol).astype(int)
    return result


def v2_feature_sets(metadata):
    full = list(metadata["schema"]["candidate_feature_columns"])
    context = {"btc_trend", "btc_volatility", "median_universe_return_1h", "median_universe_return_24h",
               "fraction_assets_above_ema", "cross_sectional_dispersion", "market_breadth", "median_universe_rsi"}
    relative = [c for c in full if any(token in c for token in ("rank", "relative_", "zscore", "alignment",
                "confirmation", "breakout", "asset_vs_universe", "volatility_relative")) or c in context]
    return {"full_87": full, "relative": relative, "full_87_identity": full + IDENTITY_FEATURES}


def sample_weights(frame, label, method):
    weight = pd.Series(1., index=frame.index)
    if method in ("symbol_balanced", "symbol_class_balanced", "symbol_class_recency"):
        counts = frame["symbol"].value_counts()
        weight *= frame["symbol"].map(len(frame) / (len(counts) * counts))
    if method in ("symbol_class_balanced", "symbol_class_recency"):
        pair = frame.groupby(["symbol", label]).size()
        symbol_count = frame["symbol"].value_counts()
        values = [symbol_count[s] / (2 * pair.get((s, int(y)), 1)) for s, y in zip(frame.symbol, frame[label])]
        weight *= np.asarray(values)
    if method == "symbol_class_recency":
        times = pd.to_datetime(frame["timestamp"], utc=True)
        position = times.rank(pct=True)
        weight *= .75 + .5 * position
    return (weight / weight.mean()).to_numpy(float)


def fit_model(train, features, label, weighting, parameters=PARAMETERS):
    model = CandidateFilter(features, label, parameters)
    model.model.fit(train[features], train[label].astype(int), sample_weight=sample_weights(train, label, weighting))
    return model


def apply_thresholds(frame, probabilities, thresholds):
    result = frame.copy(); result["candidate_probability"] = probabilities
    if isinstance(thresholds, dict):
        result["candidate_threshold"] = result["symbol"].map(thresholds).astype(float)
    else:
        result["candidate_threshold"] = float(thresholds)
    result["candidate_keep"] = (result["candidate_probability"] >= result["candidate_threshold"]).astype(int)
    return result


def balanced_metrics(frame, probabilities, thresholds, label):
    work = apply_thresholds(frame, probabilities, thresholds)
    groups = work.groupby("timestamp"); sizes = groups.candidate_keep.sum(); universe = groups.size()
    winner = work[work.future_return_rank == 1].copy(); meaningful = winner.best_net_return > MEANINGFUL_THRESHOLD
    strong = winner.best_net_return > .005; strong10 = winner.best_net_return > .01
    selected = work[work.candidate_keep == 1]
    symbol_recall = {}
    for symbol, part in winner[meaningful].groupby("symbol"):
        symbol_recall[symbol] = float(part.candidate_keep.mean())
    recalls = list(symbol_recall.values())
    no_opp_times = winner.loc[winner.best_net_return <= MEANINGFUL_THRESHOLD, "timestamp"]
    return {"winner_retained_rate": float(winner.loc[meaningful, "candidate_keep"].mean()),
            "strong_winner_retained_rate_0_5pct": float(winner.loc[strong, "candidate_keep"].mean()),
            "strong_winner_retained_rate_1pct": float(winner.loc[strong10, "candidate_keep"].mean()),
            "candidate_recall": float(work.loc[work[label] == 1, "candidate_keep"].mean()),
            "candidate_precision": float(selected[label].mean()) if len(selected) else 0.,
            "average_candidate_set_size": float(sizes.mean()), "median_candidate_set_size": float(sizes.median()),
            "empty_candidate_set_rate": float((sizes == 0).mean()), "full_universe_set_rate": float((sizes == universe).mean()),
            "universe_reduction": float(1 - sizes.sum() / universe.sum()),
            "no_opportunity_average_set_size": float(sizes.reindex(no_opp_times).mean()),
            "minimum_symbol_winner_recall": float(min(recalls)) if recalls else 0.,
            "symbol_recall_range": float(max(recalls) - min(recalls)) if recalls else 0.,
            "std_symbol_recall": float(np.std(recalls)) if recalls else 0.,
            "symbol_winner_recall": symbol_recall, "timestamps": int(len(sizes))}


def global_threshold(validation, probabilities, label):
    rows = []
    for threshold in THRESHOLD_GRID:
        rows.append({"threshold": float(threshold), **balanced_metrics(validation, probabilities, threshold, label)})
    feasible = [r for r in rows if r["average_candidate_set_size"] <= 4]
    pool = feasible or rows
    chosen = max(pool, key=lambda r: (r["minimum_symbol_winner_recall"], r["winner_retained_rate"],
                                       r["strong_winner_retained_rate_0_5pct"], -r["symbol_recall_range"],
                                       r["candidate_precision"], -r["average_candidate_set_size"]))
    return chosen["threshold"], chosen, rows


def per_symbol_thresholds(validation, probabilities, label, minimum_winners=40):
    work = validation.copy(); work["probability"] = probabilities
    global_value, _, _ = global_threshold(validation, probabilities, label)
    thresholds = {}
    rows = []
    for symbol, part in work.groupby("symbol"):
        winners = part[(part.future_return_rank == 1) & (part.best_net_return > MEANINGFUL_THRESHOLD)]
        if len(winners) < minimum_winners:
            thresholds[symbol] = global_value; rows.append({"symbol": symbol, "threshold": global_value, "fallback": True})
            continue
        candidates = []
        for threshold in THRESHOLD_GRID:
            keep = part.probability >= threshold
            recall = float((winners.probability >= threshold).mean())
            label_recall = float(keep[part[label] == 1].mean())
            precision = float(part.loc[keep, label].mean()) if keep.any() else 0.
            candidates.append((threshold, recall, label_recall, precision, float(keep.mean())))
        feasible = [r for r in candidates if r[4] <= .65]
        chosen = max(feasible or candidates, key=lambda r: (r[1], r[2], r[3], -r[4]))
        thresholds[symbol] = float(chosen[0]); rows.append({"symbol": symbol, "threshold": chosen[0],
            "validation_winner_recall": chosen[1], "validation_label_recall": chosen[2],
            "validation_precision": chosen[3], "validation_keep_rate": chosen[4], "fallback": False})
    metrics = balanced_metrics(validation, probabilities, thresholds, label)
    return thresholds, metrics, rows


def select_configuration(frame, metadata, parameters=PARAMETERS):
    data = add_symbol_identity(frame); train = data[data.split == "train"]; validation = data[data.split == "validation"]
    sets = v2_feature_sets(metadata); results = []
    for label in LABELS:
        for feature_name, weighting, identity in CONFIGURATIONS:
            features = sets[feature_name]
            model = fit_model(train, features, label, weighting, parameters)
            probability = model.predict_proba(validation)
            global_value, global_metrics, global_rows = global_threshold(validation, probability, label)
            results.append({"label": label, "feature_set": feature_name, "weighting": weighting,
                            "symbol_identity": identity, "threshold_method": "global", "thresholds": global_value,
                            "threshold_rows": global_rows, "features": features, "model": model,
                            "validation": global_metrics, "validation_brier": brier_score_loss(validation[label], probability)})
            symbol_values, symbol_metrics, symbol_rows = per_symbol_thresholds(validation, probability, label)
            results.append({"label": label, "feature_set": feature_name, "weighting": weighting,
                            "symbol_identity": identity, "threshold_method": "per_symbol", "thresholds": symbol_values,
                            "threshold_rows": symbol_rows, "features": features, "model": model,
                            "validation": symbol_metrics, "validation_brier": brier_score_loss(validation[label], probability)})
    feasible = [r for r in results if r["validation"]["average_candidate_set_size"] <= 4]
    winner = max(feasible or results, key=lambda r: (r["validation"]["minimum_symbol_winner_recall"],
        r["validation"]["winner_retained_rate"], r["validation"]["strong_winner_retained_rate_0_5pct"],
        -r["validation"]["symbol_recall_range"], r["validation"]["candidate_precision"]))
    return winner, results, data


def bias_diagnostics(frame, v1_model, v1_threshold):
    rows = []
    for split, split_frame in frame.groupby("split"):
        probability = v1_model.predict_proba(split_frame); work = apply_thresholds(split_frame, probability, v1_threshold)
        for symbol, part in work.groupby("symbol"):
            winners = part[part.future_return_rank == 1]
            rows.append({"split": split, "symbol": symbol, "rows": len(part), "label_prevalence": part.ideal_candidate.mean(),
                "winner_frequency": len(winners) / part.timestamp.nunique(), "winner_retained": winners.candidate_keep.mean(),
                "mean_probability": part.candidate_probability.mean(), "median_probability": part.candidate_probability.median(),
                "brier": brier_score_loss(part.ideal_candidate, part.candidate_probability), "keep_rate": part.candidate_keep.mean(),
                "average_target_return": part.future_net_return_4h.mean(), "feature_missing_rate": part[v1_model.features].isna().mean().mean(),
                "median_volatility": part.volatility_20_1h.median(), "median_momentum": part.momentum_4_1h.median()})
    return pd.DataFrame(rows)


def train_regime_gate(regime, features):
    train = regime[regime.split == "train"]; validation = regime[regime.split == "validation"]
    params = dict(PARAMETERS); params.update(max_iter=50, min_samples_leaf=100)
    model = HistGradientBoostingClassifier(**params).fit(train[features], train.any_profitable_asset_4h)
    probability = model.predict_proba(validation[features])[:, 1]; rows = []
    for threshold in THRESHOLD_GRID:
        predicted = probability >= threshold; positive = validation.any_profitable_asset_4h.astype(bool)
        rows.append({"threshold": threshold, "opportunity_recall": float(predicted[positive].mean()),
                     "predicted_opportunity_rate": float(predicted.mean()),
                     "no_opportunity_suppression": float((~predicted[~positive]).mean())})
    eligible = [r for r in rows if r["opportunity_recall"] >= .95]
    chosen = max(eligible or rows, key=lambda r: (r["no_opportunity_suppression"], r["opportunity_recall"]))
    return model, float(chosen["threshold"]), rows, params


def combined_gate_metrics(candidate_work, regime, gate_model, gate_features, gate_threshold, label):
    probability = gate_model.predict_proba(regime[gate_features])[:, 1]
    gate = pd.Series(probability >= gate_threshold, index=regime.timestamp)
    result = candidate_work.copy(); result["regime_gate_probability"] = result.timestamp.map(pd.Series(probability, index=regime.timestamp))
    result["regime_gate_open"] = result.timestamp.map(gate).fillna(False).astype(int)
    result["candidate_keep"] *= result["regime_gate_open"]
    # Calculate with precomputed binary decisions via pseudo probabilities.
    metrics = balanced_metrics(result, result.candidate_keep.astype(float), .5, label)
    return result, metrics


def symbol_report(work, label):
    rows = []
    for symbol, part in work.groupby("symbol"):
        winners = part[part.future_return_rank == 1]; meaningful = winners.best_net_return > MEANINGFUL_THRESHOLD
        selected = part[part.candidate_keep == 1]
        rows.append({"symbol": symbol, "winner_frequency": len(winners),
            "winner_retained": winners.loc[meaningful, "candidate_keep"].mean(),
            "candidate_recall": part.loc[part[label] == 1, "candidate_keep"].mean(),
            "candidate_precision": selected[label].mean() if len(selected) else 0.,
            "keep_rate": part.candidate_keep.mean(), "mean_probability": part.candidate_probability.mean()})
    return pd.DataFrame(rows)


def drift_analysis(data, features, model):
    probability = model.predict_proba(data); work = data.copy(); work["probability"] = probability
    train = work[work.split == "train"]
    rows = []
    for feature in features:
        median = train[feature].median(); scale = train[feature].quantile(.75) - train[feature].quantile(.25)
        for split in ("validation", "test"):
            value = work.loc[work.split == split, feature].median()
            rows.append({"feature": feature, "split": split, "train_median": median, "split_median": value,
                         "normalized_median_shift": float(abs(value - median) / max(abs(scale), 1e-9))})
    for split, part in work.groupby("split"):
        rows.append({"feature": "__prediction_probability__", "split": split, "train_median": train.probability.median(),
                     "split_median": part.probability.median(), "normalized_median_shift": abs(part.probability.median() - train.probability.median())})
    return pd.DataFrame(rows).sort_values("normalized_median_shift", ascending=False)


def baseline_row(name, work, label):
    return {"method": name, **balanced_metrics(work, work.candidate_keep.astype(float), .5, label)}


def manual_validation(work):
    winners = work[work.future_return_rank == 1].set_index("timestamp")
    sizes = work.groupby("timestamp").candidate_keep.sum()
    selectors = {"BTC_winner": winners.symbol.eq("BTCUSDT"), "LINK_winner": winners.symbol.eq("LINKUSDT"),
        "NO_OPPORTUNITY": winners.best_net_return.le(MEANINGFUL_THRESHOLD), "STRONG": winners.best_net_return.gt(.01),
        "empty": winners.index.to_series().map(sizes).eq(0), "two_to_four": winners.index.to_series().map(sizes).between(2,4)}
    checks=[]
    for case,mask in selectors.items():
        rows=winners.loc[mask]
        if rows.empty: checks.append({"case":case,"available":False}); continue
        stamp=rows.index[len(rows)//2]; part=work[work.timestamp==stamp]
        checks.append({"case":case,"available":True,"timestamp":stamp,"actual_winner":part.loc[part.future_return_rank==1,"symbol"].iloc[0],
          "candidate_set":part.loc[part.candidate_keep==1,"symbol"].tolist(),"probabilities":part.set_index("symbol").candidate_probability.to_dict(),
          "thresholds":part.set_index("symbol").candidate_threshold.to_dict(),"logic_verified":bool(((part.candidate_probability>=part.candidate_threshold).astype(int)==part.candidate_keep).all())})
    return {"passed":all(x.get("logic_verified",True) for x in checks),"checks":checks}


def downstream(work, v1_work):
    rows = []
    scores = pd.read_csv(RANKER_V2_SCORES, usecols=["timestamp", "symbol", "predicted_net_return"])
    scores.timestamp = pd.to_datetime(scores.timestamp, utc=True)
    for rule, score in (("highest_momentum", "momentum_4_1h"), ("frozen_ranker_v2", "predicted_net_return")):
        for name, base in (("no_filter", work.assign(candidate_keep=1)), ("v1", v1_work), ("v2", work)):
            part = base.merge(scores, on=["timestamp", "symbol"], how="left") if score == "predicted_net_return" else base
            selected = part[part.candidate_keep == 1]
            idx = selected.groupby("timestamp")[score].idxmax(); chosen = selected.loc[idx]
            rows.append({"rule": rule, "filter": name, "top1_accuracy": (chosen.future_return_rank == 1).mean(),
                         "decided_timestamps": chosen.timestamp.nunique()})
    return pd.DataFrame(rows)


def walk_forward(data, metadata, winner):
    times = np.array(sorted(data.timestamp.unique())); stamp = data.timestamp; target = data.target_timestamp
    windows = [(.45, .55, .70), (.55, .70, .85), (.70, .85, 1.)]; rows = []
    v1_features = metadata["schema"]["candidate_feature_columns"]
    for fold, (a, b, c) in enumerate(windows, 1):
        ta, tb, tc = times[int(len(times)*a)], times[int(len(times)*b)], times[min(len(times)-1, int(len(times)*c)-1)]
        train = data[(stamp < ta) & (target < ta)]; validation = data[(stamp >= ta)&(stamp < tb)&(target < tb)]; evaluation = data[(stamp >= tb)&(stamp <= tc)]
        for version, features, weighting in (("v1", v1_features, "none"), ("v2", winner["features"], winner["weighting"])):
            model = fit_model(train, features, winner["label"], weighting)
            vp = model.predict_proba(validation)
            if version == "v2" and winner["threshold_method"] == "per_symbol": thresholds, _, _ = per_symbol_thresholds(validation, vp, winner["label"], 20)
            else: thresholds, _, _ = global_threshold(validation, vp, winner["label"])
            metrics = balanced_metrics(evaluation, model.predict_proba(evaluation), thresholds, winner["label"])
            rows.append({"fold": fold, "version": version, "threshold_method": "global" if version == "v1" else winner["threshold_method"], **metrics})
    return pd.DataFrame(rows)


def train_and_evaluate(dataset=DEFAULT_DATASET, regime_path=DEFAULT_REGIME, metadata_path=DEFAULT_METADATA,
                       registry=DEFAULT_REGISTRY, reports=DEFAULT_REPORTS, force=False):
    registry, reports = Path(registry), Path(reports)
    if registry.exists() and any(registry.iterdir()) and not force: raise FileExistsError(registry)
    registry.mkdir(parents=True, exist_ok=True); reports.mkdir(parents=True, exist_ok=True)
    frame, metadata = load_data(dataset, regime_path, metadata_path); winner, configs, data = select_configuration(frame, metadata)
    train = data[data.split == "train"]; validation = data[data.split == "validation"]; test = data[data.split == "test"]
    model_path = winner["model"].save(registry / "candidate_model.joblib")
    test_probability = winner["model"].predict_proba(test); test_work = apply_thresholds(test, test_probability, winner["thresholds"])
    test_metrics = balanced_metrics(test, test_probability, winner["thresholds"], winner["label"])

    v1_meta = json.loads((V1_REGISTRY / "metadata.json").read_text()); v1_model = CandidateFilter.load(V1_REGISTRY / "model.joblib")
    v1_hash_before = file_hash(V1_REGISTRY / "model.joblib")
    bias = bias_diagnostics(frame, v1_model, v1_meta["candidate_threshold"])
    v1_work = apply_thresholds(test, v1_model.predict_proba(test), v1_meta["candidate_threshold"])
    v1_metrics = balanced_metrics(test, v1_work.candidate_probability, v1_meta["candidate_threshold"], v1_meta["winning_label"])

    regime = pd.read_csv(regime_path, parse_dates=["timestamp"]); gate_features = metadata["schema"]["regime_feature_columns"]
    gate_model, gate_threshold, gate_rows, gate_params = train_regime_gate(regime, gate_features)
    gate_path = registry / "regime_gate.joblib"; joblib.dump({"model": gate_model, "features": gate_features, "parameters": gate_params}, gate_path)
    gate_test = regime[regime.split == "test"].copy(); gate_probability = gate_model.predict_proba(gate_test[gate_features])[:, 1]
    gate_test_metrics = {"roc_auc": roc_auc_score(gate_test.any_profitable_asset_4h, gate_probability),
        "pr_auc": average_precision_score(gate_test.any_profitable_asset_4h, gate_probability),
        "opportunity_recall": float((gate_probability[gate_test.any_profitable_asset_4h == 1] >= gate_threshold).mean()),
        "strong_opportunity_recall": float((gate_probability[gate_test.strong_opportunity_exists_4h == 1] >= gate_threshold).mean()),
        "threshold": gate_threshold}
    combined_work, combined_metrics = combined_gate_metrics(test_work, gate_test, gate_model, gate_features, gate_threshold, winner["label"])

    symbols = symbol_report(test_work, winner["label"]); walk = walk_forward(data, metadata, winner)
    drift = drift_analysis(data, winner["features"], winner["model"]); down = downstream(test_work, v1_work)
    # Simple baselines.
    baseline_rows = [baseline_row("candidate_filter_v1", v1_work, v1_meta["winning_label"]), baseline_row("candidate_filter_v2", test_work, winner["label"])]
    masks = {"above_ema": test.ema_distance_20_1h > 0,
             "top_3_relative_strength": test.groupby("timestamp").return_rank_24h.rank(method="first", ascending=False) <= 3,
             "top_2_momentum": test.groupby("timestamp").momentum_4_1h.rank(method="first", ascending=False) <= 2,
             "all_assets": pd.Series(True, index=test.index)}
    for name, mask in masks.items(): baseline_rows.append(baseline_row(name, apply_thresholds(test, mask.astype(float), .5), winner["label"]))
    baselines = pd.DataFrame(baseline_rows)

    candidate_sets = test_work[test_work.candidate_keep == 1].groupby("timestamp").symbol.apply(lambda x: ",".join(x))
    misses = test_work[(test_work.future_return_rank == 1) & (test_work.best_net_return > MEANINGFUL_THRESHOLD) & (test_work.candidate_keep == 0)].copy()
    misses["candidate_set"] = misses.timestamp.map(candidate_sets).fillna(""); misses["regime_gate_score"] = misses.timestamp.map(pd.Series(gate_probability, index=gate_test.timestamp))
    feature_summary = [c for c in ("momentum_4_1h", "return_24h", "ema_distance_20_1h", "relative_strength_vs_market", "trend_alignment_score") if c in winner["features"]]
    misses["feature_summary"] = misses[feature_summary].apply(lambda r: json.dumps(clean(r.to_dict())), axis=1)
    misses = misses[["timestamp", "symbol", "future_net_return_4h", "candidate_probability", "candidate_threshold", "regime_gate_score", "candidate_set", "feature_summary"]]
    manual = manual_validation(test_work)

    comparison = []
    for item in configs:
        comparison.append({"label": item["label"], "feature_set": item["feature_set"], "weighting": item["weighting"],
            "symbol_identity": item["symbol_identity"], "threshold_method": item["threshold_method"],
            "validation_brier": item["validation_brier"], **{k:v for k,v in item["validation"].items() if k != "symbol_winner_recall"}})
    thresholds = winner["threshold_rows"]
    min_recall = test_metrics["minimum_symbol_winner_recall"]
    useful = (test_metrics["winner_retained_rate"] >= .75 and test_metrics["strong_winner_retained_rate_0_5pct"] >= .80 and
              test_metrics["average_candidate_set_size"] <= 4 and min_recall >= .50)
    weak = test_metrics["winner_retained_rate"] >= .65 and min_recall >= .35 and test_metrics["average_candidate_set_size"] <= 4.5
    status = "USEFUL_FUNNEL" if useful else ("WEAK_BALANCED" if weak else "FAILED")

    bias.to_csv(reports/"bias_diagnostics.csv", index=False); pd.DataFrame(comparison).to_csv(reports/"configuration_comparison.csv", index=False)
    symbols.to_csv(reports/"symbol_results.csv", index=False); pd.DataFrame(thresholds).to_csv(reports/"thresholds.csv", index=False)
    pd.DataFrame(gate_rows).to_csv(reports/"regime_gate.csv", index=False); pd.DataFrame([{"variant":"candidate_only",**test_metrics},{"variant":"regime_gate_plus_candidate",**combined_metrics}]).drop(columns="symbol_winner_recall").to_csv(reports/"combined_funnel.csv", index=False)
    walk.drop(columns="symbol_winner_recall").to_csv(reports/"walk_forward.csv", index=False); drift.to_csv(reports/"drift_analysis.csv", index=False)
    misses.to_csv(reports/"winner_misses.csv", index=False); misses[misses.symbol == "BTCUSDT"].to_csv(reports/"btc_misses.csv", index=False)
    baselines.drop(columns="symbol_winner_recall").to_csv(reports/"baseline_comparison.csv", index=False); down.to_csv(reports/"downstream_diagnostic.csv", index=False)
    (reports/"manual_validation.json").write_text(json.dumps(clean(manual),indent=2))

    summary = {"model_version":"candidate_filter_v2", "research_only":True, "not_for_batch3_control":True,
        "pipeline_stage":"candidate_filter_v2", "status":status,
        "status_criteria":{"USEFUL_FUNNEL":"winner>=75%, strong>=80%, avg set<=4, min symbol>=50%",
                           "WEAK_BALANCED":"winner>=65%, avg set<=4.5, min symbol>=35%", "FAILED":"otherwise"},
        "dataset_version":"funnel-dataset-v1", "labels_tested":list(LABELS), "winning_label":winner["label"],
        "winning_weighting":winner["weighting"], "winning_feature_set":winner["feature_set"],
        "symbol_identity":winner["symbol_identity"], "temporal_weighting":winner["weighting"] == "symbol_class_recency",
        "threshold_method":winner["threshold_method"], "thresholds":winner["thresholds"], "parameters":winner["model"].parameters,
        "validation_metrics":winner["validation"], "test_metrics":test_metrics, "v1_test_metrics":v1_metrics,
        "regime_gate_test":gate_test_metrics, "combined_metrics":combined_metrics,
        "symbol_results":symbols.to_dict("records"), "walk_forward":walk.to_dict("records"),
        "downstream":down.to_dict("records"), "top_drifting_features":drift.head(15).to_dict("records"),
        "winner_miss_count":len(misses), "btc_miss_count":int((misses.symbol == "BTCUSDT").sum()),
        "manual_validation":manual,
        "candidate_model_sha256":file_hash(model_path), "regime_model_sha256":file_hash(gate_path),
        "candidate_filter_v1_sha256_before":v1_hash_before, "candidate_filter_v1_sha256_after":file_hash(V1_REGISTRY/"model.joblib"),
        "test_used_for_selection":False}
    (registry/"features.json").write_text(json.dumps(winner["features"], indent=2)); (registry/"metadata.json").write_text(json.dumps(clean({**summary,"created_at":datetime.now(timezone.utc).isoformat()}),indent=2))
    (reports/"summary.json").write_text(json.dumps(clean(summary),indent=2)); (reports/"summary.md").write_text(f"# Candidate Filter v2\n\n- Status: **{status}**\n- Test winner retained: {test_metrics['winner_retained_rate']:.2%}\n- Minimum symbol recall: {min_recall:.2%}\n- Average set: {test_metrics['average_candidate_set_size']:.2f}\n")
    return clean(summary)


def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--force",action="store_true"); p.add_argument("--dataset",default=str(DEFAULT_DATASET)); p.add_argument("--regime",default=str(DEFAULT_REGIME)); p.add_argument("--metadata",default=str(DEFAULT_METADATA)); p.add_argument("--registry",default=str(DEFAULT_REGISTRY)); p.add_argument("--reports",default=str(DEFAULT_REPORTS)); a=p.parse_args(argv)
    result=train_and_evaluate(a.dataset,a.regime,a.metadata,a.registry,a.reports,a.force)
    print(json.dumps({k:result[k] for k in ("status","winning_label","winning_weighting","winning_feature_set","symbol_identity","temporal_weighting","threshold_method","validation_metrics","test_metrics","regime_gate_test","combined_metrics","candidate_model_sha256","regime_model_sha256")},indent=2))


if __name__=="__main__": main()
