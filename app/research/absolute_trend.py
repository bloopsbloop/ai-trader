"""M17.1R fixed Donchian research with explicit event time and safe artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT", "DOGEUSDT")
BAR = pd.Timedelta(hours=4)
ENTRY_LOOKBACK, EXIT_LOOKBACK = 120, 60
INITIAL_CAPITAL = 1000.0
ORIGINAL_NAMESPACE = "m17_1_absolute_trend_v1"
CORRECTED_NAMESPACE = "m17_1_review_corrections_v1"


@dataclass
class SleeveState:
    cash: float
    qty: float = 0.0
    pending: str | None = None
    pending_fill_time: pd.Timestamp | None = None
    entry_time: pd.Timestamp | None = None
    entry_mid: float | None = None
    entry_cash: float | None = None
    entry_commission: float = 0.0
    entry_slippage: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    turnover: float = 0.0
    position_bars: int = 0
    last_open_time: pd.Timestamp | None = None
    high_history: list[float] = field(default_factory=list)
    low_history: list[float] = field(default_factory=list)


@dataclass
class SleeveResult:
    state: SleeveState
    equity: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)


def load_history(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame.sort_values("open_time").reset_index(drop=True)
    validate_history(frame, path.name)
    return frame


def validate_history(frame: pd.DataFrame, label: str = "history") -> dict[str, Any]:
    required = ["open_time", "open", "high", "low", "close", "volume"]
    if any(column not in frame for column in required):
        raise ValueError(f"{label}: missing OHLCV columns")
    if frame.open_time.dt.tz is None or str(frame.open_time.dt.tz) != "UTC":
        raise ValueError(f"{label}: timestamps must be UTC")
    if frame.open_time.duplicated().any() or not frame.open_time.is_monotonic_increasing:
        raise ValueError(f"{label}: timestamps must be unique and increasing")
    values = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError(f"{label}: OHLC must be finite and positive")
    if ((frame.low > frame[["open", "close"]].min(axis=1)) |
            (frame.high < frame[["open", "close"]].max(axis=1)) | (frame.low > frame.high)).any():
        raise ValueError(f"{label}: inconsistent OHLC bounds")
    deltas = frame.open_time.diff().dropna()
    return {"rows": len(frame), "start": frame.open_time.iloc[0], "end": frame.open_time.iloc[-1],
            "gap_count": int((deltas != BAR).sum()),
            "max_gap_hours": float(deltas.max() / pd.Timedelta(hours=1))}


def channel_signals(frame: pd.DataFrame) -> pd.DataFrame:
    """Vector reference whose channels contain strictly prior candles."""
    out = frame[["open_time", "close"]].copy()
    out["bar_close_time"] = out.open_time + BAR
    out["entry_channel"] = frame.high.shift(1).rolling(ENTRY_LOOKBACK).max()
    out["exit_channel"] = frame.low.shift(1).rolling(EXIT_LOOKBACK).min()
    out["entry"] = out.close > out.entry_channel
    out["exit"] = out.close < out.exit_channel
    return out


def _initial_mark(state: SleeveState, symbol: str, time: pd.Timestamp) -> dict[str, Any]:
    return {"symbol": symbol, "time": time, "equity_mark_time": time, "bar_open_time": pd.NaT,
            "equity": state.cash, "position_value": 0.0, "cash": state.cash, "event": "initial"}


def _buy(state: SleeveState, ts: pd.Timestamp, mid: float, commission: float, slippage: float,
         symbol: str, result: SleeveResult) -> None:
    execution = mid * (1 + slippage); starting_cash = state.cash
    qty = starting_cash / (execution * (1 + commission)); notional = qty * execution
    fee = notional * commission; slip = qty * (execution - mid)
    state.cash = max(0.0, starting_cash - notional - fee); state.qty = qty
    state.entry_time, state.entry_mid, state.entry_cash = ts, mid, starting_cash
    state.entry_commission, state.entry_slippage = fee, slip
    state.commission += fee; state.slippage += slip; state.turnover += notional
    result.fills.append({"symbol": symbol, "fill_time": ts, "time": ts, "side": "BUY", "mid": mid,
                         "price": execution, "qty": qty, "notional": notional, "commission": fee,
                         "slippage_cost": slip, "terminal": False})


def _sell(state: SleeveState, ts: pd.Timestamp, mid: float, commission: float, slippage: float,
          symbol: str, result: SleeveResult, terminal: bool = False) -> None:
    execution = mid * (1 - slippage); qty = state.qty; proceeds = qty * execution
    fee = proceeds * commission; slip = qty * (mid - execution)
    state.cash += proceeds - fee; state.qty = 0.0
    state.commission += fee; state.slippage += slip; state.turnover += proceeds
    result.fills.append({"symbol": symbol, "fill_time": ts, "time": ts, "side": "SELL", "mid": mid,
                         "price": execution, "qty": qty, "notional": proceeds, "commission": fee,
                         "slippage_cost": slip, "terminal": terminal})
    result.trades.append({"symbol": symbol, "entry_time": state.entry_time, "exit_time": ts,
                          "holding_hours": (ts - state.entry_time).total_seconds() / 3600,
                          "gross_pnl": qty * (mid - float(state.entry_mid)),
                          "net_pnl": state.cash - float(state.entry_cash),
                          "commission": state.entry_commission + fee,
                          "slippage_cost": state.entry_slippage + slip, "terminal": terminal})
    state.entry_time = state.entry_mid = state.entry_cash = None
    state.entry_commission = state.entry_slippage = 0.0


def _signal_from_history(state: SleeveState, close: float) -> str | None:
    if state.qty == 0 and len(state.high_history) >= ENTRY_LOOKBACK:
        if close > max(state.high_history[-ENTRY_LOOKBACK:]):
            return "BUY"
    elif state.qty > 0 and len(state.low_history) >= EXIT_LOOKBACK:
        if close < min(state.low_history[-EXIT_LOOKBACK:]):
            return "SELL"
    return None


def simulate_sleeve(frame: pd.DataFrame, symbol: str, initial_cash: float,
                    commission: float = 0.0004, slippage: float = 0.0005,
                    trade_start: pd.Timestamp | None = None, trade_end: pd.Timestamp | None = None,
                    state: SleeveState | None = None, finalize: bool = True,
                    emit_initial: bool | None = None) -> SleeveResult:
    """Continue a sleeve with rolling history and pending orders stored in state.

    Shared-boundary order is prior close/signal, current open fill, current close
    mark/signal. Pending orders expire if their exact next-open time is absent.
    """
    frame = frame.sort_values("open_time").reset_index(drop=True)
    state_was_supplied = state is not None; state = state or SleeveState(initial_cash)
    result = SleeveResult(state)
    emit_initial = (not state_was_supplied) if emit_initial is None else emit_initial
    started = False; last_active_row = None
    for _, row in frame.iterrows():
        ts = row.open_time
        if trade_end is not None and ts > trade_end:
            break
        active = (trade_start is None or ts >= trade_start) and (trade_end is None or ts <= trade_end)
        if active and not started:
            if emit_initial:
                result.equity.append(_initial_mark(state, symbol, ts))
            started = True
        if state.pending is not None and ts >= state.pending_fill_time:
            if active and ts == state.pending_fill_time:
                if state.pending == "BUY" and state.qty == 0:
                    _buy(state, ts, float(row.open), commission, slippage, symbol, result)
                elif state.pending == "SELL" and state.qty > 0:
                    _sell(state, ts, float(row.open), commission, slippage, symbol, result)
            state.pending = None; state.pending_fill_time = None
        close_time = ts + BAR
        if active:
            signal = _signal_from_history(state, float(row.close))
            if signal is not None:
                state.pending, state.pending_fill_time = signal, close_time
                result.signals.append({"symbol": symbol, "bar_open_time": ts, "bar_close_time": close_time,
                                       "signal_available_time": close_time, "time": close_time, "side": signal})
            if state.qty > 0:
                state.position_bars += 1
            result.equity.append({"symbol": symbol, "time": close_time, "equity_mark_time": close_time,
                                  "bar_open_time": ts, "equity": state.cash + state.qty * float(row.close),
                                  "position_value": state.qty * float(row.close), "cash": state.cash,
                                  "event": "close"})
            last_active_row = row
        state.high_history.append(float(row.high)); state.low_history.append(float(row.low))
        del state.high_history[:-ENTRY_LOOKBACK]; del state.low_history[:-ENTRY_LOOKBACK]
        state.last_open_time = ts
    if finalize and last_active_row is not None:
        terminal_time = last_active_row.open_time + BAR
        state.pending = None; state.pending_fill_time = None
        terminal = state.qty > 0
        if terminal:
            _sell(state, terminal_time, float(last_active_row.close), commission, slippage, symbol, result, True)
        result.equity[-1].update(equity=state.cash if terminal else result.equity[-1]["equity"],
                                 position_value=0.0 if terminal else result.equity[-1]["position_value"],
                                 cash=state.cash, event="terminal" if terminal else "close")
    return result


def buy_and_hold(frame: pd.DataFrame, symbol: str, initial_cash: float, start: pd.Timestamp,
                 commission: float, slippage: float, end: pd.Timestamp | None = None) -> SleeveResult:
    mask = frame.open_time >= start
    if end is not None:
        mask &= frame.open_time <= end
    selected = frame[mask].copy(); state = SleeveState(initial_cash); result = SleeveResult(state)
    first = selected.iloc[0]; result.equity.append(_initial_mark(state, symbol, first.open_time))
    _buy(state, first.open_time, float(first.open), commission, slippage, symbol, result)
    for _, row in selected.iterrows():
        close_time = row.open_time + BAR; state.position_bars += 1
        result.equity.append({"symbol": symbol, "time": close_time, "equity_mark_time": close_time,
                              "bar_open_time": row.open_time, "equity": state.cash + state.qty * float(row.close),
                              "position_value": state.qty * float(row.close), "cash": state.cash, "event": "close"})
    last = selected.iloc[-1]
    _sell(state, last.open_time + BAR, float(last.close), commission, slippage, symbol, result, True)
    result.equity[-1].update(equity=state.cash, position_value=0.0, cash=state.cash, event="terminal")
    return result


def aggregate(results: list[SleeveResult]) -> pd.DataFrame:
    parts = []
    for index, result in enumerate(results):
        part = pd.DataFrame(result.equity).set_index("time")[["equity", "position_value"]]
        if not part.index.is_unique:
            raise ValueError("duplicate equity marks are ambiguous")
        parts.append(part.rename(columns={"equity": f"e{index}", "position_value": f"p{index}"}))
    merged = pd.concat(parts, axis=1).sort_index().ffill()
    if merged.isna().any().any():
        raise ValueError("sleeve equity grids do not share an initial mark")
    ecols = [column for column in merged if column.startswith("e")]
    pcols = [column for column in merged if column.startswith("p")]
    merged["equity"] = merged[ecols].sum(axis=1); merged["position_value"] = merged[pcols].sum(axis=1)
    merged["exposure"] = merged.position_value / merged.equity.replace(0, np.nan)
    result = merged.reset_index().rename(columns={"time": "equity_mark_time"})
    result["time"] = result.equity_mark_time
    return result


def drawdown_metrics(equity: pd.DataFrame) -> tuple[float, int]:
    values = equity.equity.to_numpy(dtype=float); dd = values / np.maximum.accumulate(values) - 1
    duration = best = 0
    for value in dd:
        duration = duration + 1 if value < 0 else 0; best = max(best, duration)
    return float(dd.min()), int(best)


def daily_returns(equity: pd.DataFrame) -> pd.Series:
    series = equity.set_index("time").equity
    if not series.index.is_unique or not series.index.is_monotonic_increasing:
        raise ValueError("equity marks must be unique and ordered")
    midnight = series[series.index.hour == 0]
    if len(midnight) < 2 or not (midnight.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all():
        raise ValueError("daily equity boundary grid is not continuous")
    result = midnight.pct_change().iloc[1:]
    if not np.isfinite(result.to_numpy()).all():
        raise ValueError("daily returns contain NaN or infinity")
    result.index.name = "date"
    return result


def metrics(equity: pd.DataFrame, results: list[SleeveResult], initial: float) -> dict[str, Any]:
    if "exposure" not in equity:
        equity = equity.copy(); equity["exposure"] = equity.position_value / equity.equity.replace(0, np.nan)
    end = float(equity.equity.iloc[-1]); days = (equity.time.iloc[-1] - equity.time.iloc[0]).total_seconds() / 86400
    daily = daily_returns(equity); trades = [trade for result in results for trade in result.trades]
    pnl = np.array([trade["net_pnl"] for trade in trades]); wins = pnl[pnl > 0].sum(); losses = -pnl[pnl < 0].sum()
    dd, dd_bars = drawdown_metrics(equity)
    return {"ending_equity": end, "net_return": end / initial - 1,
            "annualized_return": (end / initial) ** (365 / days) - 1,
            "mean_daily_return": float(daily.mean()), "trades": len(trades),
            "expectancy": float(pnl.mean()) if len(pnl) else 0.0,
            "profit_factor": float(wins / losses) if losses else (float("inf") if wins else 0.0),
            "time_in_market": sum(r.state.position_bars for r in results) / ((len(equity) - 1) * len(results)),
            "average_exposure": float(equity.exposure.mean()), "max_exposure": float(equity.exposure.max()),
            "turnover": sum(r.state.turnover for r in results), "turnover_ratio": sum(r.state.turnover for r in results) / initial,
            "mean_holding_hours": float(np.mean([t["holding_hours"] for t in trades])) if trades else 0.0,
            "median_holding_hours": float(np.median([t["holding_hours"] for t in trades])) if trades else 0.0,
            "max_drawdown": dd, "max_drawdown_bars": dd_bars,
            "commission": sum(r.state.commission for r in results), "slippage_cost": sum(r.state.slippage for r in results)}


def _require_aligned_daily(left: pd.Series, right: pd.Series) -> np.ndarray:
    if not left.index.is_unique or not right.index.is_unique or not left.index.equals(right.index):
        raise ValueError("paired daily returns require unique, exactly matching dates")
    if len(left) == 0 or not (left.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all():
        raise ValueError("paired daily returns require a continuous daily grid")
    values = (left - right).to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("paired daily returns contain NaN or infinity")
    return values


def block_summaries(left: pd.Series, right: pd.Series, block_days: int) -> list[dict[str, Any]]:
    diff = _require_aligned_daily(left, right)
    return [{"block": i // block_days, "start": left.index[i], "end": left.index[min(i + block_days, len(left)) - 1],
             "sum": float(diff[i:i + block_days].sum()), "count": len(diff[i:i + block_days])}
            for i in range(0, len(diff), block_days)]


def paired_block_bootstrap(left: pd.Series, right: pd.Series, block_days: int, repetitions: int = 5000,
                           seed: int = 42) -> dict[str, float]:
    diff = _require_aligned_daily(left, right)
    blocks = [diff[i:i + block_days] for i in range(0, len(diff), block_days)]
    rng = np.random.default_rng(seed); samples = np.empty(repetitions)
    for index in range(repetitions):
        chosen = rng.integers(0, len(blocks), size=len(blocks)); sample = np.concatenate([blocks[i] for i in chosen])
        samples[index] = sample.sum() / len(sample)
    return {"mean_difference": float(diff.mean()), "ci_lower": float(np.quantile(samples, .025)),
            "ci_upper": float(np.quantile(samples, .975)),
            "bootstrap_positive_fraction": float((samples > 0).mean())}


def _records(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8"); return
    clean = [{key: (value.isoformat() if isinstance(value, pd.Timestamp) else value) for key, value in row.items()}
             for row in rows]
    fieldnames = list(dict.fromkeys(key for row in clean for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames); writer.writeheader(); writer.writerows(clean)


def _window_return(equity: pd.DataFrame, start: str | pd.Timestamp, end: str | pd.Timestamp) -> float:
    """Return for source candles whose open times are in inclusive [start, end]."""
    start, end = pd.Timestamp(start), pd.Timestamp(end); marks = equity.set_index("time").equity
    if start not in marks.index or end + BAR not in marks.index:
        raise ValueError(f"missing exact boundary marks for {start} through {end}")
    return float(marks.loc[end + BAR] / marks.loc[start] - 1)


def _prepare_output(root: Path, output_dir: Path | None) -> Path:
    original = (root / "data/research" / ORIGINAL_NAMESPACE).resolve()
    target = (output_dir or root / "data/research" / CORRECTED_NAMESPACE).resolve()
    if target == original:
        raise FileExistsError("original M17.1 namespace is immutable")
    if target.exists():
        raise FileExistsError(f"refusing to overwrite research artifacts: {target}")
    target.mkdir(parents=True, exist_ok=False)
    return target


def run(root: Path | None = None, output_dir: Path | None = None) -> dict[str, Any]:
    root = root or Path(__file__).resolve().parents[2]
    protocol_path = root / "docs/M17_1_PROTOCOL.json"; protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    out = _prepare_output(root, output_dir)
    period_start, period_end = map(pd.Timestamp, protocol["data_period"]); frames = {}; audits = []
    for symbol in SYMBOLS:
        path = root / f"data/history/{symbol}_4h.csv"; frame = load_history(path)
        frame = frame[(frame.open_time >= period_start) & (frame.open_time <= period_end)].reset_index(drop=True)
        audit = validate_history(frame, symbol); audit.update(symbol=symbol, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        audits.append(audit); frames[symbol] = frame
    expected_times = frames[SYMBOLS[0]].open_time
    if any(not frame.open_time.equals(expected_times) for frame in frames.values()):
        raise ValueError("M17.1R requires a synchronized common 4h grid")
    start = pd.Timestamp(protocol["economic_start"]); sleeve_cash = INITIAL_CAPITAL / len(SYMBOLS)
    scenarios = {"zero": (0.0, 0.0), "base": (.0004, .0005), "double": (.0008, .001)}
    scenario_results, scenario_equity, scenario_metrics = {}, {}, {}
    for name, (commission, slippage) in scenarios.items():
        results = [simulate_sleeve(frames[s], s, sleeve_cash, commission, slippage, start) for s in SYMBOLS]
        equity = aggregate(results); scenario_results[name], scenario_equity[name] = results, equity
        scenario_metrics[name] = metrics(equity, results, INITIAL_CAPITAL)
    bh_results = [buy_and_hold(frames[s], s, sleeve_cash, start, .0004, .0005) for s in SYMBOLS]
    bh_equity = aggregate(bh_results); bh_metrics = metrics(bh_equity, bh_results, INITIAL_CAPITAL)
    base = scenario_equity["base"]; continuous_folds = []; restarted = []
    for fold in protocol["walk_forward_evaluations"]:
        continuous_folds.append({"fold": fold["fold"], "mode": "continuous", "start": fold["start"], "end": fold["end"],
                                 "net_return": _window_return(base, fold["start"], fold["end"])})
        results = [simulate_sleeve(frames[s], s, sleeve_cash, .0004, .0005,
                                   pd.Timestamp(fold["start"]), pd.Timestamp(fold["end"])) for s in SYMBOLS]
        equity = aggregate(results)
        restarted.append({"fold": fold["fold"], "mode": "restarted", "start": fold["start"], "end": fold["end"],
                          **metrics(equity, results, INITIAL_CAPITAL)})
    windows = [{"window": name, "start": bounds[0], "end": bounds[1], "net_return": _window_return(base, *bounds)}
               for name, bounds in protocol["splits"].items()]
    base_daily, bh_daily = daily_returns(base), daily_returns(bh_equity); cash_daily = base_daily * 0
    bootstrap, blocks = [], []
    for days in (14, 7, 28):
        for comparison, right in (("buy_and_hold", bh_daily), ("cash", cash_daily)):
            bootstrap.append({"period": "full_history", "comparison": comparison, "block_days": days,
                              **paired_block_bootstrap(base_daily, right, days)})
            blocks.extend({"period": "full_history", "comparison": comparison, "block_days": days, **row}
                          for row in block_summaries(base_daily, right, days))
    symbols = []
    for symbol, result in zip(SYMBOLS, scenario_results["base"]):
        equity = pd.DataFrame(result.equity)
        symbols.append({"symbol": symbol, **metrics(equity, [result], sleeve_cash),
                        "contribution": result.state.cash - sleeve_cash})
    test_return = next(row["net_return"] for row in windows if row["window"] == "test")
    primary_ci = next(row for row in bootstrap if row["comparison"] == "cash" and row["block_days"] == 14)
    other_contribution = sum(x["contribution"] for x in symbols) - max(x["contribution"] for x in symbols)
    criteria = {"test_positive": test_return > 0, "daily_ci_positive": primary_ci["ci_lower"] > 0,
                "two_of_three_windows_positive": sum(x["net_return"] > 0 for x in continuous_folds) >= 2,
                "double_cost_positive": scenario_metrics["double"]["net_return"] > 0,
                "other_sleeves_positive": other_contribution > 0}
    promising = all(criteria.values()); some_positive = any((criteria["test_positive"],
                                                              scenario_metrics["base"]["net_return"] > 0,
                                                              criteria["two_of_three_windows_positive"]))
    status = "PROMISING_ECONOMIC_HYPOTHESIS" if promising else ("WEAK" if some_positive else "NO_ECONOMIC_EDGE")
    old_path = root / f"data/research/{ORIGINAL_NAMESPACE}/summary.json"; old = json.loads(old_path.read_text(encoding="utf-8"))
    summary = {"status": status, "revision": "M17.1R",
               "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
               "event_time_convention": "prior close/signal -> same-boundary next-open fill -> next close mark",
               "period": [base.time.iloc[0].isoformat(), base.time.iloc[-1].isoformat()],
               "scenario_metrics": scenario_metrics, "buy_and_hold": bh_metrics, "windows": windows,
               "continuous_folds": continuous_folds, "restarted_folds": restarted, "bootstrap": bootstrap,
               "symbols": symbols, "criteria": criteria, "data_audit": audits,
               "old_summary_sha256": hashlib.sha256(old_path.read_bytes()).hexdigest(),
               "old_to_corrected": {
                   "base_net_return": [old["scenario_metrics"]["base"]["net_return"], scenario_metrics["base"]["net_return"]],
                   "test_net_return": [next(x["net_return"] for x in old["windows"] if x["window"] == "test"), test_return],
                   "base_max_drawdown": [old["scenario_metrics"]["base"]["max_drawdown"], scenario_metrics["base"]["max_drawdown"]],
                   "base_mean_daily_return": [old["scenario_metrics"]["base"]["mean_daily_return"], scenario_metrics["base"]["mean_daily_return"]]}}
    _records(out / "signals.csv", [x for r in scenario_results["base"] for x in r.signals])
    _records(out / "fills.csv", [x for r in scenario_results["base"] for x in r.fills])
    _records(out / "trades.csv", [x for r in scenario_results["base"] for x in r.trades])
    _records(out / "sleeve_equity.csv", [x for r in scenario_results["base"] for x in r.equity])
    base.to_csv(out / "portfolio_equity.csv", index=False)
    pd.DataFrame({"strategy": base_daily, "buy_and_hold": bh_daily, "cash": cash_daily}).to_csv(out / "daily_returns.csv")
    pd.DataFrame(windows + continuous_folds).to_csv(out / "window_results.csv", index=False)
    pd.DataFrame(restarted).to_csv(out / "restarted_folds.csv", index=False); pd.DataFrame(symbols).to_csv(out / "symbol_results.csv", index=False)
    pd.DataFrame([{"scenario": k, **v} for k, v in scenario_metrics.items()]).to_csv(out / "cost_scenarios.csv", index=False)
    pd.DataFrame([{"baseline": "buy_and_hold", **bh_metrics}, {"baseline": "cash", "ending_equity": INITIAL_CAPITAL,
                  "net_return": 0.0, "annualized_return": 0.0, "mean_daily_return": 0.0, "trades": 0}]).to_csv(out / "baselines.csv", index=False)
    pd.DataFrame(bootstrap).to_csv(out / "bootstrap.csv", index=False); pd.DataFrame(blocks).to_csv(out / "bootstrap_blocks.csv", index=False)
    pd.DataFrame(audits).to_csv(out / "data_audit.csv", index=False); (out / "protocol.json").write_bytes(protocol_path.read_bytes())
    (out / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=True, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M17.1R into a new immutable research namespace")
    parser.add_argument("--root", type=Path); parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(); summary = run(args.root, args.output_dir)
    print(json.dumps({"status": summary["status"], "output": CORRECTED_NAMESPACE,
                      "scenario_metrics": summary["scenario_metrics"]}, indent=2))


if __name__ == "__main__":
    main()
