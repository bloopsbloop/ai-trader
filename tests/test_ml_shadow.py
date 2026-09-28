from datetime import datetime, timedelta, timezone
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.ai.runner import AIRunner
from app.ai.storage import AIStorage
from app.ml.features import FEATURE_VERSION, market_feature_names
from app.ml.shadow import (EXPERIMENT_ID, MLShadowService, _quality,
                           build_live_feature_row, comparison_categories,
                           disagreement_rows, export_forward, forward_metrics,
                           per_symbol_metrics, probability_buckets, shadow_report,
                           theoretical_curve)
from app.ml.model import HistGradientBoostingDecisionModel
from app.ml.train import file_hash
from app.models.candle import Candle
from tests.test_ai import Collector, Market, NOW, bars


def long_bars(timeframe, now=NOW, count=120, future=False):
    seconds = {"15m": 900, "1h": 3600, "4h": 14400}[timeframe]
    boundary = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, timezone.utc)
    rows = []
    for index in range(count):
        opening = boundary - timedelta(seconds=seconds * (count - index))
        price = 100 + index * .1 + np.sin(index / 7)
        rows.append(Candle(opening, price, price + 1, price - 1, price + .2, 100 + index))
    if future:
        rows.append(Candle(boundary, 999, 1000, 998, 999, 9999))
    return rows


def candles(now=NOW, future=False):
    return {timeframe: long_bars(timeframe, now, future=future) for timeframe in ("15m", "1h", "4h")}


class FakeModel:
    def __init__(self, names, probability=.7):
        self.names = list(names)
        self.medians = {name: 0.0 for name in names}
        self.probability = probability

    def feature_names(self):
        return list(self.names)

    def _matrix(self, frame):
        return frame[self.names].fillna(pd.Series(self.medians)).to_numpy(dtype=float)

    def predict_proba(self, frame):
        self._matrix(frame)
        return np.full(len(frame), self.probability)


def service(tmp_path, probability=.7, candle_data=None):
    candle_data = candle_data or candles()
    row = build_live_feature_row(candle_data, NOW)
    names = market_feature_names(row)
    model = FakeModel(names, probability)
    metadata = {"model_version": "ml_v1_market", "feature_version": FEATURE_VERSION,
                "feature_names": names, "decision_threshold": .6}
    storage = AIStorage(tmp_path / "shadow.sqlite3")
    return MLShadowService(storage, model=model, metadata=metadata, now=lambda: NOW), candle_data


def heuristic(decision="BUY"):
    return [{"symbol": "BTCUSDT", "score": .81, "decision": decision}]


def test_closed_candles_only_and_higher_timeframe_alignment():
    plain = build_live_feature_row(candles(), NOW)
    with_future = build_live_feature_row(candles(future=True), NOW)
    pd.testing.assert_frame_equal(plain.reset_index(drop=True), with_future.reset_index(drop=True))
    timestamp = pd.Timestamp(plain.iloc[0]["timestamp"])
    assert all(pd.Timestamp(plain.iloc[0][f"source_timestamp_{tf}"]) <= timestamp
               for tf in ("15m", "1h", "4h"))


def test_schema_order_and_trained_nan_handling_are_enforced(tmp_path):
    obj, data = service(tmp_path)
    assert obj._prediction("BTCUSDT", data, NOW, heuristic()[0])["feature_hash"]
    bad = FakeModel(obj.model.feature_names() + ["unknown"])
    info = obj.metadata | {"feature_names": bad.feature_names()}
    broken = MLShadowService(obj.storage, model=bad, metadata=info, now=lambda: NOW)
    with pytest.raises(ValueError, match="Feature schema mismatch"):
        broken._prediction("BTCUSDT", data, NOW, heuristic()[0])


