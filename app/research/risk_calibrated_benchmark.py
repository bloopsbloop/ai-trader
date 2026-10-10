"""M17.2 risk-calibrated passive benchmark research (local, immutable output)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.research.absolute_trend import (
    BAR,
    INITIAL_CAPITAL,
    SYMBOLS,
    SleeveResult,
    _records,
    aggregate,
    block_summaries,
    buy_and_hold,
    daily_returns,
    drawdown_metrics,
    load_history,
    metrics,
    paired_block_bootstrap,
    simulate_sleeve,
    validate_history,
)

NAMESPACE = "m17_2_risk_calibrated_benchmark_v1"
PROTOCOL = "M17_2_PROTOCOL.json"
SCENARIOS = {"base": (0.0004, 0.0005), "double": (0.0008, 0.001)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_output(root: Path, output_dir: Path | None = None) -> Path:
    target = (output_dir or root / "data/research" / NAMESPACE).resolve()
    if target.exists():
        raise FileExistsError(f"refusing to overwrite research artifacts: {target}")
    target.mkdir(parents=True, exist_ok=False)
    return target


def load_frames(root: Path, protocol: dict[str, Any]) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    start, end = map(pd.Timestamp, protocol["data_period"])
    frames: dict[str, pd.DataFrame] = {}
    audits: list[dict[str, Any]] = []
    for symbol in protocol["universe"]:
        path = root / "data/history" / f"{symbol}_4h.csv"
        actual_hash = sha256(path)
        if actual_hash != protocol["data_hashes"][symbol]:
            raise ValueError(f"frozen history hash mismatch: {symbol}")
        frame = load_history(path)
        frame = frame[(frame.open_time >= start) & (frame.open_time <= end)].reset_index(drop=True)
        audit = validate_history(frame, symbol)
        audit.update(symbol=symbol, sha256=actual_hash)
        audits.append(audit)
        frames[symbol] = frame
    common = frames[protocol["universe"][0]].open_time
    if any(not frame.open_time.equals(common) for frame in frames.values()):
        raise ValueError("M17.2 requires a synchronized common 4h grid")
    return frames, audits


def run_strategy(frames: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp,
                 commission: float, slippage: float) -> tuple[list[SleeveResult], pd.DataFrame]:
    cash = INITIAL_CAPITAL / len(SYMBOLS)
    results = [simulate_sleeve(frames[s], s, cash, commission, slippage, start, end) for s in SYMBOLS]
    return results, aggregate(results)


def _cash_equity(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                 capital: float = INITIAL_CAPITAL) -> pd.DataFrame:
    selected = frame[(frame.open_time >= start) & (frame.open_time <= end)]
    if selected.empty or selected.open_time.iloc[0] != start or selected.open_time.iloc[-1] != end:
        raise ValueError("cash policy requires exact 4h boundaries")
    times = [start, *list(selected.open_time + BAR)]
    if len(times) != len(set(times)):
        raise ValueError("duplicate cash equity marks")
    out = pd.DataFrame({"time": times, "equity": capital, "position_value": 0.0,
                        "cash": capital, "exposure": 0.0})
    out["equity_mark_time"] = out.time
    return out


def run_passive(frames: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp,
                invested_fraction: float, commission: float, slippage: float
                ) -> tuple[list[SleeveResult], pd.DataFrame]:
    if not np.isfinite(invested_fraction) or not 0 <= invested_fraction <= 1:
        raise ValueError("invested_fraction must be finite and within [0, 1]")
    common = frames[SYMBOLS[0]].open_time
    if start not in common.array or end not in common.array:
        raise ValueError("passive policy requires exact 4h boundaries")
    if invested_fraction == 0:
        return [], _cash_equity(frames[SYMBOLS[0]], start, end)
    sleeve_cash = INITIAL_CAPITAL * invested_fraction / len(SYMBOLS)
    results = [buy_and_hold(frames[s], s, sleeve_cash, start, commission, slippage, end) for s in SYMBOLS]
    equity = aggregate(results).copy()
    reserve = INITIAL_CAPITAL * (1 - invested_fraction)
    equity["equity"] += reserve
    equity["cash"] = equity.equity - equity.position_value
    equity["exposure"] = equity.position_value / equity.equity.replace(0, np.nan)
    return results, equity


def volatility_fraction(sigma_strategy: float, sigma_buyhold: float) -> float:
    """Return the frozen bounded risk fraction or stop on undefined calibration."""
    if not np.isfinite(sigma_buyhold) or sigma_buyhold <= 0 or not np.isfinite(sigma_strategy):
        raise RuntimeError("METHODOLOGY_BLOCKED: invalid buy-and-hold or strategy volatility")
    return float(np.clip(sigma_strategy / sigma_buyhold, 0.0, 1.0))


def calibrate_lambda(frames: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp
                     ) -> tuple[float, dict[str, Any]]:
    strategy_results, strategy_equity = run_strategy(frames, start, end, *SCENARIOS["base"])
    bh_results, bh_equity = run_passive(frames, start, end, 1.0, *SCENARIOS["base"])
    strategy_daily, bh_daily = daily_returns(strategy_equity), daily_returns(bh_equity)
    if not strategy_daily.index.equals(bh_daily.index):
        raise ValueError("calibration daily returns are not exactly aligned")
    sigma_strategy = float(strategy_daily.std(ddof=1))
    sigma_buyhold = float(bh_daily.std(ddof=1))
    value = volatility_fraction(sigma_strategy, sigma_buyhold)
    return value, {
        "calibration_start": start, "calibration_end": end,
        "daily_start": strategy_daily.index[0], "daily_end": strategy_daily.index[-1],
        "daily_observations": len(strategy_daily), "sigma_strategy": sigma_strategy,
        "sigma_buyhold": sigma_buyhold, "lambda": value,
        "strategy_ending_equity": float(strategy_equity.equity.iloc[-1]),
        "buyhold_ending_equity": float(bh_equity.equity.iloc[-1]),
        "strategy_trades": sum(len(r.trades) for r in strategy_results),
        "buyhold_trades": sum(len(r.trades) for r in bh_results),
    }


def policy_metrics(equity: pd.DataFrame, results: list[SleeveResult]) -> dict[str, Any]:
    if results:
        out = metrics(equity, results, INITIAL_CAPITAL)
    else:
        daily = daily_returns(equity)
        dd, dd_bars = drawdown_metrics(equity)
        days = (equity.time.iloc[-1] - equity.time.iloc[0]).total_seconds() / 86400
        out = {"ending_equity": float(equity.equity.iloc[-1]), "net_return": 0.0,
               "annualized_return": 0.0 if days else 0.0, "mean_daily_return": float(daily.mean()),
               "trades": 0, "expectancy": 0.0, "profit_factor": 0.0, "time_in_market": 0.0,
               "average_exposure": 0.0, "max_exposure": 0.0, "turnover": 0.0,
               "turnover_ratio": 0.0, "mean_holding_hours": 0.0, "median_holding_hours": 0.0,
               "max_drawdown": dd, "max_drawdown_bars": dd_bars, "commission": 0.0,
               "slippage_cost": 0.0}
    daily = daily_returns(equity)
    out.update(realized_daily_volatility=float(daily.std(ddof=1)),
               annualized_volatility=float(daily.std(ddof=1) * np.sqrt(365)),
               max_drawdown_days=float(out["max_drawdown_bars"] * 4 / 24),
               total_costs=float(out["commission"] + out["slippage_cost"]),
               vs_cash=float(out["ending_equity"] - INITIAL_CAPITAL))
    return out


def _tag(rows: list[dict[str, Any]], **tags: Any) -> list[dict[str, Any]]:
    return [{**tags, **row} for row in rows]


def _result_rows(results: list[SleeveResult], attribute: str, **tags: Any) -> list[dict[str, Any]]:
    return _tag([row for result in results for row in getattr(result, attribute)], **tags)


def _equity_rows(equity: pd.DataFrame, **tags: Any) -> list[dict[str, Any]]:
    ledger = equity.copy()
    if "cash" not in ledger:
        ledger["cash"] = ledger.equity - ledger.position_value
    return _tag(ledger[["time", "equity", "position_value", "cash", "exposure"]].to_dict("records"), **tags)


def _symbol_contributions(results: list[SleeveResult], initial_per_sleeve: float,
                          **tags: Any) -> list[dict[str, Any]]:
    rows = []
    for symbol, result in zip(SYMBOLS, results):
        rows.append({**tags, "symbol": symbol,
                     "initial_capital": initial_per_sleeve, "ending_equity": result.state.cash,
                     "contribution": result.state.cash - initial_per_sleeve,
                     "turnover": result.state.turnover, "commission": result.state.commission,
                     "slippage_cost": result.state.slippage, "trades": len(result.trades)})
    return rows


def _artifact_manifest(directory: Path) -> dict[str, str]:
    return {path.name: sha256(path) for path in sorted(directory.iterdir()) if path.is_file() and path.name != "artifact_manifest.json"}


def run(root: Path | None = None, output_dir: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    protocol_path = root / "docs" / PROTOCOL
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    out = prepare_output(root, output_dir)
    try:
        frames, audits = load_frames(root, protocol)
        main_start, main_end = map(pd.Timestamp, protocol["main"]["calibration"])
        main_lambda, main_cal = calibrate_lambda(frames, main_start, main_end)
        calibrations = [{"calibration_id": "main_train", **main_cal}]
        lambdas = {"validation": main_lambda, "test": main_lambda}
        windows = [
            {"window": "validation", "kind": "main", "start": protocol["main"]["validation"][0], "end": protocol["main"]["validation"][1]},
            {"window": "test", "kind": "main", "start": protocol["main"]["test"][0], "end": protocol["main"]["test"][1]},
        ]
        for fold in protocol["walk_forward"]:
            cal_start, cal_end = map(pd.Timestamp, fold["calibration"])
            value, record = calibrate_lambda(frames, cal_start, cal_end)
            name = f"fold_{fold['fold']}"
            calibrations.append({"calibration_id": name, **record})
            lambdas[name] = value
            windows.append({"window": name, "kind": "walk_forward", "start": fold["evaluation"][0], "end": fold["evaluation"][1]})

        metric_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        signal_rows: list[dict[str, Any]] = []
        fill_rows: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []
        contribution_rows: list[dict[str, Any]] = []
        bootstrap_rows: list[dict[str, Any]] = []
        block_rows: list[dict[str, Any]] = []
        run_cache: dict[tuple[str, str, str], tuple[list[SleeveResult], pd.DataFrame]] = {}

        for window in windows:
            name = window["window"]
            start, end = pd.Timestamp(window["start"]), pd.Timestamp(window["end"])
            value = lambdas[name]
            for scenario, costs in SCENARIOS.items():
                policies = {
                    "strategy": run_strategy(frames, start, end, *costs),
                    "buyhold_full": run_passive(frames, start, end, 1.0, *costs),
                    "buyhold_calibrated": run_passive(frames, start, end, value, *costs),
                    "cash": run_passive(frames, start, end, 0.0, *costs),
                }
                for policy, (results, equity) in policies.items():
                    run_cache[(name, scenario, policy)] = results, equity
                    tags = {"window": name, "window_kind": window["kind"], "scenario": scenario,
                            "policy": policy, "start": start, "end": end, "lambda": value}
                    metric_rows.append({**tags, **policy_metrics(equity, results)})
                    daily = daily_returns(equity)
                    daily_rows.extend({**tags, "date": date, "daily_net_return": ret}
                                      for date, ret in daily.items())
                    equity_rows.extend(_equity_rows(equity, **tags))
                    signal_rows.extend(_result_rows(results, "signals", **tags))
                    fill_rows.extend(_result_rows(results, "fills", **tags))
                    trade_rows.extend(_result_rows(results, "trades", **tags))
                    allocated = INITIAL_CAPITAL / len(SYMBOLS) if policy != "buyhold_calibrated" else INITIAL_CAPITAL * value / len(SYMBOLS)
                    contribution_rows.extend(_symbol_contributions(results, allocated, **tags))
                strategy_daily = daily_returns(policies["strategy"][1])
                passive_daily = daily_returns(policies["buyhold_calibrated"][1])
                for days in (28, 14, 56):
                    effect = paired_block_bootstrap(strategy_daily, passive_daily, days, 5000, 42)
                    bootstrap_rows.append({"window": name, "window_kind": window["kind"], "scenario": scenario,
                                           "block_days": days, "repetitions": 5000, "seed": 42,
                                           "daily_start": strategy_daily.index[0], "daily_end": strategy_daily.index[-1],
                                           "observations": len(strategy_daily), **effect})
                    block_rows.extend({"window": name, "window_kind": window["kind"], "scenario": scenario,
                                       "block_days": days, **row}
                                      for row in block_summaries(strategy_daily, passive_daily, days))

        # Preserve a separate continuous path. It is descriptive and not part of the primary decision.
        history_start = pd.Timestamp(protocol["strategy"]["economic_start"])
        history_end = pd.Timestamp(protocol["data_period"][1])
        for scenario, costs in SCENARIOS.items():
            for policy, (results, equity) in {
                "strategy_continuous": run_strategy(frames, history_start, history_end, *costs),
                "buyhold_full_descriptive": run_passive(frames, history_start, history_end, 1.0, *costs),
                "buyhold_main_lambda_descriptive": run_passive(frames, history_start, history_end, main_lambda, *costs),
                "cash_descriptive": run_passive(frames, history_start, history_end, 0.0, *costs),
            }.items():
                tags = {"window": "full_history_descriptive", "window_kind": "descriptive", "scenario": scenario,
                        "policy": policy, "start": history_start, "end": history_end, "lambda": main_lambda}
                metric_rows.append({**tags, **policy_metrics(equity, results)})
                equity_rows.extend(_equity_rows(equity, **tags))

        metrics_frame = pd.DataFrame(metric_rows)
        effects_frame = pd.DataFrame(bootstrap_rows)
        def metric(window: str, scenario: str, policy: str, column: str) -> float:
            row = metrics_frame[(metrics_frame.window == window) & (metrics_frame.scenario == scenario) &
                                (metrics_frame.policy == policy)]
            return float(row.iloc[0][column])
        primary = effects_frame[(effects_frame.window == "test") & (effects_frame.scenario == "base") &
                                (effects_frame.block_days == 28)].iloc[0]
        fold_positive = sum(float(effects_frame[(effects_frame.window == f"fold_{i}") &
                                                (effects_frame.scenario == "base") &
                                                (effects_frame.block_days == 28)].iloc[0].mean_difference) > 0
                            for i in range(1, 4))
        criteria = {
            "primary_28d_ci_lower_positive": float(primary.ci_lower) > 0,
            "strategy_test_return_positive": metric("test", "base", "strategy", "net_return") > 0,
            "at_least_two_fold_effects_positive": fold_positive >= 2,
            "double_test_effect_positive": float(effects_frame[(effects_frame.window == "test") &
                                                                  (effects_frame.scenario == "double") &
                                                                  (effects_frame.block_days == 28)].iloc[0].mean_difference) > 0,
            "double_strategy_test_return_positive": metric("test", "double", "strategy", "net_return") > 0,
            "strategy_test_vol_not_higher": metric("test", "base", "strategy", "realized_daily_volatility") <= metric("test", "base", "buyhold_calibrated", "realized_daily_volatility"),
            "strategy_test_drawdown_not_worse": abs(metric("test", "base", "strategy", "max_drawdown")) <= abs(metric("test", "base", "buyhold_calibrated", "max_drawdown")),
        }
        any_positive = (float(primary.mean_difference) > 0 or fold_positive > 0 or
                        criteria["double_test_effect_positive"])
        status = ("PROMISING_INCREMENTAL_EVIDENCE" if all(criteria.values()) else
                  "WEAK" if any_positive else "NO_INCREMENTAL_EVIDENCE")

        _records(out / "calibration.csv", calibrations)
        metrics_frame.to_csv(out / "window_metrics.csv", index=False)
        effects_frame.to_csv(out / "paired_effects.csv", index=False)
        _records(out / "bootstrap_blocks.csv", block_rows)
        _records(out / "signals.csv", signal_rows)
        _records(out / "fills.csv", fill_rows)
        _records(out / "trades.csv", trade_rows)
        _records(out / "portfolio_equity.csv", equity_rows)
        _records(out / "daily_returns.csv", daily_rows)
        _records(out / "symbol_contributions.csv", contribution_rows)
        pd.DataFrame(audits).to_csv(out / "data_audit.csv", index=False)
        (out / "protocol.json").write_bytes(protocol_path.read_bytes())
        source_hashes = {
            "docs/M17_2_PROTOCOL.json": sha256(protocol_path),
            "docs/M17_1_PROTOCOL.json": sha256(root / "docs/M17_1_PROTOCOL.json"),
            "docs/M17_1R_ADDENDUM.md": sha256(root / "docs/M17_1R_ADDENDUM.md"),
            "docs/M16_2_PROTOCOL.json": sha256(root / "docs/M16_2_PROTOCOL.json"),
            "data/ml/reports/m16_2_native_ltr_v1/walk_forward.csv": sha256(root / "data/ml/reports/m16_2_native_ltr_v1/walk_forward.csv"),
            **{f"data/history/{symbol}_4h.csv": protocol["data_hashes"][symbol] for symbol in SYMBOLS},
        }
        (out / "source_hashes.json").write_text(json.dumps(source_hashes, indent=2), encoding="utf-8")
        summary = {
            "version": protocol["version"], "status": status, "protocol_sha256": sha256(protocol_path),
            "source_commit": protocol["source_commit"], "runtime_note": "recorded by verification report",
            "main_lambda": main_lambda, "calibrations": calibrations,
            "primary": {key: (value.item() if isinstance(value, np.generic) else value) for key, value in primary.to_dict().items()},
            "fold_positive_point_effects": fold_positive, "criteria": criteria,
            "reused_period_warning": "Validation, Test and walk-forward windows are reused research periods, not untouched holdouts.",
            "full_history_warning": "Full-history rows are descriptive; main-Train lambda is non-causal before calibration end.",
            "window_metrics": metric_rows, "paired_effects": bootstrap_rows,
        }
        (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        manifest = _artifact_manifest(out)
        (out / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return summary
    except Exception:
        # A failed run must not reserve the immutable namespace with partial evidence.
        for path in sorted(out.glob("*"), reverse=True):
            if path.is_file():
                path.unlink()
        out.rmdir()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M17.2 risk-calibrated passive benchmark research")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    summary = run(args.root, args.output_dir)
    print(json.dumps({"status": summary["status"], "main_lambda": summary["main_lambda"],
                      "output": NAMESPACE}, indent=2))


if __name__ == "__main__":
    main()
