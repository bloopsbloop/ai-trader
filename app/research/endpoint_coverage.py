"""M17.2R endpoint coverage audit from saved M17.2 ledgers only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SOURCE_NAMESPACE = "m17_2_risk_calibrated_benchmark_v1"
OUTPUT_NAMESPACE = "m17_2_endpoint_coverage_v1"
EVALUATION_WINDOWS = ("validation", "test", "fold_1", "fold_2", "fold_3")
BLOCK_DURATION = pd.Timedelta(days=28)
REPETITIONS = 5000
SEED = 42


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_snapshot(directory: Path) -> dict[str, dict[str, Any]]:
    return {path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(directory.iterdir()) if path.is_file()}


def prepare_output(root: Path, output_dir: Path | None = None) -> Path:
    target = (output_dir or root / "data/research" / OUTPUT_NAMESPACE).resolve()
    if target.exists():
        raise FileExistsError(f"refusing to overwrite review artifacts: {target}")
    target.mkdir(parents=True, exist_ok=False)
    return target


def _validate_equity(equity: pd.DataFrame) -> pd.DataFrame:
    required = {"time", "equity"}
    if not required.issubset(equity.columns):
        raise ValueError("equity ledger is missing time/equity")
    out = equity.copy()
    out["time"] = pd.to_datetime(out.time, utc=True)
    out = out.sort_values("time").reset_index(drop=True)
    if out.time.duplicated().any() or not out.time.is_monotonic_increasing:
        raise ValueError("equity marks must be unique and ordered")
    values = out.equity.to_numpy(dtype=float)
    if len(out) < 2 or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("equity marks must contain at least two finite positive values")
    return out


def equity_increments(equity: pd.DataFrame) -> pd.DataFrame:
    frame = _validate_equity(equity)
    result = pd.DataFrame({
        "interval_start": frame.time.iloc[:-1].to_numpy(),
        "interval_end": frame.time.iloc[1:].to_numpy(),
        "simple_return": frame.equity.to_numpy(dtype=float)[1:] / frame.equity.to_numpy(dtype=float)[:-1] - 1,
    })
    result["duration_hours"] = ((result.interval_end - result.interval_start) / pd.Timedelta(hours=1)).astype(float)
    if (result.duration_hours <= 0).any():
        raise ValueError("equity intervals must have positive duration")
    return result


def coverage_record(equity: pd.DataFrame, fills: pd.DataFrame | None = None) -> dict[str, Any]:
    frame = _validate_equity(equity)
    midnight = frame[(frame.time.dt.hour == 0) & (frame.time.dt.minute == 0) & (frame.time.dt.second == 0)]
    if midnight.empty:
        raise ValueError("no UTC-midnight equity marks")
    coverage_start, coverage_end = midnight.time.iloc[0], midnight.time.iloc[-1]
    daily = midnight.set_index("time").equity.pct_change().iloc[1:]
    if len(daily) and not (daily.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all():
        raise ValueError("full-day coverage is not continuous")
    path_start, path_end = frame.time.iloc[0], frame.time.iloc[-1]
    initial_hours = float((coverage_start - path_start) / pd.Timedelta(hours=1))
    terminal_hours = float((path_end - coverage_end) / pd.Timedelta(hours=1))
    initial_factor = float(midnight.equity.iloc[0] / frame.equity.iloc[0]) if initial_hours else 1.0
    terminal_factor = float(frame.equity.iloc[-1] / midnight.equity.iloc[-1]) if terminal_hours else 1.0
    full_day_factor = float(np.prod(1 + daily.to_numpy(dtype=float))) if len(daily) else 1.0
    total_factor = float(frame.equity.iloc[-1] / frame.equity.iloc[0])
    reconciled = initial_factor * full_day_factor * terminal_factor
    fill_frame = pd.DataFrame() if fills is None else fills.copy()
    if not fill_frame.empty:
        fill_frame["fill_time"] = pd.to_datetime(fill_frame.fill_time, utc=True)
        fill_frame["cost"] = fill_frame.commission.astype(float) + fill_frame.slippage_cost.astype(float)
    initial_mask = (pd.Series(False, index=fill_frame.index) if fill_frame.empty or not initial_hours else
                    (fill_frame.fill_time >= path_start) & (fill_frame.fill_time < coverage_start))
    terminal_mask = (pd.Series(False, index=fill_frame.index) if fill_frame.empty or not terminal_hours else
                     (fill_frame.fill_time >= coverage_end) & (fill_frame.fill_time <= path_end))
    if (initial_mask & terminal_mask).any():
        raise ValueError("a fill cannot belong to both boundary intervals")
    return {
        "path_start": path_start, "path_end": path_end,
        "daily_coverage_start_mark": coverage_start, "daily_coverage_end_mark": coverage_end,
        "first_daily_return_end": daily.index[0] if len(daily) else pd.NaT,
        "last_daily_return_end": daily.index[-1] if len(daily) else pd.NaT,
        "full_daily_intervals": len(daily),
        "initial_partial_hours": initial_hours, "terminal_partial_hours": terminal_hours,
        "initial_boundary_return": initial_factor - 1,
        "terminal_boundary_return": terminal_factor - 1,
        "full_day_compounded_return": full_day_factor - 1,
        "full_window_return": total_factor - 1,
        "initial_boundary_costs": float(fill_frame.loc[initial_mask, "cost"].sum()) if not fill_frame.empty else 0.0,
        "terminal_boundary_costs": float(fill_frame.loc[terminal_mask, "cost"].sum()) if not fill_frame.empty else 0.0,
        "initial_boundary_fill_count": int(initial_mask.sum()),
        "terminal_boundary_fill_count": int(terminal_mask.sum()),
        "reconciled_factor": reconciled,
        "reconciliation_error": reconciled - total_factor,
    }


def paired_four_hour(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    linc, rinc = equity_increments(left), equity_increments(right)
    if not linc[["interval_start", "interval_end"]].equals(rinc[["interval_start", "interval_end"]]):
        raise ValueError("paired 4h returns require exactly matching interval coverage")
    if not np.allclose(linc.duration_hours, 4.0):
        raise ValueError("4h sensitivity requires actual consecutive 4h equity intervals")
    result = linc.rename(columns={"simple_return": "strategy_return"})
    result["passive_return"] = rinc.simple_return.to_numpy(dtype=float)
    result["difference"] = result.strategy_return - result.passive_return
    return result


def calendar_blocks(paired: pd.DataFrame, duration: pd.Timedelta = BLOCK_DURATION) -> pd.DataFrame:
    if paired.empty:
        raise ValueError("cannot block an empty paired series")
    frame = paired.copy().reset_index(drop=True)
    anchor = frame.interval_start.iloc[0]
    ids = ((frame.interval_start - anchor) // duration).astype(int)
    rows = []
    for block_id, group in frame.groupby(ids, sort=True):
        nominal_start = anchor + block_id * duration
        nominal_end = nominal_start + duration
        rows.append({"block": int(block_id), "nominal_start": nominal_start, "nominal_end": nominal_end,
                     "first_interval_start": group.interval_start.iloc[0],
                     "last_interval_end": group.interval_end.iloc[-1],
                     "calendar_span_hours": float((group.interval_end.iloc[-1] - group.interval_start.iloc[0]) / pd.Timedelta(hours=1)),
                     "sum_difference": float(group.difference.sum()), "count": len(group),
                     "complete_28d_block": bool(group.interval_start.iloc[0] == nominal_start and
                                                group.interval_end.iloc[-1] == nominal_end)})
    return pd.DataFrame(rows)


def block_bootstrap(blocks: pd.DataFrame, repetitions: int = REPETITIONS, seed: int = SEED) -> dict[str, float]:
    sums = blocks.sum_difference.to_numpy(dtype=float)
    counts = blocks["count"].to_numpy(dtype=int)
    if len(sums) == 0 or (counts <= 0).any():
        raise ValueError("bootstrap blocks require observations")
    rng = np.random.default_rng(seed)
    samples = np.empty(repetitions)
    for index in range(repetitions):
        selected = rng.integers(0, len(sums), size=len(sums))
        samples[index] = sums[selected].sum() / counts[selected].sum()
    point = float(sums.sum() / counts.sum())
    return {"mean_4h_difference": point, "linear_daily_x6": point * 6,
            "ci_lower_4h": float(np.quantile(samples, 0.025)),
            "ci_upper_4h": float(np.quantile(samples, 0.975)),
            "ci_lower_linear_daily_x6": float(np.quantile(samples, 0.025) * 6),
            "ci_upper_linear_daily_x6": float(np.quantile(samples, 0.975) * 6),
            "bootstrap_positive_fraction": float((samples > 0).mean())}


def _json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def run(root: Path | None = None, output_dir: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    source = root / "data/research" / SOURCE_NAMESPACE
    expected = json.loads((source / "artifact_manifest.json").read_text(encoding="utf-8"))
    if any(sha256(source / name) != digest for name, digest in expected.items()):
        raise ValueError("M17.2 source artifact hash mismatch before audit")
    before = file_snapshot(source)
    out = prepare_output(root, output_dir)
    try:
        equity = pd.read_csv(source / "portfolio_equity.csv", parse_dates=["time", "start", "end"])
        fills = pd.read_csv(source / "fills.csv", parse_dates=["fill_time", "time", "start", "end"])
        saved_daily = pd.read_csv(source / "daily_returns.csv", parse_dates=["date", "start", "end"])
        calibration = pd.read_csv(source / "calibration.csv", parse_dates=["calibration_start", "calibration_end", "daily_start", "daily_end"])
        frozen_effects = pd.read_csv(source / "paired_effects.csv", parse_dates=["daily_start", "daily_end"])
        coverage_rows, boundary_fill_rows = [], []
        four_hour_rows, block_rows, sensitivity_rows = [], [], []
        keys = ["window", "scenario", "policy"]
        evaluation = equity[equity.window.isin(EVALUATION_WINDOWS)]
        for key, group in evaluation.groupby(keys, sort=False):
            window, scenario, policy = key
            group_fills = fills[(fills.window == window) & (fills.scenario == scenario) & (fills.policy == policy)]
            record = coverage_record(group, group_fills)
            daily_group = saved_daily[(saved_daily.window == window) & (saved_daily.scenario == scenario) &
                                      (saved_daily.policy == policy)]
            if len(daily_group) != record["full_daily_intervals"] or (len(daily_group) and
                    (daily_group.date.iloc[0] != record["first_daily_return_end"] or
                     daily_group.date.iloc[-1] != record["last_daily_return_end"])):
                raise ValueError(f"saved daily-return coverage mismatch: {key}")
            coverage_rows.append({"window": window, "scenario": scenario, "policy": policy, **record})
            if record["initial_partial_hours"]:
                selected = group_fills[(group_fills.fill_time >= record["path_start"]) &
                                       (group_fills.fill_time < record["daily_coverage_start_mark"])]
                boundary_fill_rows.extend({"window": window, "scenario": scenario, "policy": policy,
                                           "boundary": "initial", **row}
                                          for row in selected.to_dict("records"))
            if record["terminal_partial_hours"]:
                selected = group_fills[(group_fills.fill_time >= record["daily_coverage_end_mark"]) &
                                       (group_fills.fill_time <= record["path_end"])]
                boundary_fill_rows.extend({"window": window, "scenario": scenario, "policy": policy,
                                           "boundary": "terminal", **row}
                                          for row in selected.to_dict("records"))

        for window in EVALUATION_WINDOWS:
            for scenario in ("base", "double"):
                strategy = evaluation[(evaluation.window == window) & (evaluation.scenario == scenario) &
                                      (evaluation.policy == "strategy")]
                passive = evaluation[(evaluation.window == window) & (evaluation.scenario == scenario) &
                                     (evaluation.policy == "buyhold_calibrated")]
                paired = paired_four_hour(strategy, passive)
                blocks = calendar_blocks(paired)
                tags = {"window": window, "scenario": scenario}
                four_hour_rows.extend({**tags, **row} for row in paired.to_dict("records"))
                block_rows.extend({**tags, **row} for row in blocks.to_dict("records"))
                sensitivity_rows.append({**tags, "observations": len(paired), "block_count": len(blocks),
                                         "block_calendar_days": 28, "repetitions": REPETITIONS, "seed": SEED,
                                         "coverage_start": paired.interval_start.iloc[0],
                                         "coverage_end": paired.interval_end.iloc[-1],
                                         **block_bootstrap(blocks)})

        calibration_rows = []
        for row in calibration.to_dict("records"):
            start, end = row["calibration_start"], row["calibration_end"] + pd.Timedelta(hours=4)
            coverage_start = start.ceil("D")
            coverage_end = end.floor("D")
            calibration_rows.append({**row, "equity_path_start": start, "equity_path_end_expected": end,
                                     "daily_coverage_start_mark": coverage_start,
                                     "daily_coverage_end_mark": coverage_end,
                                     "first_daily_return_end": row["daily_start"],
                                     "last_daily_return_end": row["daily_end"],
                                     "initial_partial_hours": float((coverage_start - start) / pd.Timedelta(hours=1)),
                                     "terminal_partial_hours": float((end - coverage_end) / pd.Timedelta(hours=1)),
                                     "note": "Coverage reconstructed from saved calibration metadata; calibration equity ledgers were not persisted."})

        coverage_frame = pd.DataFrame(coverage_rows)
        if coverage_frame.reconciliation_error.abs().max() > 1e-12:
            raise ValueError("endpoint reconciliation failed")
        primary = frozen_effects[(frozen_effects.window == "test") & (frozen_effects.scenario == "base") &
                                 (frozen_effects.block_days == 28)].iloc[0].to_dict()
        test_sensitivity = next(row for row in sensitivity_rows if row["window"] == "test" and row["scenario"] == "base")
        summary = {
            "version": "m17.2r-endpoint-coverage-v1", "status": "WEAK",
            "source_namespace": SOURCE_NAMESPACE,
            "source_protocol_sha256": sha256(root / "docs/M17_2_PROTOCOL.json"),
            "source_artifacts_unchanged": True,
            "lambda_unchanged": True, "fills_unchanged": True, "total_pnl_unchanged": True,
            "frozen_primary_full_utc_days": primary,
            "post_review_all_4h_sensitivity": test_sensitivity,
            "scope": "The frozen primary covers complete UTC-midnight intervals only. Full net return covers the entire equity path. The all-4h calculation is post-review sensitivity and cannot raise status.",
        }
        coverage_frame.to_csv(out / "coverage.csv", index=False)
        pd.DataFrame(boundary_fill_rows).to_csv(out / "boundary_fills.csv", index=False)
        pd.DataFrame(calibration_rows).to_csv(out / "calibration_coverage.csv", index=False)
        pd.DataFrame(four_hour_rows).to_csv(out / "four_hour_returns.csv", index=False)
        pd.DataFrame(block_rows).to_csv(out / "four_hour_blocks.csv", index=False)
        pd.DataFrame(sensitivity_rows).to_csv(out / "four_hour_sensitivity.csv", index=False)
        pd.DataFrame([primary]).to_csv(out / "frozen_primary.csv", index=False)
        _json_write(out / "source_hashes_before.json", before)
        after = file_snapshot(source)
        _json_write(out / "source_hashes_after.json", after)
        if before != after:
            raise ValueError("M17.2 source artifacts changed during audit")
        _json_write(out / "summary.json", summary)
        manifest = {path.name: sha256(path) for path in sorted(out.iterdir()) if path.is_file()}
        _json_write(out / "artifact_manifest.json", manifest)
        return summary
    except Exception:
        for path in sorted(out.iterdir(), reverse=True):
            if path.is_file():
                path.unlink()
        out.rmdir()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit M17.2 daily endpoint coverage from saved ledgers")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = run(args.root, args.output_dir)
    print(json.dumps({"status": result["status"], "output": OUTPUT_NAMESPACE}, indent=2))


if __name__ == "__main__":
    main()
