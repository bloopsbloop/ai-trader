from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.ml.candidate_filter import (
    CandidateFilter,
    STATUS_CRITERIA,
    assert_feature_safety,
    funnel_metrics,
    select_configuration,
    select_threshold,
    set_distribution,
    scored,
    symbol_results,
    walk_forward,
)

FAST = {"learning_rate": .1, "max_iter": 5, "max_leaf_nodes": 5,
        "min_samples_leaf": 2, "l2_regularization": 1.,
        "early_stopping": False, "random_state": 42}


def frame(hours=90):
    rows = []
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
    for i, timestamp in enumerate(pd.date_range("2024-01-01", periods=hours, freq="1h", tz="UTC")):
        split = "train" if i < hours * .6 else ("validation" if i < hours * .8 else "test")
        values = [.012 * np.sin(i / 5 + asset) + .002 * asset for asset in range(4)]
        ranks = pd.Series(values).rank(method="first", ascending=False).astype(int).tolist()
        best = max(values)
        category = "STRONG" if best > .01 else ("MEDIUM" if best > .005 else ("WEAK" if best > .001 else "NO_OPPORTUNITY"))
        for asset, symbol in enumerate(symbols):
            value = values[asset]
            rows.append({
                "timestamp": timestamp, "target_timestamp": timestamp + pd.Timedelta(hours=4),
                "symbol": symbol, "split": split, "eligible_universe_size": 4,
                "m": np.sin(i / 5 + asset), "x": asset / 4, "a": np.cos(i / 7),
                "future_net_return_4h": value, "future_return_rank": ranks[asset],
                "candidate_positive": int(value > .001), "candidate_strong": int(value > .005),
                "candidate_top_half": int(ranks[asset] <= 2 and value > 0),
                "candidate_top_2": int(ranks[asset] <= 2 and value > 0),
                "ideal_candidate": int(ranks[asset] <= 2 and value > .001),
                "best_net_return": best, "regime_category": category,
                "best_minus_second": best - sorted(values)[-2],
                "best_minus_median": best - np.median(values),
                "future_cross_sectional_dispersion": np.std(values),
            })
    return pd.DataFrame(rows)


def metadata():
    return {"schema": {"candidate_feature_columns": ["m", "x", "a"],
                       "additional_candidate_features": ["a"],
                       "candidate_target_columns": ["candidate_top_2", "ideal_candidate", "candidate_positive",
                                                    "future_net_return_4h", "future_return_rank"]}}


def test_candidate_model_training_and_probability_schema():
    data = frame(); train = data[data.split == "train"]
    model = CandidateFilter(["m", "x", "a"], "ideal_candidate", FAST).fit(train)
    probability = model.predict_proba(data[data.split == "validation"])
    assert len(probability) == len(data[data.split == "validation"])
    assert np.logical_and(probability >= 0, probability <= 1).all()


def test_threshold_selection_respects_size_constraint():
    validation = frame()[lambda x: x.split == "validation"]
    probabilities = np.linspace(0, 1, len(validation))
    selected, rows = select_threshold(validation, probabilities, "ideal_candidate", [.2, .4, .6, .8])
    assert selected["average_candidate_set_size"] <= 4
    assert selected in rows


def test_candidate_set_can_be_empty_and_distribution_is_complete():
    data = frame(10)
    work = scored(data, np.zeros(len(data)), .5)
    assert work.groupby("timestamp").candidate_keep.sum().eq(0).all()
    distribution = set_distribution(work)
    assert distribution.candidate_set_size.tolist() == list(range(8))
    assert distribution.loc[0, "frequency"] == 1


def test_winner_and_strong_retention_metrics():
    data = frame(20)
    probability = (data.future_return_rank == 1).astype(float)
    metrics = funnel_metrics(data, probability, .5, "ideal_candidate")
    assert metrics["winner_retained_rate"] == 1
    assert metrics["strong_winner_retained_rate_0_5pct"] == 1
    assert metrics["average_candidate_set_size"] == 1


def test_feature_safety_rejects_targets():
    assert_feature_safety(["m", "x", "a"], metadata())
    with pytest.raises(ValueError):
        assert_feature_safety(["m", "future_net_return_4h"], metadata())


def test_per_symbol_reporting():
    data = scored(frame(20), np.tile([.9, .8, .2, .1], 20), .5)
    result = symbol_results(data, "ideal_candidate")
    assert set(result.symbol) == {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}
    assert set(["recall", "precision", "keep_rate", "winner_retained_rate"]).issubset(result.columns)


def test_test_label_mutation_does_not_change_selection():
    data = frame()
    first, _ = select_configuration(data, metadata(), [FAST])
    changed = data.copy()
    mask = changed.split == "test"
    changed.loc[mask, ["candidate_top_2", "ideal_candidate", "candidate_positive"]] = 1 - changed.loc[mask, ["candidate_top_2", "ideal_candidate", "candidate_positive"]]
    changed.loc[mask, "future_net_return_4h"] *= -100
    second, _ = select_configuration(changed, metadata(), [FAST])
    assert (first["label"], first["feature_set"], first["parameter_id"], first["threshold"]) == (
        second["label"], second["feature_set"], second["parameter_id"], second["threshold"])


def test_walk_forward_outputs_all_funnel_metrics():
    data = frame(120)
    result = walk_forward(data, ["m", "x", "a"], "ideal_candidate", FAST)
    assert len(result) == 3
    assert set(["winner_retained_rate", "strong_winner_retained_rate_0_5pct",
                "average_candidate_set_size", "candidate_precision",
                "empty_candidate_set_rate", "full_universe_set_rate"]).issubset(result.columns)


def test_model_save_load_and_research_metadata(tmp_path):
    data = frame(); train = data[data.split == "train"]
    model = CandidateFilter(["m", "x", "a"], "ideal_candidate", FAST).fit(train)
    path = model.save(tmp_path / "model.joblib")
    loaded = CandidateFilter.load(path)
    np.testing.assert_allclose(model.predict_proba(train), loaded.predict_proba(train))
    research = {"research_only": True, "not_for_batch3_control": True,
                "pipeline_stage": "candidate_filter", "status_criteria": STATUS_CRITERIA}
    meta = tmp_path / "metadata.json"; meta.write_text(json.dumps(research))
    assert json.loads(meta.read_text())["not_for_batch3_control"] is True