def test_prediction_storage_shadow_decision_and_restart_idempotency(tmp_path):
    first, data = service(tmp_path)
    inserted = first.process({"BTCUSDT": data}, heuristic(), NOW)
    assert inserted[0]["shadow_decision"] == "SHADOW_BUY"
    restarted = MLShadowService(first.storage, model=first.model, metadata=first.metadata, now=lambda: NOW)
    assert restarted.process({"BTCUSDT": data}, heuristic(), NOW) == []
    report = shadow_report(first.storage.path)
    assert report["metrics"]["prediction_count"] == 1
    assert report["latest"][0]["heuristic_decision"] == "BUY"
    with sqlite3.connect(first.storage.path) as connection:
        assert connection.execute("SELECT inserted_predictions FROM ml_shadow_cycles").fetchone()[0] == 1


def test_outcome_resolution_waits_for_future_closed_candle(tmp_path):
    obj, data = service(tmp_path)
    obj.process({"BTCUSDT": data}, heuristic(), NOW)
    original = shadow_report(obj.storage.path)["latest"][0]
    assert not obj.resolve({"BTCUSDT": data}, NOW + timedelta(hours=3, minutes=59))
    future_time = NOW + timedelta(hours=4)
    future_data = candles(future_time)
    resolved = obj.resolve({"BTCUSDT": future_data}, future_time)
    assert len(resolved) == 1 and resolved[0]["estimated_cost"] == pytest.approx(.0018)
    assert resolved[0]["future_close"] > 0
    assert not obj.resolve({"BTCUSDT": future_data}, future_time)
    final = shadow_report(obj.storage.path)["latest"][0]
    for key in ("probability_positive", "heuristic_score", "heuristic_decision", "feature_hash"):
        assert final[key] == original[key]


def test_next_shadow_cycle_resolves_due_outcomes_automatically(tmp_path):
    obj, data = service(tmp_path)
    obj.process({"BTCUSDT": data}, heuristic(), NOW)
    future_time = NOW + timedelta(hours=4)
    obj.process({"BTCUSDT": candles(future_time)}, heuristic(), future_time)
    metrics = shadow_report(obj.storage.path, now=lambda: future_time)["metrics"]
    assert metrics["resolved_count"] == 1
    assert metrics["unresolved_count"] == 1


def resolved_row(probability, actual_label, actual_return, ml="SHADOW_BUY", heuristic_decision="PASS",
                 symbol="BTCUSDT", hour=0):
    stamp = NOW + timedelta(hours=hour)
    return {"experiment_id": EXPERIMENT_ID, "candle_close_timestamp": stamp.isoformat(),
            "target_timestamp": (stamp + timedelta(hours=4)).isoformat(), "symbol": symbol,
            "resolved_at": (stamp + timedelta(hours=4)).isoformat(),
            "actual_label": actual_label, "probability_positive": probability,
            "shadow_decision": ml, "heuristic_decision": heuristic_decision,
            "actual_net_return": actual_return}


def test_metrics_require_enough_resolved_samples():
    row = {"resolved_at": NOW.isoformat(), "actual_label": 1, "probability_positive": .8,
           "shadow_decision": "SHADOW_BUY", "actual_net_return": .01}
    assert forward_metrics([row] * 5)["status"] == "Not enough data"
    metrics = forward_metrics(([row] * 10) + [{**row, "actual_label": 0,
                                               "probability_positive": .2,
                                               "shadow_decision": "SHADOW_PASS"}] * 10)
    assert metrics["status"] == "Preliminary" and metrics["roc_auc"] == 1
    assert forward_metrics([row] * 50)["status"] == "Early forward sample"
    assert forward_metrics([row] * 200)["status"] == "Larger forward sample"


