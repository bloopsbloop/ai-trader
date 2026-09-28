from pathlib import Path
import json

import numpy as np
import pandas as pd
import pytest

from app.ml.evaluate import _write_csv, walk_forward_evaluation
from app.ml.metrics import classification_metrics, select_threshold, simulate_trades
from app.ml.model import HistGradientBoostingDecisionModel, balanced_sample_weights
from app.ml.train import load_dataset, train_and_save, train_candidate


def frame(rows=900):
    time = pd.date_range("2023-01-01", periods=rows, freq="h", tz="UTC")
    wave = np.sin(np.arange(rows) / 9)
    labels = (np.roll(wave, -4) > wave + .15).astype(int)
    split = np.where(np.arange(rows) < rows * .6, "train",
                     np.where(np.arange(rows) < rows * .8, "validation", "test"))
    return pd.DataFrame({"timestamp": time, "target_timestamp": time + pd.Timedelta(hours=4),
                         "source_open_time": time - pd.Timedelta(hours=1),
                         "symbol": np.where(np.arange(rows) % 2, "BTCUSDT", "ETHUSDT"),
                         "timeframe": "1h", "close": 100 + wave,
                         "f1": wave, "f2": np.cos(np.arange(rows) / 11),
                         "future_net_return": np.roll(wave, -4) / 100,
                         "label": labels, "split": split})


def metadata():
    return {"features": ["f1", "f2"], "news": {"included": False},
            "feature_version": "test", "rows": 900,
            "target": {"name": "test"}, "holdout_limitation": "test",
            "periods": {name: {"start": "x", "end": "y", "rows": 1} for name in ("train", "validation", "test")},
            "config": {"commission": .0004, "slippage": .0005},
            "class_distribution": {}}


PARAMS = ({"max_iter": 8, "max_leaf_nodes": 7, "min_samples_leaf": 10,
           "learning_rate": .1, "l2_regularization": 1, "early_stopping": False},)


def test_model_training_probability_schema_save_load_and_reproducibility(tmp_path):
    data = frame(300)
    train = data.iloc[:200]
    first = HistGradientBoostingDecisionModel(["f1", "f2"], parameters=PARAMS[0], seed=7).fit(train, train.label)
    second = HistGradientBoostingDecisionModel(["f1", "f2"], parameters=PARAMS[0], seed=7).fit(train, train.label)
    probability = first.predict_proba(data.iloc[200:])
    assert np.all((0 <= probability) & (probability <= 1))
    np.testing.assert_allclose(probability, second.predict_proba(data.iloc[200:]))
    rows = first.prediction_rows(data.iloc[200:203], .6, data.timestamp.iloc[200:203], data.symbol.iloc[200:203])
    assert set(rows[0]) == {"timestamp", "symbol", "probability_positive", "prediction", "threshold", "model_version", "calibrated"}
    loaded = HistGradientBoostingDecisionModel.load(first.save(tmp_path / "model.joblib"))
    np.testing.assert_allclose(probability, loaded.predict_proba(data.iloc[200:]))


def test_balanced_weights_do_not_oversample():
    labels = np.array([0] * 8 + [1] * 2)
    weights = balanced_sample_weights(labels)
    assert len(weights) == len(labels)
    assert weights[labels == 1].sum() == pytest.approx(weights[labels == 0].sum())


def test_threshold_selection_uses_supplied_validation_only():
    data = frame(300).iloc[100:250].copy()
    probability = np.linspace(.1, .9, len(data))
    selected, candidates = select_threshold(data, probability, data.label, (.5, .6, .7), minimum_trades=1)
    changed_elsewhere = frame(300).iloc[250:].copy()
    changed_elsewhere["label"] = 1 - changed_elsewhere["label"]
    repeated, _ = select_threshold(data, probability, data.label, (.5, .6, .7), minimum_trades=1)
    assert selected["threshold"] == repeated["threshold"]
    assert len(candidates) == 3


def test_training_selection_is_unchanged_when_test_is_mutated():
    data = frame()
    first = train_candidate(data, metadata(), seed=11, threshold_grid=(.5, .6), parameter_candidates=PARAMS)
    changed = data.copy()
    changed.loc[changed.split == "test", ["label", "f1", "f2"]] = [1, 999, -999]
    second = train_candidate(changed, metadata(), seed=11, threshold_grid=(.5, .6), parameter_candidates=PARAMS)
    assert first["threshold"] == second["threshold"]
    assert first["selected_configuration"]["parameters"] == second["selected_configuration"]["parameters"]
    assert first["calibration"] == second["calibration"]


def test_trading_simulation_respects_holding_period_and_cost_adjusted_return():
    data = frame(20)
    probability = np.ones(len(data))
    result = simulate_trades(data, probability, .5, position_fraction=.1)
    assert result["trade_count"] <= 5
    assert result["turnover"] > 0 and result["fees"] > 0
    assert result["ending_equity"] == pytest.approx(1000 + result["realized_pnl"])


def test_classification_metrics_schema():
    result = classification_metrics([0, 0, 1, 1], [.1, .4, .6, .9], .5)
    assert set(("roc_auc", "pr_auc", "precision", "recall", "f1", "brier", "confusion_matrix")) <= set(result)
    assert result["confusion_matrix"] == [[2, 0], [0, 2]]


def test_walk_forward_evaluates_future_folds_only():
    data = frame()
    model_metadata = {"selected_configuration": {"parameters": PARAMS[0], "class_weight": None}, "seed": 3}
    folds, aggregate = walk_forward_evaluation(data, metadata(), model_metadata, (.5, .6))
    assert len(folds) == 3 and aggregate["folds"] == 3
    for fold in folds:
        assert pd.Timestamp(fold["threshold_selected_on"]["end"]) < pd.Timestamp(fold["evaluated_period"]["start"])


def test_feature_importance_csv_export(tmp_path):
    path = tmp_path / "feature_importance.csv"
    _write_csv(path, [{"feature": "f1", "importance_mean": .2, "importance_std": .01}])
    assert "importance_mean" in path.read_text(encoding="utf-8")


def test_registry_metadata_and_feature_importance(tmp_path):
    dataset = tmp_path / "without_news.csv.gz"
    info_path = tmp_path / "without_news.metadata.json"
    registry = tmp_path / "registry"
    frame().to_csv(dataset, index=False, compression="gzip")
    info_path.write_text(json.dumps(metadata()), encoding="utf-8")
    saved = train_and_save(dataset, info_path, registry, seed=5,
                           threshold_grid=(.5, .6), force=False,
                           parameter_candidates=PARAMS)
    assert saved["historical_news_available"] is False
    assert saved["test_evaluated_during_training"] is False
    assert saved["feature_names"] == ["f1", "f2"]
    assert saved["feature_importance"]
    assert {"model.joblib", "metadata.json", "feature_names.json", "training_config.json"} <= {
        path.name for path in registry.iterdir()
    }
    with pytest.raises(FileExistsError):
        train_and_save(dataset, info_path, registry, parameter_candidates=PARAMS)


def test_news_training_refused_without_historical_coverage(tmp_path):
    data = frame(20)
    dataset = tmp_path / "data.csv.gz"
    data.to_csv(dataset, index=False, compression="gzip")
    info = metadata() | {"news": {"included": True, "historical_observations_present": False}}
    info_path = tmp_path / "metadata.json"
    info_path.write_text(json.dumps(info), encoding="utf-8")
    with pytest.raises(ValueError, match="historical news coverage"):
        load_dataset(dataset, info_path)
