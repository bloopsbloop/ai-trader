"""M18.2 acquisition and one-time evaluation of the frozen Donchian policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import time
import traceback
import urllib.request
import zipfile
from datetime import datetime, timezone
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
    daily_returns,
    drawdown_metrics,
    metrics,
    simulate_sleeve,
)
from app.research.risk_calibrated_benchmark import run_passive

PROTOCOL_SHA256 = "0997c5ba7f63351bbf10ab6271aa6d996ce9232dfefd8a792e735d57e1449f5e"
EXECUTION_MANIFEST_SHA256: str | None = None  # resolved and frozen in the code-freeze record
LAMBDA = 0.5923903262274034
START = pd.Timestamp("2026-01-01T00:00:00Z")
END = pd.Timestamp("2026-10-01T00:00:00Z")
LAST_OPEN = END - BAR
WARMUP_START = pd.Timestamp("2025-12-12T00:00:00Z")
WARMUP_END = pd.Timestamp("2025-12-31T20:00:00Z")
MONTHS = tuple(f"2026-{month:02d}" for month in range(1, 10))
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
HOLDOUT_NAMESPACE = "m18_1_2026_jan_sep_v1"
EVALUATION_NAMESPACE = "m18_1_frozen_strategy_evaluation_v1"
SCENARIOS = {"base": (0.0004, 0.0005), "double": (0.0008, 0.001)}
ARCHIVE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                   "quote_asset_volume", "number_of_trades", "taker_buy_base_asset_volume",
                   "taker_buy_quote_asset_volume", "ignore"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def paths(root: Path) -> dict[str, Path]:
    holdout = root / "data/holdouts" / HOLDOUT_NAMESPACE
    return {"holdout": holdout, "raw": holdout / "raw", "normalized": holdout / "normalized",
            "evaluation": root / "data/research" / EVALUATION_NAMESPACE}


def require_absent(path: Path, label: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {label}: {path}")


def verify_frozen_inputs(root: Path) -> dict[str, str]:
    protocol = root / "docs/M18_1_EVALUATION_PROTOCOL.json"
    if sha256(protocol) != PROTOCOL_SHA256:
        raise ValueError("frozen M18.1 protocol hash mismatch")
    frozen = json.loads(protocol.read_text(encoding="utf-8"))
    checks = {
        "docs/M18_1_EVALUATION_PROTOCOL.json": PROTOCOL_SHA256,
        "app/research/absolute_trend.py": frozen["strategy_source"]["sha256"],
        "docs/M17_1_PROTOCOL.json": frozen["strategy_source"]["m17_1_protocol_sha256"],
        "docs/M17_2_PROTOCOL.json": frozen["strategy_source"]["m17_2_protocol_sha256"],
        "data/research/m17_2_risk_calibrated_benchmark_v1/summary.json": frozen["strategy_source"]["m17_2_summary_sha256"],
    }
    for name, expected in checks.items():
        if sha256(root / name) != expected:
            raise ValueError(f"protected input hash mismatch: {name}")
    return checks


def download(url: str, destination: Path, attempts: int = 3) -> dict[str, Any]:
    errors = []
    for attempt in range(1, attempts + 1):
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "AI-Trader-M18.2-research/1"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            temporary.replace(destination)
            return {"url": url, "attempt": attempt, "acquired_at": utc_now(),
                    "bytes": destination.stat().st_size, "sha256": sha256(destination)}
        except Exception as exc:  # network errors are evidence in the acquisition log
            errors.append({"attempt": attempt, "type": type(exc).__name__, "message": str(exc)})
            temporary.unlink(missing_ok=True)
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(json.dumps({"status": "DATA_ACQUISITION_BLOCKED", "url": url, "errors": errors}))


def expected_checksum(checksum_path: Path, archive_name: str) -> str:
    line = checksum_path.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
    if not match or Path(match.group(2)).name != archive_name:
        raise ValueError(f"invalid official checksum file: {checksum_path.name}")
    return match.group(1).lower()


def acquire(root: Path, attempts: int = 3) -> dict[str, Any]:
    verify_frozen_inputs(root)
    target = paths(root)
    require_absent(target["holdout"], "acquisition namespace")
    target["raw"].mkdir(parents=True, exist_ok=False)
    log: dict[str, Any] = {"version": "m18.2-acquisition-v1", "status": "RUNNING",
                           "started_at": utc_now(), "source": BASE_URL, "files": []}
    log_path = target["holdout"] / "acquisition_manifest.json"
    write_json(log_path, log)
    try:
        for symbol in SYMBOLS:
            symbol_dir = target["raw"] / symbol
            symbol_dir.mkdir()
            for month in MONTHS:
                archive_name = f"{symbol}-4h-{month}.zip"
                archive_url = f"{BASE_URL}/{symbol}/4h/{archive_name}"
                checksum_url = archive_url + ".CHECKSUM"
                checksum_path = symbol_dir / f"{archive_name}.CHECKSUM"
                archive_path = symbol_dir / archive_name
                checksum_info = download(checksum_url, checksum_path, attempts)
                archive_info = download(archive_url, archive_path, attempts)
                official = expected_checksum(checksum_path, archive_name)
                verified = archive_info["sha256"] == official
                record = {"symbol": symbol, "month": month, "archive_name": archive_name,
                          "archive": archive_info, "checksum": checksum_info,
                          "official_sha256": official, "checksum_verified": verified}
                log["files"].append(record)
                write_json(log_path, log)
                if not verified:
                    raise RuntimeError(f"DATA_ACQUISITION_BLOCKED: checksum mismatch {archive_name}")
        log.update(status="COMPLETE", completed_at=utc_now(), archive_count=len(log["files"]),
                   checksum_verified_count=sum(x["checksum_verified"] for x in log["files"]))
        write_json(log_path, log)
        return log
    except Exception as exc:
        log.update(status="DATA_ACQUISITION_BLOCKED", stopped_at=utc_now(),
                   error_type=type(exc).__name__, error=str(exc))
        write_json(log_path, log)
        raise


def _read_archive(archive: Path) -> pd.DataFrame:
    with zipfile.ZipFile(archive) as bundle:
        members = [name for name in bundle.namelist() if not name.endswith("/")]
        if len(members) != 1 or not members[0].lower().endswith(".csv"):
            raise ValueError(f"unexpected archive members: {archive.name}")
        with bundle.open(members[0]) as handle:
            frame = pd.read_csv(handle, header=None)
    if frame.shape[1] != 12:
        raise ValueError(f"{archive.name}: expected 12 fields, got {frame.shape[1]}")
    frame.columns = ARCHIVE_COLUMNS
    return frame


def validate_archive_frame(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    out = frame.copy()
    for column in ARCHIVE_COLUMNS:
        out[column] = pd.to_numeric(out[column], errors="raise")
    for column in ("open_time", "close_time", "number_of_trades"):
        values = out[column].to_numpy(dtype=float)
        if not np.isfinite(values).all() or not np.equal(values, np.floor(values)).all():
            raise ValueError(f"{label}: {column} must contain finite integers")
        out[column] = out[column].astype("int64")
    if not out.open_time.between(1_000_000_000_000_000, 9_999_999_999_999_999).all():
        raise ValueError(f"{label}: expected unambiguous 2026 microsecond open timestamps")
    if not out.close_time.between(1_000_000_000_000_000, 9_999_999_999_999_999).all():
        raise ValueError(f"{label}: expected unambiguous 2026 microsecond close timestamps")
    out["open_time_utc"] = pd.to_datetime(out.open_time, unit="us", utc=True)
    out["close_time_utc"] = pd.to_datetime(out.close_time, unit="us", utc=True)
    if (out.close_time - out.open_time != 14_400_000_000 - 1).any():
        raise ValueError(f"{label}: close timestamp does not describe one 4h candle")
    if ((out.open_time_utc.dt.minute != 0) | (out.open_time_utc.dt.second != 0) |
            (out.open_time_utc.dt.microsecond != 0) | (~out.open_time_utc.dt.hour.isin([0, 4, 8, 12, 16, 20]))).any():
        raise ValueError(f"{label}: open timestamps are not UTC 4h boundaries")
    price = out[["open", "high", "low", "close"]].to_numpy(dtype=float)
    other = out[["volume", "quote_asset_volume", "taker_buy_base_asset_volume",
                 "taker_buy_quote_asset_volume"]].to_numpy(dtype=float)
    if not np.isfinite(price).all() or (price <= 0).any() or not np.isfinite(other).all() or (other < 0).any():
        raise ValueError(f"{label}: invalid OHLCV values")
    if ((out.low > out[["open", "close"]].min(axis=1)) |
            (out.high < out[["open", "close"]].max(axis=1)) | (out.low > out.high)).any():
        raise ValueError(f"{label}: inconsistent OHLC bounds")
    if (out.number_of_trades < 0).any() or out.open_time_utc.duplicated().any():
        raise ValueError(f"{label}: invalid trade count or duplicate open time")
    return out


def normalized_output(frame: pd.DataFrame) -> pd.DataFrame:
    """Build the persisted schema without retaining raw integer timestamp columns."""
    return pd.DataFrame({
        "open_time": frame.open_time_utc,
        "open": frame.open,
        "high": frame.high,
        "low": frame.low,
        "close": frame.close,
        "volume": frame.volume,
        "close_time": frame.close_time_utc,
        "quote_asset_volume": frame.quote_asset_volume,
        "number_of_trades": frame.number_of_trades,
        "taker_buy_base_asset_volume": frame.taker_buy_base_asset_volume,
        "taker_buy_quote_asset_volume": frame.taker_buy_quote_asset_volume,
    })


def normalize(root: Path, resume: bool = False) -> dict[str, Any]:
    verify_frozen_inputs(root)
    target = paths(root)
    acquisition_path = target["holdout"] / "acquisition_manifest.json"
    acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
    if acquisition.get("status") != "COMPLETE" or len(acquisition.get("files", [])) != 63:
        raise RuntimeError("DATA_ACQUISITION_BLOCKED: complete 63-archive manifest required")
    audit_path = target["holdout"] / "normalization_manifest.json"
    resume_record: dict[str, Any] | None = None
    if resume:
        if not target["normalized"].exists() or not audit_path.exists():
            raise ValueError("normalization resume requires preserved failed attempt")
        failed = json.loads(audit_path.read_text(encoding="utf-8"))
        if failed.get("status") != "DATA_QUALITY_BLOCKED":
            raise ValueError("normalization resume is allowed only after a recorded failed attempt")
        preserved_dir = target["holdout"] / "normalized_failed_attempt_1"
        preserved_manifest = target["holdout"] / "normalization_manifest_attempt_1.json"
        require_absent(preserved_dir, "failed normalization archive")
        require_absent(preserved_manifest, "failed normalization manifest")
        target["normalized"].rename(preserved_dir)
        shutil.copy2(audit_path, preserved_manifest)
        resume_record = {"resumed_at": utc_now(), "reason": "implementation error before economic evaluation",
                         "preserved_directory": preserved_dir.name,
                         "preserved_manifest": preserved_manifest.name,
                         "preserved_manifest_sha256": sha256(preserved_manifest)}
    else:
        require_absent(target["normalized"], "normalized namespace")
    target["normalized"].mkdir(exist_ok=False)
    audit = {"version": "m18.2-normalized-v1", "status": "RUNNING", "started_at": utc_now(), "symbols": []}
    if resume_record is not None:
        audit["resume"] = resume_record
    write_json(audit_path, audit)
    try:
        common = pd.date_range(START, END, freq="4h", inclusive="left")
        for symbol in SYMBOLS:
            pieces = []
            for month in MONTHS:
                archive_name = f"{symbol}-4h-{month}.zip"
                archive = target["raw"] / symbol / archive_name
                checksum = archive.with_name(archive.name + ".CHECKSUM")
                if sha256(archive) != expected_checksum(checksum, archive_name):
                    raise ValueError(f"checksum changed before normalization: {archive_name}")
                piece = validate_archive_frame(_read_archive(archive), archive_name)
                year, month_number = map(int, month.split("-"))
                if not ((piece.open_time_utc.dt.year == year) &
                        (piece.open_time_utc.dt.month == month_number)).all():
                    raise ValueError(f"{archive_name}: row outside named calendar month")
                pieces.append(piece)
            frame = pd.concat(pieces, ignore_index=True).sort_values("open_time_utc").reset_index(drop=True)
            if len(frame) != 1638 or not frame.open_time_utc.equals(pd.Series(common, name="open_time_utc")):
                raise ValueError(f"DATA_QUALITY_BLOCKED: {symbol} does not match exact 1638-candle grid")
            output = normalized_output(frame)
            path = target["normalized"] / f"{symbol}_4h.csv"
            output.to_csv(path, index=False)
            audit["symbols"].append({"symbol": symbol, "rows": len(output),
                                     "start": output.open_time.iloc[0], "end": output.open_time.iloc[-1],
                                     "sha256": sha256(path), "bytes": path.stat().st_size,
                                     "gaps": int((output.open_time.diff().dropna() != BAR).sum()),
                                     "duplicates": int(output.open_time.duplicated().sum())})
            write_json(audit_path, audit)
        hashes = [entry["sha256"] for entry in audit["symbols"]]
        if len(set(hashes)) != len(hashes):
            raise ValueError("unexpected duplicate normalized symbol files")
        audit.update(status="COMPLETE", completed_at=utc_now(), symbols_count=7,
                     rows_per_symbol=1638, common_grid=True, timestamp_unit="microseconds")
        write_json(audit_path, audit)
        return audit
    except Exception as exc:
        audit.update(status="DATA_QUALITY_BLOCKED", stopped_at=utc_now(),
                     error_type=type(exc).__name__, error=str(exc))
        write_json(audit_path, audit)
        raise


def assemble_strategy_frame(warmup: pd.DataFrame, evaluation: pd.DataFrame) -> pd.DataFrame:
    warm = warmup.copy(); current = evaluation.copy()
    warm["open_time"] = pd.to_datetime(warm.open_time, utc=True)
    current["open_time"] = pd.to_datetime(current.open_time, utc=True)
    expected_warm = pd.date_range(WARMUP_START, START, freq="4h", inclusive="left")
    expected_eval = pd.date_range(START, END, freq="4h", inclusive="left")
    if len(warm) != 120 or not warm.open_time.reset_index(drop=True).equals(pd.Series(expected_warm, name="open_time")):
        raise ValueError("warm-up must contain exactly the frozen 120-candle grid")
    if len(current) != 1638 or not current.open_time.reset_index(drop=True).equals(pd.Series(expected_eval, name="open_time")):
        raise ValueError("evaluation must contain exactly the frozen 1638-candle grid")
    return pd.concat([warm, current], ignore_index=True)


def run_strategy(frames: dict[str, pd.DataFrame], commission: float, slippage: float
                 ) -> tuple[list[SleeveResult], pd.DataFrame]:
    cash = INITIAL_CAPITAL / len(SYMBOLS)
    results = [simulate_sleeve(frames[s], s, cash, commission, slippage, START, LAST_OPEN) for s in SYMBOLS]
    if any(any(pd.Timestamp(fill["fill_time"]) < START + BAR for fill in result.fills) for result in results):
        raise ValueError("flat-start policy produced an early inherited fill")
    return results, aggregate(results)


def daily_reconciliation(equity: pd.DataFrame) -> tuple[pd.Series, float]:
    returns = daily_returns(equity)
    factor = float(np.prod(1 + returns.to_numpy(dtype=float)))
    total = float(equity.equity.iloc[-1] / equity.equity.iloc[0])
    error = factor - total
    if len(returns) != 273 or returns.index[0] != START + pd.Timedelta(days=1) or returns.index[-1] != END:
        raise ValueError("daily endpoint coverage does not match the frozen 273-day path")
    if abs(error) > 1e-12:
        raise ValueError("daily factor reconciliation failed")
    return returns, error


def paired_difference(left: pd.Series, right: pd.Series) -> pd.Series:
    if not left.index.equals(right.index) or not left.index.is_unique:
        raise ValueError("paired endpoints must match exactly")
    result = left - right
    if not np.isfinite(result.to_numpy(dtype=float)).all():
        raise ValueError("paired difference contains non-finite values")
    return result


def calendar_blocks(difference: pd.Series, block_days: int) -> pd.DataFrame:
    if difference.empty or not difference.index.is_monotonic_increasing:
        raise ValueError("ordered daily differences required")
    interval_start = difference.index - pd.Timedelta(days=1)
    ids = ((interval_start - START) // pd.Timedelta(days=block_days)).astype(int)
    rows = []
    for block_id in sorted(set(ids)):
        mask = ids == block_id; values = difference.to_numpy(dtype=float)[mask]
        endpoints = difference.index[mask]
        nominal_start = START + block_id * pd.Timedelta(days=block_days)
        nominal_end = nominal_start + pd.Timedelta(days=block_days)
        rows.append({"block": int(block_id), "nominal_start": nominal_start, "nominal_end": nominal_end,
                     "first_return_end": endpoints[0], "last_return_end": endpoints[-1],
                     "sum": float(values.sum()), "count": len(values),
                     "complete": bool(len(values) == block_days and endpoints[-1] == nominal_end)})
    return pd.DataFrame(rows)


def bootstrap(blocks: pd.DataFrame, repetitions: int = 5000, seed: int = 42) -> dict[str, float]:
    sums = blocks["sum"].to_numpy(dtype=float); counts = blocks["count"].to_numpy(dtype=int)
    rng = np.random.default_rng(seed); samples = np.empty(repetitions)
    for index in range(repetitions):
        chosen = rng.integers(0, len(blocks), size=len(blocks))
        samples[index] = sums[chosen].sum() / counts[chosen].sum()
    point = float(sums.sum() / counts.sum())
    return {"mean_difference": point,
            "ci_95_lower": float(np.quantile(samples, 0.025)),
            "ci_95_upper": float(np.quantile(samples, 0.975)),
            "ci_97_5_lower": float(np.quantile(samples, 0.0125)),
            "ci_97_5_upper": float(np.quantile(samples, 0.9875)),
            "bootstrap_positive_fraction": float((samples > 0).mean())}


def classify_claim(kind: str, point: float, adjusted_lower: float, strategy_net_return: float) -> str:
    if point <= 0:
        return "NO_ABSOLUTE_EVIDENCE" if kind == "A" else "NO_INCREMENTAL_EVIDENCE"
    if adjusted_lower > 0 and strategy_net_return > 0:
        return "POSITIVE_ABSOLUTE_EVIDENCE" if kind == "A" else "POSITIVE_INCREMENTAL_EVIDENCE"
    return "INCONCLUSIVE_ABSOLUTE" if kind == "A" else "INCONCLUSIVE_INCREMENTAL"


def overall_status(a_classification: str, b_classification: str, a_point: float, b_point: float,
                   stress_strategy_return: float, stress_relative_point: float,
                   blocker: str | None = None) -> str:
    if blocker is not None:
        return blocker
    if (a_classification == "POSITIVE_ABSOLUTE_EVIDENCE" and
            b_classification == "POSITIVE_INCREMENTAL_EVIDENCE" and
            stress_strategy_return > 0 and stress_relative_point > 0):
        return "PROMISING_CANDIDATE_UNCERTAIN_PROVENANCE"
    if a_point <= 0 or b_point <= 0:
        return "NO_EVIDENCE_FOR_COMBINED_HYPOTHESIS"
    return "PARTIAL_OR_INCONCLUSIVE"


def policy_metrics(equity: pd.DataFrame, results: list[SleeveResult]) -> dict[str, Any]:
    if results:
        result = metrics(equity, results, INITIAL_CAPITAL)
    else:
        dd, dd_bars = drawdown_metrics(equity)
        result = {"ending_equity": float(equity.equity.iloc[-1]), "net_return": 0.0,
                  "annualized_return": 0.0, "mean_daily_return": 0.0, "trades": 0,
                  "expectancy": 0.0, "profit_factor": 0.0, "time_in_market": 0.0,
                  "average_exposure": 0.0, "max_exposure": 0.0, "turnover": 0.0,
                  "turnover_ratio": 0.0, "mean_holding_hours": 0.0, "median_holding_hours": 0.0,
                  "max_drawdown": dd, "max_drawdown_bars": dd_bars,
                  "commission": 0.0, "slippage_cost": 0.0}
    daily, error = daily_reconciliation(equity)
    result.update(realized_daily_volatility=float(daily.std(ddof=1)),
                  annualized_volatility=float(daily.std(ddof=1) * math.sqrt(365)),
                  max_drawdown_days=float(result["max_drawdown_bars"] * 4 / 24),
                  total_costs=float(result["commission"] + result["slippage_cost"]),
                  daily_intervals=len(daily), reconciliation_error=error)
    return result


def _cash_equity() -> pd.DataFrame:
    times = pd.date_range(START, END, freq="4h")
    frame = pd.DataFrame({"time": times, "equity": INITIAL_CAPITAL, "position_value": 0.0,
                          "cash": INITIAL_CAPITAL, "exposure": 0.0})
    frame["equity_mark_time"] = frame.time
    return frame


def _result_rows(results: list[SleeveResult], attribute: str, **tags: Any) -> list[dict[str, Any]]:
    return [{**tags, **row} for result in results for row in getattr(result, attribute)]


def _equity_rows(equity: pd.DataFrame, **tags: Any) -> list[dict[str, Any]]:
    frame = equity.copy()
    if "cash" not in frame:
        frame["cash"] = frame.equity - frame.position_value
    return [{**tags, **row} for row in frame[["time", "equity", "position_value", "cash", "exposure"]].to_dict("records")]


def _load_validated_frames(root: Path) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, Any]]:
    target = paths(root); manifest_path = target["holdout"] / "normalization_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "COMPLETE" or manifest.get("rows_per_symbol") != 1638:
        raise RuntimeError("DATA_QUALITY_BLOCKED: complete normalized manifest required")
    expected = {row["symbol"]: row for row in manifest["symbols"]}
    old_protocol = json.loads((root / "docs/M17_1_PROTOCOL.json").read_text(encoding="utf-8"))
    strategy_frames, evaluation_frames = {}, {}
    for symbol in SYMBOLS:
        normalized = target["normalized"] / f"{symbol}_4h.csv"
        if sha256(normalized) != expected[symbol]["sha256"]:
            raise ValueError(f"normalized hash mismatch: {symbol}")
        evaluation = pd.read_csv(normalized, parse_dates=["open_time", "close_time"])
        history_path = root / "data/history" / f"{symbol}_4h.csv"
        if sha256(history_path) != old_protocol["data_hashes"][symbol]:
            raise ValueError(f"warm-up source hash mismatch: {symbol}")
        history = pd.read_csv(history_path); history["open_time"] = pd.to_datetime(history.open_time, utc=True)
        warmup = history[(history.open_time >= WARMUP_START) & (history.open_time <= WARMUP_END)].copy()
        strategy_frames[symbol] = assemble_strategy_frame(warmup, evaluation)
        evaluation_frames[symbol] = evaluation
    return strategy_frames, evaluation_frames, manifest


def evaluate(root: Path, code_freeze_path: Path) -> dict[str, Any]:
    protected = verify_frozen_inputs(root); target = paths(root)
    freeze = json.loads(code_freeze_path.read_text(encoding="utf-8"))
    module_path = root / "app/research/frozen_evaluation.py"
    if sha256(module_path) != freeze["evaluation_code_sha256"]:
        raise ValueError("evaluation code differs from pre-result freeze")
    require_absent(target["evaluation"], "one-time evaluation")
    target["evaluation"].mkdir(parents=True, exist_ok=False)
    status_path = target["evaluation"] / "execution_status.json"
    write_json(status_path, {"status": "RUNNING", "started_at": utc_now(), "batch": 1,
                             "provenance": "HOLDOUT_PROVENANCE_UNCERTAIN"})
    try:
        strategy_frames, evaluation_frames, normalization = _load_validated_frames(root)
        all_metrics, all_equity, all_daily = [], [], []
        signals, fills, trades, contributions, blocks_out, effects = [], [], [], [], [], []
        cache: dict[tuple[str, str], tuple[list[SleeveResult], pd.DataFrame, pd.Series]] = {}
        for scenario, costs in SCENARIOS.items():
            strategy = run_strategy(strategy_frames, *costs)
            full = run_passive(evaluation_frames, START, LAST_OPEN, 1.0, *costs)
            calibrated = run_passive(evaluation_frames, START, LAST_OPEN, LAMBDA, *costs)
            cash = ([], _cash_equity())
            for policy, (results, equity) in {"strategy": strategy, "buyhold_full": full,
                                               "buyhold_calibrated": calibrated, "cash": cash}.items():
                daily, _ = daily_reconciliation(equity)
                cache[(scenario, policy)] = results, equity, daily
                tags = {"scenario": scenario, "policy": policy}
                all_metrics.append({**tags, **policy_metrics(equity, results)})
                all_equity.extend(_equity_rows(equity, **tags))
                all_daily.extend({**tags, "date": date, "daily_net_return": value} for date, value in daily.items())
                signals.extend(_result_rows(results, "signals", **tags)); fills.extend(_result_rows(results, "fills", **tags))
                trades.extend(_result_rows(results, "trades", **tags))
                initial = INITIAL_CAPITAL / len(SYMBOLS) if policy != "buyhold_calibrated" else INITIAL_CAPITAL * LAMBDA / len(SYMBOLS)
                for symbol, result in zip(SYMBOLS, results):
                    contributions.append({**tags, "symbol": symbol, "initial_capital": initial,
                                          "ending_equity": result.state.cash,
                                          "contribution": result.state.cash - initial,
                                          "trades": len(result.trades), "turnover": result.state.turnover,
                                          "commission": result.state.commission,
                                          "slippage_cost": result.state.slippage})
            for claim, comparator in (("A_strategy_minus_cash", "cash"),
                                      ("B_strategy_minus_calibrated_passive", "buyhold_calibrated")):
                difference = paired_difference(cache[(scenario, "strategy")][2], cache[(scenario, comparator)][2])
                for days in (28, 14, 56):
                    block_frame = calendar_blocks(difference, days)
                    if days == 28 and (len(block_frame) != 10 or block_frame.iloc[-1]["count"] != 21):
                        raise ValueError("primary block structure differs from preregistration")
                    effect = bootstrap(block_frame)
                    effects.append({"scenario": scenario, "claim": claim, "block_days": days,
                                    "observations": len(difference), "block_count": len(block_frame), **effect})
                    blocks_out.extend({"scenario": scenario, "claim": claim, "block_days": days, **row}
                                      for row in block_frame.to_dict("records"))

        metrics_frame = pd.DataFrame(all_metrics); effects_frame = pd.DataFrame(effects)
        def metric(scenario: str, policy: str, column: str) -> float:
            return float(metrics_frame[(metrics_frame.scenario == scenario) &
                                       (metrics_frame.policy == policy)].iloc[0][column])
        def effect(scenario: str, claim: str, column: str) -> float:
            return float(effects_frame[(effects_frame.scenario == scenario) &
                                       (effects_frame.claim == claim) &
                                       (effects_frame.block_days == 28)].iloc[0][column])
        strategy_net = metric("base", "strategy", "net_return")
        a_point = effect("base", "A_strategy_minus_cash", "mean_difference")
        b_point = effect("base", "B_strategy_minus_calibrated_passive", "mean_difference")
        a_class = classify_claim("A", a_point, effect("base", "A_strategy_minus_cash", "ci_97_5_lower"), strategy_net)
        b_class = classify_claim("B", b_point, effect("base", "B_strategy_minus_calibrated_passive", "ci_97_5_lower"), strategy_net)
        stress_strategy = metric("double", "strategy", "net_return")
        stress_relative = effect("double", "B_strategy_minus_calibrated_passive", "mean_difference")
        overall = overall_status(a_class, b_class, a_point, b_point, stress_strategy, stress_relative)

        quarterly = []
        quarters = [(pd.Timestamp("2026-01-01T00:00:00Z"), pd.Timestamp("2026-04-01T00:00:00Z")),
                    (pd.Timestamp("2026-04-01T00:00:00Z"), pd.Timestamp("2026-07-01T00:00:00Z")),
                    (pd.Timestamp("2026-07-01T00:00:00Z"), pd.Timestamp("2026-10-01T00:00:00Z"))]
        for scenario in SCENARIOS:
            for policy in ("strategy", "buyhold_full", "buyhold_calibrated", "cash"):
                equity = cache[(scenario, policy)][1].set_index("time")
                for index, (start, end) in enumerate(quarters, 1):
                    selected = equity.loc[start:end]
                    values = selected.equity.to_numpy(dtype=float)
                    dd = values / np.maximum.accumulate(values) - 1
                    quarterly.append({"scenario": scenario, "policy": policy, "quarter": index,
                                      "start": start, "end": end, "net_return": float(values[-1] / values[0] - 1),
                                      "max_drawdown": float(dd.min()), "average_exposure": float(selected.exposure.mean())})

        pd.DataFrame(all_metrics).to_csv(target["evaluation"] / "metrics.csv", index=False)
        _records(target["evaluation"] / "portfolio_equity.csv", all_equity)
        _records(target["evaluation"] / "daily_returns.csv", all_daily)
        _records(target["evaluation"] / "signals.csv", signals)
        _records(target["evaluation"] / "fills.csv", fills)
        _records(target["evaluation"] / "trades.csv", trades)
        pd.DataFrame(contributions).to_csv(target["evaluation"] / "symbol_contributions.csv", index=False)
        effects_frame.to_csv(target["evaluation"] / "bootstrap_effects.csv", index=False)
        _records(target["evaluation"] / "bootstrap_blocks.csv", blocks_out)
        pd.DataFrame(quarterly).to_csv(target["evaluation"] / "calendar_subwindows.csv", index=False)
        source_hashes = {**protected, "app/research/frozen_evaluation.py": sha256(module_path),
                         "docs/M18_2_EXECUTION_MANIFEST.json": sha256(root / "docs/M18_2_EXECUTION_MANIFEST.json"),
                         "acquisition_manifest.json": sha256(target["holdout"] / "acquisition_manifest.json"),
                         "normalization_manifest.json": sha256(target["holdout"] / "normalization_manifest.json")}
        write_json(target["evaluation"] / "source_hashes.json", source_hashes)
        summary = {"version": "m18.2-one-time-frozen-evaluation-v1", "batch": 1,
                   "provenance": "HOLDOUT_PROVENANCE_UNCERTAIN", "overall_status": overall,
                   "claim_A": {"classification": a_class, "point": a_point,
                               "ci_95": [effect("base", "A_strategy_minus_cash", "ci_95_lower"), effect("base", "A_strategy_minus_cash", "ci_95_upper")],
                               "ci_97_5": [effect("base", "A_strategy_minus_cash", "ci_97_5_lower"), effect("base", "A_strategy_minus_cash", "ci_97_5_upper")]},
                   "claim_B": {"classification": b_class, "point": b_point,
                               "ci_95": [effect("base", "B_strategy_minus_calibrated_passive", "ci_95_lower"), effect("base", "B_strategy_minus_calibrated_passive", "ci_95_upper")],
                               "ci_97_5": [effect("base", "B_strategy_minus_calibrated_passive", "ci_97_5_lower"), effect("base", "B_strategy_minus_calibrated_passive", "ci_97_5_upper")]},
                   "stress": {"strategy_net_return": stress_strategy, "relative_point": stress_relative},
                   "lambda": LAMBDA, "metrics": all_metrics,
                   "data_validation": normalization,
                   "warning": "Bootstrap-positive fraction is not the probability that an edge exists. No result authorizes trading or shadow use."}
        write_json(target["evaluation"] / "summary.json", summary)
        manifest = {path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
                    for path in sorted(target["evaluation"].iterdir()) if path.is_file() and path.name not in {"artifact_manifest.json", "execution_status.json"}}
        write_json(target["evaluation"] / "artifact_manifest.json", manifest)
        write_json(status_path, {"status": "COMPLETE", "completed_at": utc_now(), "batch": 1,
                                 "overall_status": overall, "artifact_count": len(manifest),
                                 "provenance": "HOLDOUT_PROVENANCE_UNCERTAIN"})
        return summary
    except Exception as exc:
        write_json(status_path, {"status": "FAILED_REVIEW_REQUIRED", "stopped_at": utc_now(), "batch": 1,
                                 "error_type": type(exc).__name__, "error": str(exc),
                                 "traceback": traceback.format_exc(),
                                 "provenance": "HOLDOUT_PROVENANCE_UNCERTAIN"})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="M18.2 frozen acquisition and one-time evaluation")
    parser.add_argument("action", choices=["acquire", "normalize", "evaluate"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--code-freeze", type=Path)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--resume-normalization", action="store_true")
    args = parser.parse_args(); root = (args.root or Path(__file__).resolve().parents[2]).resolve()
    if args.action == "acquire":
        result = acquire(root, args.attempts); print(json.dumps({"status": result["status"], "archives": result.get("archive_count")}, indent=2))
    elif args.action == "normalize":
        result = normalize(root, resume=args.resume_normalization)
        print(json.dumps({"status": result["status"], "symbols": result.get("symbols_count")}, indent=2))
    else:
        if args.code_freeze is None:
            parser.error("--code-freeze is required for evaluate")
        result = evaluate(root, args.code_freeze)
        print(json.dumps({"overall_status": result["overall_status"], "claim_A": result["claim_A"]["classification"],
                          "claim_B": result["claim_B"]["classification"]}, indent=2))


if __name__ == "__main__":
    main()
