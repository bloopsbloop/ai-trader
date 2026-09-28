"""Forward-only ML shadow inference. Predictions never reach PaperBroker."""
from __future__ import annotations

import argparse
import csv
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

from app.ai.market import INTERVALS, PublicScannerData
from app.ai.news import utc
from app.ai.storage import AIStorage, DEFAULT_PATH
from app.ml.features import FEATURE_VERSION, candle_features, merge_multitimeframe
from app.ml.model import HistGradientBoostingDecisionModel
from app.ml.dataset import PROJECT_ROOT
from app.ml.train import DEFAULT_REGISTRY, file_hash

MODEL_VERSION = "ml_v1_market"
BASE_TIMEFRAME = "1h"
HORIZON_HOURS = 4
COMMISSION = .0004
SLIPPAGE = .0005
MINIMUM_EDGE = .001
MINIMUM_METRIC_SAMPLES = 20
EXPERIMENT_ID = "ml_v1_market_forward_001"
EXPERIMENT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
THEORETICAL_STARTING_CAPITAL = 1000.0
THEORETICAL_NOTIONAL = 100.0


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
        self.experiment = self._register_experiment() if self.available else None

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

    def _register_experiment(self):
        payload = {"experiment_id": EXPERIMENT_ID,
                   "model_hash": self.metadata.get("model_sha256", "unregistered-test-model"),
                   "model_version": self.metadata["model_version"],
                   "feature_version": self.metadata["feature_version"],
                   "threshold": float(self.metadata["decision_threshold"]),
                   "start_timestamp": utc(self.now()).isoformat(),
                   "symbols": list(EXPERIMENT_SYMBOLS),
                   "timeframes": list(INTERVALS), "base_timeframe": BASE_TIMEFRAME,
                   "target_horizon_hours": HORIZON_HOURS,
                   "commission": COMMISSION, "slippage": SLIPPAGE,
                   "round_trip_cost": 2 * (COMMISSION + SLIPPAGE),
                   "theoretical_starting_capital": THEORETICAL_STARTING_CAPITAL,
                   "theoretical_notional_per_signal": THEORETICAL_NOTIONAL}
        return self.storage.ensure_shadow_experiment(EXPERIMENT_ID, payload)

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
        return {"experiment_id": EXPERIMENT_ID,
                "candle_close_timestamp": utc(candle_close).isoformat(), "symbol": symbol,
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
        unresolved = self.storage.unresolved_shadow_predictions(set(candles_by_symbol), EXPERIMENT_ID)
        for prediction in unresolved:
            candles = _candle_frame(candles_by_symbol[prediction["symbol"]][BASE_TIMEFRAME], BASE_TIMEFRAME, cutoff)
            candles["close_timestamp"] = pd.to_datetime(candles["open_time"], utc=True) + pd.Timedelta(hours=1)
            target = pd.Timestamp(prediction["target_timestamp"])
            outcome = candles[candles["close_timestamp"] == target]
            if outcome.empty:
                continue
            future_close = float(outcome.iloc[-1]["close"])
            gross = future_close / float(prediction["market_price"]) - 1
            cost = 2 * (COMMISSION + SLIPPAGE)
            net = gross - cost
            label = int(net > MINIMUM_EDGE)
            predicted = int(prediction["probability_positive"] >= prediction["decision_threshold"])
            values = {"resolved_at": utc(cutoff).isoformat(), "future_close": future_close,
                      "gross_future_return": gross,
                      "estimated_cost": cost, "actual_net_return": net, "actual_label": label,
                      "prediction_correct": int(predicted == label)}
            if self.storage.resolve_shadow_prediction(prediction["id"], values):
                resolved.append({"id": prediction["id"], **values})
        return resolved

    def process(self, candles_by_symbol, heuristic_rows, cutoff):
        heuristic = {row["symbol"]: row for row in heuristic_rows
                     if row["symbol"] in EXPERIMENT_SYMBOLS}
        cycle = {"experiment_id": EXPERIMENT_ID,
                 "slot": int(utc(cutoff).timestamp()) // 900,
                 "cutoff": utc(cutoff).isoformat(), "status": "SUCCESS",
                 "eligible_symbols": len(heuristic), "inserted_predictions": 0,
                 "error_category": None, "error": None,
                 "created_at": utc(self.now()).isoformat()}
        try:
            if not self.available:
                raise RuntimeError(self.load_error or "ML shadow subsystem disabled")
            self.resolve(candles_by_symbol, cutoff)
            inserted = []
            for symbol in sorted(heuristic):
                if symbol not in candles_by_symbol:
                    continue
                row = self._prediction(symbol, candles_by_symbol[symbol], cutoff, heuristic[symbol])
                if self.storage.store_shadow_prediction(row):
                    inserted.append(row)
            cycle["inserted_predictions"] = len(inserted)
            self.storage.record_shadow_cycle(cycle)
            return inserted
        except Exception as exc:
            cycle.update(status="FAILED", error_category=("model_load" if self.load_error else
                         "feature_schema" if isinstance(exc, ValueError) else "inference"),
                         error=f"{type(exc).__name__}: {exc}")
            self.storage.record_shadow_cycle(cycle)
            raise


def sample_status(count):
    if count < 20:
        return "Not enough data"
    if count < 50:
        return "Preliminary"
    if count < 200:
        return "Early forward sample"
    return "Larger forward sample"


def theoretical_curve(rows, decision_field, buy_value, name,
                      starting_capital=THEORETICAL_STARTING_CAPITAL,
                      notional=THEORETICAL_NOTIONAL):
    trades = sorted((row for row in rows if row.get("resolved_at") and row.get(decision_field) == buy_value),
                    key=lambda row: (row.get("candle_close_timestamp", ""), row.get("symbol", "")))
    equity = float(starting_capital)
    peak = equity
    points = [{"strategy": name, "timestamp": None, "symbol": None, "equity": equity,
               "pnl": 0.0, "drawdown": 0.0}]
    pnl_values = []
    for row in trades:
        pnl = notional * float(row["actual_net_return"])
        equity += pnl
        peak = max(peak, equity)
        pnl_values.append(pnl)
        points.append({"strategy": name, "timestamp": row.get("target_timestamp"),
                       "symbol": row.get("symbol"), "equity": equity, "pnl": pnl,
                       "drawdown": equity / peak - 1})
    wins = sum(value for value in pnl_values if value > 0)
    losses = sum(value for value in pnl_values if value < 0)
    return {"strategy": name, "label": "THEORETICAL SHADOW RESULT",
            "starting_capital": starting_capital, "fixed_notional_per_signal": notional,
            "costs": "actual_net_return already subtracts 0.18% round-trip commission+slippage",
            "trade_count": len(trades), "pnl": equity - starting_capital,
            "net_return": equity / starting_capital - 1, "ending_equity": equity,
            "profit_factor": wins / abs(losses) if losses else (None if not wins else None),
            "max_drawdown": abs(min((point["drawdown"] for point in points), default=0.0)),
            "points": points}


def forward_metrics(rows, minimum_samples=MINIMUM_METRIC_SAMPLES):
    total = len(rows)
    resolved = [row for row in rows if row.get("resolved_at")]
    status = sample_status(len(resolved))
    result = {"status": status, "prediction_count": total, "resolved_count": len(resolved),
              "unresolved_count": total - len(resolved),
              "positive_prediction_count": sum(row["shadow_decision"] == "SHADOW_BUY" for row in rows),
              "shadow_buy_count": sum(row["shadow_decision"] == "SHADOW_BUY" for row in rows),
              "shadow_pass_count": sum(row["shadow_decision"] == "SHADOW_PASS" for row in rows),
              "actual_positive_labels": sum(row.get("actual_label") == 1 for row in resolved),
              "theoretical_trade_count": sum(row["shadow_decision"] == "SHADOW_BUY" for row in resolved)}
    if len(resolved) < minimum_samples:
        result.update(minimum_resolved=minimum_samples, precision=None, recall=None, f1=None,
                      roc_auc=None, pr_auc=None, brier=None,
                      average_actual_net_return_after_shadow_buy=None,
                      median_actual_net_return_after_shadow_buy=None, hit_rate=None,
                      cumulative_theoretical_pnl=None, profit_factor=None, max_drawdown=None)
        return result
    labels = np.asarray([row["actual_label"] for row in resolved], dtype=int)
    probabilities = np.asarray([row["probability_positive"] for row in resolved], dtype=float)
    predictions = np.asarray([row["shadow_decision"] == "SHADOW_BUY" for row in resolved], dtype=int)
    buys = [row for row in resolved if row["shadow_decision"] == "SHADOW_BUY"]
    precision = float(precision_score(labels, predictions, zero_division=0))
    recall = float(recall_score(labels, predictions, zero_division=0))
    curve = theoretical_curve(resolved, "shadow_decision", "SHADOW_BUY", "ML shadow")
    result.update(precision=precision,
                  recall=recall,
                  f1=(2 * precision * recall / (precision + recall) if precision + recall else 0.0),
                  brier=float(brier_score_loss(labels, probabilities)),
                  roc_auc=float(roc_auc_score(labels, probabilities)) if len(set(labels)) == 2 else None,
                  pr_auc=float(average_precision_score(labels, probabilities)) if len(set(labels)) == 2 else None,
                  average_actual_net_return_after_shadow_buy=(float(np.mean([r["actual_net_return"] for r in buys])) if buys else None),
                  median_actual_net_return_after_shadow_buy=(float(np.median([r["actual_net_return"] for r in buys])) if buys else None),
                  hit_rate=(sum(r["actual_net_return"] > 0 for r in buys) / len(buys) if buys else None),
                  cumulative_theoretical_pnl=curve["pnl"], profit_factor=curve["profit_factor"],
                  max_drawdown=curve["max_drawdown"])
    return result


def comparison_categories(rows):
    definitions = (("ml_buy_heuristic_buy", "SHADOW_BUY", "BUY"),
                   ("ml_buy_heuristic_pass", "SHADOW_BUY", "PASS"),
                   ("ml_pass_heuristic_buy", "SHADOW_PASS", "BUY"),
                   ("ml_pass_heuristic_pass", "SHADOW_PASS", "PASS"))
    result = {}
    resolved = [row for row in rows if row.get("resolved_at")]
    for name, ml, heuristic in definitions:
        sample = [row for row in resolved if row["shadow_decision"] == ml and
                  ("BUY" if row.get("heuristic_decision") == "BUY" else "PASS") == heuristic]
        returns = [float(row["actual_net_return"]) for row in sample]
        result[name] = {"count": len(sample),
                        "positive_label_rate": (sum(row["actual_label"] == 1 for row in sample) / len(sample) if sample else None),
                        "average_actual_net_return": (float(np.mean(returns)) if returns else None),
                        "median_actual_net_return": (float(np.median(returns)) if returns else None)}
    return result


def disagreement_rows(rows):
    return [{**row, "disagreement": ("A_ML_BUY_HEURISTIC_PASS" if row["shadow_decision"] == "SHADOW_BUY"
                                     else "B_ML_PASS_HEURISTIC_BUY")}
            for row in rows if row.get("resolved_at") and
            ((row["shadow_decision"] == "SHADOW_BUY" and row.get("heuristic_decision") != "BUY") or
             (row["shadow_decision"] == "SHADOW_PASS" and row.get("heuristic_decision") == "BUY"))]


def probability_buckets(rows):
    buckets = (("0.00-0.40", 0, .4, False), ("0.40-0.50", .4, .5, False),
               ("0.50-0.60", .5, .6, False), ("0.60-0.70", .6, .7, False),
               ("0.70-1.00", .7, 1.0, True))
    resolved = [row for row in rows if row.get("resolved_at")]
    result = []
    for label, low, high, inclusive in buckets:
        sample = [row for row in resolved if low <= row["probability_positive"] and
                  (row["probability_positive"] <= high if inclusive else row["probability_positive"] < high)]
        returns = [float(row["actual_net_return"]) for row in sample]
        result.append({"bucket": label, "count": len(sample),
                       "mean_predicted_probability": (float(np.mean([r["probability_positive"] for r in sample])) if sample else None),
                       "actual_positive_rate": (sum(row["actual_label"] == 1 for row in sample) / len(sample) if sample else None),
                       "average_actual_net_return": (float(np.mean(returns)) if returns else None)})
    return result


def per_symbol_metrics(rows):
    symbols = sorted(set(EXPERIMENT_SYMBOLS) | {row["symbol"] for row in rows})
    return {symbol: forward_metrics([row for row in rows if row["symbol"] == symbol]) for symbol in symbols}


def _quality(rows, cycles, scans, now):
    now = pd.Timestamp(utc(now))
    cycle_slots = {row["slot"] for row in cycles}
    missing_cycles = sum(scan.get("slot") not in cycle_slots for scan in scans)
    stale = sum(bool(scan.get("stale_scan")) or any(error.get("stage") == "features" for error in scan.get("errors", []))
                for scan in scans)
    failed = [row for row in cycles if row["status"] == "FAILED"]
    old_unresolved = sum(not row.get("resolved_at") and now > pd.Timestamp(row["target_timestamp"]) + pd.Timedelta(hours=1)
                         for row in rows)
    gaps = 0
    for symbol in {row["symbol"] for row in rows}:
        stamps = sorted(pd.Timestamp(row["candle_close_timestamp"]) for row in rows if row["symbol"] == symbol)
        gaps += sum((later - earlier) > pd.Timedelta(hours=1) for earlier, later in zip(stamps, stamps[1:]))
    warnings = []
    if missing_cycles:
        warnings.append(f"{missing_cycles} Batch 3 scan cycle(s) have no ML shadow cycle record")
    if failed:
        warnings.append(f"{len(failed)} ML shadow cycle(s) failed")
    if stale:
        warnings.append(f"{stale} scan cycle(s) reported stale/missing candle input")
    if old_unresolved:
        warnings.append(f"{old_unresolved} outcome(s) are unresolved more than one hour after target close")
    if gaps:
        warnings.append(f"{gaps} per-symbol hourly prediction timestamp gap(s) detected")
    return {"missing_inference_cycles": missing_cycles,
            "feature_schema_failures": sum(row.get("error_category") == "feature_schema" for row in failed),
            "model_load_failures": sum(row.get("error_category") == "model_load" for row in failed),
            "inference_failures": sum(row.get("error_category") == "inference" for row in failed),
            "stale_or_missing_candle_cycles": stale,
            "unresolved_older_than_expected": old_unresolved,
            "timestamp_gaps": gaps, "warnings": warnings}


def shadow_report(path=DEFAULT_PATH, latest=20, now=lambda: datetime.now(timezone.utc)):
    path = Path(path)
    if not path.exists():
        ml_curve = theoretical_curve([], "shadow_decision", "SHADOW_BUY", "ML shadow")
        heuristic_curve = theoretical_curve([], "heuristic_decision", "BUY", "Heuristic shadow")
        cash = {"strategy": "Cash baseline", "label": "THEORETICAL SHADOW RESULT",
                "starting_capital": THEORETICAL_STARTING_CAPITAL,
                "ending_equity": THEORETICAL_STARTING_CAPITAL, "pnl": 0.0, "net_return": 0.0,
                "trade_count": 0, "profit_factor": None, "max_drawdown": 0.0,
                "points": [{"strategy": "Cash baseline", "timestamp": None, "symbol": None,
                            "equity": THEORETICAL_STARTING_CAPITAL, "pnl": 0.0, "drawdown": 0.0}]}
        return {"experiment": None, "model_version": MODEL_VERSION,
                "mode": "SHADOW / NO TRADE CONTROL", "metrics": forward_metrics([]),
                "comparison": comparison_categories([]), "disagreements": [],
                "probability_buckets": probability_buckets([]), "per_symbol": per_symbol_metrics([]),
                "theoretical": {"ml": ml_curve, "heuristic": heuristic_curve, "cash": cash},
                "data_quality": {"warnings": ["Shadow database does not exist"]},
                "latest_prediction_time": None, "latest": []}
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        exists = "ml_shadow_predictions" in tables
        rows = [] if not exists else [dict(row) for row in connection.execute(
            "SELECT * FROM ml_shadow_predictions ORDER BY candle_close_timestamp DESC,symbol")]
        for row in rows:
            row.setdefault("experiment_id", EXPERIMENT_ID)
            row.setdefault("future_close", None)
        experiment_row = (connection.execute("SELECT payload FROM ml_shadow_experiments WHERE experiment_id=?",
                                             (EXPERIMENT_ID,)).fetchone()
                          if "ml_shadow_experiments" in tables else None)
        experiment = json.loads(experiment_row[0]) if experiment_row else None
        cycles = ([dict(row) for row in connection.execute(
            "SELECT * FROM ml_shadow_cycles WHERE experiment_id=? ORDER BY slot", (EXPERIMENT_ID,))]
                  if "ml_shadow_cycles" in tables else [])
        scans = []
        if "ai_scans" in tables:
            for payload, in connection.execute("SELECT payload FROM ai_scans ORDER BY slot"):
                scan = json.loads(payload)
                if not experiment or scan.get("cutoff", "") >= experiment["start_timestamp"]:
                    scans.append(scan)
    finally:
        connection.close()
    ml_curve = theoretical_curve(rows, "shadow_decision", "SHADOW_BUY", "ML shadow")
    heuristic_curve = theoretical_curve(rows, "heuristic_decision", "BUY", "Heuristic shadow")
    cash = {"strategy": "Cash baseline", "label": "THEORETICAL SHADOW RESULT",
            "starting_capital": THEORETICAL_STARTING_CAPITAL,
            "ending_equity": THEORETICAL_STARTING_CAPITAL, "pnl": 0.0, "net_return": 0.0,
            "trade_count": 0, "profit_factor": None, "max_drawdown": 0.0,
            "points": [{"strategy": "Cash baseline", "timestamp": None, "symbol": None,
                        "equity": THEORETICAL_STARTING_CAPITAL, "pnl": 0.0, "drawdown": 0.0}]}
    return {"experiment": experiment, "model_version": MODEL_VERSION,
            "mode": "SHADOW / NO TRADE CONTROL", "metrics": forward_metrics(rows),
            "comparison": comparison_categories(rows),
            "disagreements": disagreement_rows(rows),
            "probability_buckets": probability_buckets(rows),
            "per_symbol": per_symbol_metrics(rows),
            "theoretical": {"ml": ml_curve, "heuristic": heuristic_curve, "cash": cash},
            "data_quality": _quality(rows, cycles, scans, now()),
            "latest_prediction_time": rows[0]["candle_close_timestamp"] if rows else None,
            "latest": rows[:latest]}


def _write_csv(path, rows):
    path = Path(path)
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8") as target:
        if not rows:
            target.write("")
            return
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def export_forward(path=DEFAULT_PATH, output=PROJECT_ROOT / "data" / "ml" / "forward"):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = shadow_report(path, latest=20)
    summary = {key: value for key, value in report.items() if key not in {"latest", "disagreements"}}
    summary["theoretical"] = {name: {key: value for key, value in curve.items() if key != "points"}
                              for name, curve in report["theoretical"].items()}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    metrics = report["metrics"]
    markdown = ["# ML forward shadow experiment", "",
                f"- Experiment: {(report['experiment'] or {}).get('experiment_id', EXPERIMENT_ID)}",
                "- Mode: ML SHADOW ONLY — does not control paper trades.",
                f"- Sample status: {metrics['status']}",
                f"- Predictions: {metrics['prediction_count']}",
                f"- Resolved / unresolved: {metrics['resolved_count']} / {metrics['unresolved_count']}",
                f"- Theoretical ML PnL: {report['theoretical']['ml']['pnl']:.6f} USDT",
                f"- Theoretical heuristic PnL: {report['theoretical']['heuristic']['pnl']:.6f} USDT", "",
                "Results use fixed 100 USDT notional per resolved signal and already cost-adjusted returns."]
    (output / "summary.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    all_rows = shadow_report(path, latest=10**9)["latest"]
    _write_csv(output / "predictions.csv", all_rows)
    _write_csv(output / "disagreements.csv", report["disagreements"])
    _write_csv(output / "probability_buckets.csv", report["probability_buckets"])
    equity = []
    for curve in report["theoretical"].values():
        equity.extend(curve["points"])
    _write_csv(output / "equity_curve.csv", equity)
    return {"output": str(output.resolve()), "files": sorted(path.name for path in output.iterdir()),
            "metrics": metrics}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect and maintain the ML shadow forward experiment")
    parser.add_argument("command", choices=("status", "report", "resolve", "export"))
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--output", default=str(PROJECT_ROOT / "data" / "ml" / "forward"))
    args = parser.parse_args(argv)
    if args.command == "resolve":
        storage = AIStorage(args.db)
        service = MLShadowService(storage)
        symbols = sorted({row["symbol"] for row in storage.unresolved_shadow_predictions(experiment_id=EXPERIMENT_ID)})
        cutoff = datetime.now(timezone.utc)
        market = PublicScannerData()
        candle_rows = {symbol: {BASE_TIMEFRAME: market.candles(symbol, BASE_TIMEFRAME, cutoff)} for symbol in symbols}
        result = {"resolved": len(service.resolve(candle_rows, cutoff)), "symbols_checked": symbols}
    elif args.command == "export":
        result = export_forward(args.db, args.output)
    else:
        result = shadow_report(args.db, latest=20 if args.command == "report" else 1)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
