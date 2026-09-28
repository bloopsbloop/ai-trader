"""Forward-only ML shadow inference. Predictions never reach PaperBroker."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, brier_score_loss,
                             precision_score, recall_score, roc_auc_score)

from app.ai.market import INTERVALS
from app.ai.news import utc
from app.ai.storage import DEFAULT_PATH
from app.ml.features import FEATURE_VERSION, candle_features, merge_multitimeframe
from app.ml.model import HistGradientBoostingDecisionModel
from app.ml.train import DEFAULT_REGISTRY, file_hash

MODEL_VERSION = "ml_v1_market"
BASE_TIMEFRAME = "1h"
HORIZON_HOURS = 4
COMMISSION = .0004
SLIPPAGE = .0005
MINIMUM_EDGE = .001
MINIMUM_METRIC_SAMPLES = 20


def _candle_frame(candles, timeframe, cutoff):
    seconds = INTERVALS[timeframe]
    cutoff = utc(cutoff)
    closed = [candle for candle in candles
              if utc(candle.open_time) + timedelta(seconds=seconds) <= cutoff]
    if not closed:
        raise ValueError(f"No closed {timeframe} candles")
    return pd.DataFrame([asdict(candle) for candle in closed])


def build_live_feature_row(candles_by_timeframe, cutoff):
    """Use the training feature functions and backward joins on closed bars only."""
    if set(candles_by_timeframe) != set(INTERVALS):
        raise ValueError("15m, 1h and 4h candles are required")
    calculated = {timeframe: candle_features(_candle_frame(candles, timeframe, cutoff), timeframe)
                  for timeframe, candles in candles_by_timeframe.items()}
    merged = merge_multitimeframe(calculated, BASE_TIMEFRAME)
    cutoff_stamp = pd.Timestamp(utc(cutoff))
    eligible = merged[pd.to_datetime(merged["timestamp"], utc=True) <= cutoff_stamp]
    if eligible.empty:
        raise ValueError("No closed base-timeframe feature row")
    row = eligible.iloc[[-1]].copy()
    timestamp = pd.Timestamp(row.iloc[0]["timestamp"])
    for timeframe in INTERVALS:
        source = pd.Timestamp(row.iloc[0][f"source_timestamp_{timeframe}"])
        if source > timestamp:
            raise ValueError(f"Future {timeframe} candle alignment")
    return row


class MLShadowService:
    def __init__(self, storage, registry=DEFAULT_REGISTRY, model=None, metadata=None,
                 now=lambda: datetime.now(timezone.utc)):
        self.storage = storage
        self.registry = Path(registry)
        self.now = now
        self.model = model
        self.metadata = metadata
        self.load_error = None
        if self.model is None:
            self._load()
        else:
            self._validate_loaded()

    def _load(self):
        try:
            metadata_path = self.registry / "metadata.json"
            model_path = self.registry / "model.joblib"
            self.metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if self.metadata.get("model_sha256") and file_hash(model_path) != self.metadata["model_sha256"]:
                raise ValueError("Model hash does not match registry metadata")
            self.model = HistGradientBoostingDecisionModel.load(model_path)
            self._validate_loaded()
        except Exception as exc:
            self.model = None
            self.load_error = f"{type(exc).__name__}: {exc}"
            logging.exception("ML shadow model load failed")

    def _validate_loaded(self):
        if self.metadata.get("model_version") != MODEL_VERSION:
            raise ValueError("Unexpected model version")
        if self.metadata.get("feature_version") != FEATURE_VERSION:
            raise ValueError("Feature version mismatch")
        expected = list(self.metadata.get("feature_names", []))
        if not expected or expected != self.model.feature_names():
            raise ValueError("Model feature name/order mismatch")
        medians = getattr(self.model, "medians", {})
        if set(expected) - set(medians):
            raise ValueError("Missing trained NaN-imputation medians")
        if not 0 < float(self.metadata.get("decision_threshold", 0)) < 1:
            raise ValueError("Invalid decision threshold")

    @property
    def available(self):
        return self.model is not None and self.load_error is None

    def _prediction(self, symbol, candles, cutoff, heuristic):
        row = build_live_feature_row(candles, cutoff)
        expected = self.model.feature_names()
        missing = [name for name in expected if name not in row]
        if missing:
            raise ValueError(f"Feature schema mismatch: {missing}")
        matrix = self.model._matrix(row)
        if matrix.shape != (1, len(expected)) or not np.isfinite(matrix).all():
            raise ValueError("Invalid prepared feature vector")
        feature_hash = hashlib.sha256(matrix.astype("<f8", copy=False).tobytes()).hexdigest()
        probability = float(self.model.predict_proba(row)[0])
        if not np.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid model probability")
        threshold = float(self.metadata["decision_threshold"])
        candle_close = pd.Timestamp(row.iloc[0]["timestamp"]).to_pydatetime()
        return {"candle_close_timestamp": utc(candle_close).isoformat(), "symbol": symbol,
                "base_timeframe": BASE_TIMEFRAME, "model_version": self.metadata["model_version"],
                "feature_version": self.metadata["feature_version"],
                "probability_positive": probability, "decision_threshold": threshold,
                "shadow_decision": "SHADOW_BUY" if probability >= threshold else "SHADOW_PASS",
                "market_price": float(row.iloc[0]["close"]), "scoring_mode": "ml_shadow",
                "feature_hash": feature_hash, "created_at": utc(self.now()).isoformat(),
                "heuristic_score": None if heuristic is None else float(heuristic["score"]),
                "heuristic_decision": None if heuristic is None else heuristic["decision"],
                "target_timestamp": utc(candle_close + timedelta(hours=HORIZON_HOURS)).isoformat()}

    def resolve(self, candles_by_symbol, cutoff):
        resolved = []
        unresolved = self.storage.unresolved_shadow_predictions(set(candles_by_symbol))
        for prediction in unresolved:
            candles = _candle_frame(candles_by_symbol[prediction["symbol"]][BASE_TIMEFRAME], BASE_TIMEFRAME, cutoff)
            candles["close_timestamp"] = pd.to_datetime(candles["open_time"], utc=True) + pd.Timedelta(hours=1)
            target = pd.Timestamp(prediction["target_timestamp"])
            outcome = candles[candles["close_timestamp"] == target]
            if outcome.empty:
                continue
            gross = float(outcome.iloc[-1]["close"]) / float(prediction["market_price"]) - 1
            cost = 2 * (COMMISSION + SLIPPAGE)
            net = gross - cost
            label = int(net > MINIMUM_EDGE)
            predicted = int(prediction["probability_positive"] >= prediction["decision_threshold"])
            values = {"resolved_at": utc(cutoff).isoformat(), "gross_future_return": gross,
                      "estimated_cost": cost, "actual_net_return": net, "actual_label": label,
                      "prediction_correct": int(predicted == label)}
            if self.storage.resolve_shadow_prediction(prediction["id"], values):
                resolved.append({"id": prediction["id"], **values})
        return resolved

    def process(self, candles_by_symbol, heuristic_rows, cutoff):
        if not self.available:
            raise RuntimeError(self.load_error or "ML shadow subsystem disabled")
        self.resolve(candles_by_symbol, cutoff)
        heuristic = {row["symbol"]: row for row in heuristic_rows}
        inserted = []
        for symbol in sorted(heuristic):
            if symbol not in candles_by_symbol:
                continue
            row = self._prediction(symbol, candles_by_symbol[symbol], cutoff, heuristic[symbol])
            if self.storage.store_shadow_prediction(row):
                inserted.append(row)
        return inserted


def forward_metrics(rows, minimum_samples=MINIMUM_METRIC_SAMPLES):
    total = len(rows)
    resolved = [row for row in rows if row.get("resolved_at")]
    result = {"prediction_count": total, "resolved_count": len(resolved),
              "unresolved_count": total - len(resolved),
              "positive_prediction_count": sum(row["shadow_decision"] == "SHADOW_BUY" for row in rows),
              "theoretical_trade_count": sum(row["shadow_decision"] == "SHADOW_BUY" for row in resolved)}
    if len(resolved) < minimum_samples:
        result.update(status="Not enough data", minimum_resolved=minimum_samples)
        return result
    labels = np.asarray([row["actual_label"] for row in resolved], dtype=int)
    probabilities = np.asarray([row["probability_positive"] for row in resolved], dtype=float)
    predictions = np.asarray([row["shadow_decision"] == "SHADOW_BUY" for row in resolved], dtype=int)
    buys = [row for row in resolved if row["shadow_decision"] == "SHADOW_BUY"]
    result.update(status="Ready",
                  precision=float(precision_score(labels, predictions, zero_division=0)),
                  recall=float(recall_score(labels, predictions, zero_division=0)),
                  brier=float(brier_score_loss(labels, probabilities)),
                  roc_auc=float(roc_auc_score(labels, probabilities)) if len(set(labels)) == 2 else None,
                  pr_auc=float(average_precision_score(labels, probabilities)) if len(set(labels)) == 2 else None,
                  average_actual_net_return_after_shadow_buy=(float(np.mean([r["actual_net_return"] for r in buys])) if buys else None),
                  hit_rate=(sum(r["actual_net_return"] > 0 for r in buys) / len(buys) if buys else None))
    return result


def shadow_report(path=DEFAULT_PATH, latest=20):
    path = Path(path)
    if not path.exists():
        return {"model_version": MODEL_VERSION, "mode": "SHADOW / NO TRADE CONTROL",
                "metrics": forward_metrics([]), "latest_prediction_time": None, "latest": []}
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ml_shadow_predictions'").fetchone()
        rows = [] if not exists else [dict(row) for row in connection.execute(
            "SELECT * FROM ml_shadow_predictions ORDER BY candle_close_timestamp DESC,symbol")]
    finally:
        connection.close()
    return {"model_version": MODEL_VERSION, "mode": "SHADOW / NO TRADE CONTROL",
            "metrics": forward_metrics(rows),
            "latest_prediction_time": rows[0]["candle_close_timestamp"] if rows else None,
            "latest": rows[:latest]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect ML shadow predictions; read-only")
    parser.add_argument("command", choices=("status", "report"))
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    report = shadow_report(args.db, latest=20 if args.command == "report" else 1)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
