"""Classification and trading-oriented evaluation without overlapping trades."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,
                             f1_score, precision_score, recall_score, roc_auc_score)


def classification_metrics(labels, probabilities, threshold):
    labels = np.asarray(labels, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    predictions = probabilities >= threshold
    matrix = confusion_matrix(labels, predictions, labels=[0, 1])
    return {"roc_auc": float(roc_auc_score(labels, probabilities)),
            "pr_auc": float(average_precision_score(labels, probabilities)),
            "precision": float(precision_score(labels, predictions, zero_division=0)),
            "recall": float(recall_score(labels, predictions, zero_division=0)),
            "f1": float(f1_score(labels, predictions, zero_division=0)),
            "brier": float(brier_score_loss(labels, probabilities)),
            "confusion_matrix": matrix.astype(int).tolist(), "threshold": float(threshold),
            "positive_rate": float(predictions.mean()), "rows": len(labels)}


def simulate_trades(frame, probabilities, threshold, starting_balance=1000.0,
                    position_fraction=.10, commission=.0004):
    """One position at a time; entry decision at T, exit at saved T+horizon net return."""
    work = frame[["timestamp", "target_timestamp", "symbol", "future_net_return"]].copy()
    work["probability"] = np.asarray(probabilities, dtype=float)
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    work["target_timestamp"] = pd.to_datetime(work["target_timestamp"], utc=True)
    work = work.sort_values(["timestamp", "probability"], ascending=[True, False])
    equity, peak, available_at = float(starting_balance), float(starting_balance), pd.Timestamp.min.tz_localize("UTC")
    fees = turnover = 0.0
    pnl_values, equity_curve = [], [equity]
    for timestamp, group in work.groupby("timestamp", sort=True):
        if timestamp < available_at:
            continue
        candidate = group[group["probability"] >= threshold].head(1)
        if candidate.empty:
            continue
        row = candidate.iloc[0]
        notional = equity * position_fraction
        pnl = notional * float(row["future_net_return"])
        fee = notional * commission * 2
        equity += pnl
        fees += fee
        turnover += notional * 2
        pnl_values.append(pnl)
        available_at = row["target_timestamp"]
        peak = max(peak, equity)
        equity_curve.append(equity)
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value <= 0]
    drawdowns = [(value / max(equity_curve[:index + 1]) - 1) for index, value in enumerate(equity_curve)]
    return {"net_return": equity / starting_balance - 1, "realized_pnl": equity - starting_balance,
            "trade_count": len(pnl_values), "win_rate": len(wins) / len(pnl_values) if pnl_values else 0.0,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses and sum(losses) else (float("inf") if wins else 0.0),
            "expectancy": float(np.mean(pnl_values)) if pnl_values else 0.0,
            "max_drawdown": abs(min(drawdowns, default=0.0)), "fees": fees,
            "turnover": turnover, "ending_equity": equity}


def select_threshold(frame, probabilities, labels, grid, minimum_trades=100):
    candidates = []
    for threshold in grid:
        classification = classification_metrics(labels, probabilities, threshold)
        trading = simulate_trades(frame, probabilities, threshold)
        eligible = trading["trade_count"] >= minimum_trades
        score = trading["net_return"] - .5 * trading["max_drawdown"] if eligible else -1e9 + trading["trade_count"]
        candidates.append({"threshold": float(threshold), "score": score, "eligible": eligible,
                           "classification": classification, "trading": trading})
    selected = max(candidates, key=lambda item: (item["score"], item["classification"]["precision"], -item["threshold"]))
    return selected, candidates


def buy_hold_baseline(frame, starting_balance=1000.0, total_fraction=.10, commission=.0004, slippage=.0005):
    curves, returns = [], []
    for _, group in frame.sort_values("timestamp").groupby("symbol"):
        normalized = group["close"].to_numpy(dtype=float) / float(group["close"].iloc[0])
        curves.append(pd.Series(normalized, index=pd.to_datetime(group["timestamp"], utc=True)))
        returns.append(normalized[-1] - 1 - 2 * (commission + slippage))
    combined = pd.concat(curves, axis=1).ffill().dropna().mean(axis=1)
    equity = starting_balance * (1 + total_fraction * (combined - 1))
    drawdown = equity / equity.cummax() - 1
    pnl = starting_balance * total_fraction * float(np.mean(returns))
    return {"net_return": pnl / starting_balance, "realized_pnl": pnl, "trade_count": len(returns),
            "win_rate": sum(value > 0 for value in returns) / len(returns),
            "profit_factor": sum(max(value, 0) for value in returns) / abs(sum(min(value, 0) for value in returns)) if any(value < 0 for value in returns) else float("inf"),
            "expectancy": pnl / len(returns), "max_drawdown": abs(float(drawdown.min())),
            "fees": starting_balance * total_fraction * commission * 2,
            "turnover": starting_balance * total_fraction * 2, "ending_equity": starting_balance + pnl,
            "exposure_note": "Equal-weight symbols sharing 10% total capital; one round trip per symbol."}