def test_comparison_disagreements_probability_buckets_and_symbols():
    rows = [resolved_row(.75, 1, .02, "SHADOW_BUY", "BUY", "BTCUSDT", 0),
            resolved_row(.55, 0, -.01, "SHADOW_BUY", "PASS", "ETHUSDT", 1),
            resolved_row(.45, 1, .01, "SHADOW_PASS", "BUY", "SOLUSDT", 2),
            resolved_row(.35, 0, -.02, "SHADOW_PASS", "PASS", "BTCUSDT", 3)]
    categories = comparison_categories(rows)
    assert all(values["count"] == 1 for values in categories.values())
    assert {row["disagreement"] for row in disagreement_rows(rows)} == {
        "A_ML_BUY_HEURISTIC_PASS", "B_ML_PASS_HEURISTIC_BUY"}
    buckets = {row["bucket"]: row for row in probability_buckets(rows)}
    assert buckets["0.70-1.00"]["actual_positive_rate"] == 1
    assert buckets["0.50-0.60"]["average_actual_net_return"] == pytest.approx(-.01)
    symbols = per_symbol_metrics(rows)
    assert symbols["BTCUSDT"]["resolved_count"] == 2
    assert symbols["ETHUSDT"]["status"] == "Not enough data"


def test_theoretical_curve_uses_fixed_notional_and_cost_adjusted_return():
    rows = [resolved_row(.8, 1, .0082, hour=0),
            resolved_row(.8, 0, -.0118, hour=1)]
    curve = theoretical_curve(rows, "shadow_decision", "SHADOW_BUY", "ML")
    assert curve["fixed_notional_per_signal"] == 100
    assert curve["pnl"] == pytest.approx(-.36)
    assert curve["ending_equity"] == pytest.approx(999.64)
    assert curve["trade_count"] == 2 and curve["max_drawdown"] > 0


def test_experiment_metadata_export_and_quality_warnings(tmp_path):
    obj, data = service(tmp_path)
    obj.process({"BTCUSDT": data}, heuristic(), NOW)
    report = shadow_report(obj.storage.path, now=lambda: NOW + timedelta(hours=6))
    assert report["experiment"]["experiment_id"] == EXPERIMENT_ID
    assert report["experiment"]["model_version"] == "ml_v1_market"
    assert report["experiment"]["round_trip_cost"] == pytest.approx(.0018)
    exported = export_forward(obj.storage.path, tmp_path / "forward")
    assert set(exported["files"]) == {"summary.json", "summary.md", "predictions.csv",
                                         "disagreements.csv", "probability_buckets.csv", "equity_curve.csv"}
    quality = _quality(
        [resolved_row(.7, 1, .01, hour=0) | {"resolved_at": None,
                                            "target_timestamp": (NOW - timedelta(hours=2)).isoformat()},
         resolved_row(.7, 1, .01, hour=3)],
        [{"slot": 1, "status": "FAILED", "error_category": "feature_schema"}],
        [{"slot": 2, "stale_scan": True, "errors": []}], NOW)
    assert quality["missing_inference_cycles"] == 1
    assert quality["feature_schema_failures"] == 1
    assert quality["unresolved_older_than_expected"] == 1
    assert quality["timestamp_gaps"] == 1
    assert quality["warnings"]


def test_model_failure_fallback_does_not_change_batch3_results(tmp_path):
    class Failure:
        load_error = None
        def process(self, *args):
            raise RuntimeError("inference failed")
    market_a, market_b = Market(), Market()
    control = AIRunner(AIStorage(tmp_path / "control.sqlite3"), market_a, Collector(),
                       paper=True, now=lambda: market_a.now, shadow=False)
    shadow = AIRunner(AIStorage(tmp_path / "shadow.sqlite3"), market_b, Collector(),
                      paper=True, now=lambda: market_b.now, shadow=Failure())
    control_scan, shadow_scan = control.run_once(), shadow.run_once()
    assert control_scan["rankings"] == shadow_scan["rankings"]
    assert control.storage.accounts() == shadow.storage.accounts()
    with control.storage._connection() as left, shadow.storage._connection() as right:
        left_trades = list(left.execute("SELECT account,payload FROM ai_trades ORDER BY account"))
        right_trades = list(right.execute("SELECT account,payload FROM ai_trades ORDER BY account"))
    # IDs/timestamps are incidental; prices, side, quantity and portfolio state are equal.
    def material(rows):
        return [(account, {key: json.loads(payload)[key] for key in ("symbol", "side", "status", "price", "quantity")})
                for account, payload in rows]
    assert material(left_trades) == material(right_trades)


def test_successful_ml_output_cannot_change_batch3_results(tmp_path):
    market_a, market_b = Market(), Market()
    control_storage = AIStorage(tmp_path / "control_success.sqlite3")
    shadow_storage = AIStorage(tmp_path / "shadow_success.sqlite3")
    row = build_live_feature_row(candles(), NOW)
    names = market_feature_names(row)
    observer = MLShadowService(
        shadow_storage, model=FakeModel(names, probability=.99),
        metadata={"model_version": "ml_v1_market", "feature_version": FEATURE_VERSION,
                  "feature_names": names, "decision_threshold": .5}, now=lambda: NOW)
    control = AIRunner(control_storage, market_a, Collector(), paper=True,
                       now=lambda: market_a.now, shadow=False)
    shadow = AIRunner(shadow_storage, market_b, Collector(), paper=True,
                      now=lambda: market_b.now, shadow=observer)
    first, second = control.run_once(), shadow.run_once()
    assert first["rankings"] == second["rankings"]
    assert control_storage.accounts() == shadow_storage.accounts()
    assert shadow_report(shadow_storage.path)["metrics"]["positive_prediction_count"] == 1


def test_storage_schema_and_model_load_failure_are_safe(tmp_path):
    storage = AIStorage(tmp_path / "state.sqlite3")
    missing = MLShadowService(storage, registry=tmp_path / "missing")
    assert not missing.available and "FileNotFoundError" in missing.load_error
    with sqlite3.connect(storage.path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(ml_shadow_predictions)")}
    assert {"feature_hash", "actual_net_return", "heuristic_decision", "prediction_correct"} <= columns


def test_schema_failure_alert_requires_repetition_and_is_deduplicated(tmp_path):
    storage = AIStorage(tmp_path / "alerts.sqlite3")
    for _ in range(2):
        storage.record_shadow_failure("feature_schema", "missing feature", NOW.isoformat())
    assert not storage.pending_notifications()
    storage.record_shadow_failure("feature_schema", "missing feature", NOW.isoformat())
    storage.record_shadow_failure("feature_schema", "missing feature", NOW.isoformat())
    assert len(storage.pending_notifications()) == 1


def test_shadow_loads_valid_registry_model(tmp_path):
    row = build_live_feature_row(candles(), NOW)
    names = market_feature_names(row)
    training = pd.concat([row] * 20, ignore_index=True)
    training[names] = training[names].fillna(0)
    for index, name in enumerate(names):
        training[name] += np.arange(len(training)) * (index + 1) * 1e-8
    medians = {name: float(training[name].median()) for name in names}
    model = HistGradientBoostingDecisionModel(names, medians,
                                               {"max_iter": 2, "min_samples_leaf": 2}, seed=7)
    model.fit(training, np.arange(len(training)) % 2)
    registry = tmp_path / "registry"
    model_path = model.save(registry / "model.joblib")
    metadata = {"model_version": "ml_v1_market", "feature_version": FEATURE_VERSION,
                "feature_names": names, "decision_threshold": .5,
                "model_sha256": file_hash(model_path)}
    (registry / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    loaded = MLShadowService(AIStorage(tmp_path / "load.sqlite3"), registry=registry)
    assert loaded.available and loaded.model.feature_names() == names


def test_shadow_dashboard_is_read_only_and_prominent(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    obj, data = service(tmp_path)
    obj.process({"BTCUSDT": data}, heuristic(), NOW)
    before = obj.storage.path.read_bytes()
    monkeypatch.setenv("AI_SCANNER_DB", str(obj.storage.path))
    page = AppTest.from_string(
        "import streamlit as st\nfrom app.dashboard.ai_pages import ml_shadow_page\nml_shadow_page(st)"
    ).run(timeout=30)
    assert not page.exception
    assert any("does not control paper trades" in item.value.lower() for item in page.warning)
    assert before == obj.storage.path.read_bytes()
