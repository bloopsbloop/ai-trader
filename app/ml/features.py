"""Vectorized candle features. Every output row uses bars closed by its timestamp."""
from __future__ import annotations

import numpy as np
import pandas as pd

INTERVAL_MINUTES = {"15m": 15, "1h": 60, "4h": 240}
FEATURE_VERSION = "market-v1"


def _ema(series, period):
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _rsi(close, period=14):
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative = gain / loss.replace(0, np.nan)
    result = 100 - 100 / (1 + relative)
    return result.mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)


def _atr(frame, period=14):
    previous = frame["close"].shift(1)
    true_range = pd.concat(((frame["high"] - frame["low"]),
                            (frame["high"] - previous).abs(),
                            (frame["low"] - previous).abs()), axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(frame, period=14):
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = _atr(frame, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _supertrend_state(frame, period=10, multiplier=3.0):
    atr = _atr(frame, period).to_numpy()
    high, low, close = (frame[name].to_numpy() for name in ("high", "low", "close"))
    upper = (high + low) / 2 + multiplier * atr
    lower = (high + low) / 2 - multiplier * atr
    final_upper, final_lower = upper.copy(), lower.copy()
    state = np.full(len(frame), np.nan)
    bullish = True
    for index in range(1, len(frame)):
        if np.isnan(atr[index]):
            continue
        if np.isnan(final_upper[index - 1]) or upper[index] < final_upper[index - 1] or close[index - 1] > final_upper[index - 1]:
            final_upper[index] = upper[index]
        else:
            final_upper[index] = final_upper[index - 1]
        if np.isnan(final_lower[index - 1]) or lower[index] > final_lower[index - 1] or close[index - 1] < final_lower[index - 1]:
            final_lower[index] = lower[index]
        else:
            final_lower[index] = final_lower[index - 1]
        if close[index] > final_upper[index]:
            bullish = True
        elif close[index] < final_lower[index]:
            bullish = False
        state[index] = 1.0 if bullish else 0.0
    return pd.Series(state, index=frame.index)


def candle_features(candles: pd.DataFrame, timeframe: str, _contiguous=False) -> pd.DataFrame:
    """Return features keyed by candle close time, never candle open time."""
    if timeframe not in INTERVAL_MINUTES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    required = {"open_time", "open", "high", "low", "close", "volume"}
    if not required.issubset(candles.columns):
        raise ValueError(f"Missing candle columns: {sorted(required - set(candles.columns))}")
    frame = candles[list(required)].copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame.sort_values("open_time")
    if frame["open_time"].duplicated().any():
        raise ValueError(f"Duplicate {timeframe} candles")
    expected = pd.Timedelta(minutes=INTERVAL_MINUTES[timeframe])
    differences = frame["open_time"].diff()
    if not _contiguous and not differences.dropna().eq(expected).all():
        groups = (differences.notna() & differences.ne(expected)).cumsum()
        return pd.concat((candle_features(group, timeframe, _contiguous=True)
                          for _, group in frame.groupby(groups, sort=False)), ignore_index=True)
    if _contiguous and not differences.dropna().eq(expected).all():
        raise ValueError(f"Internal irregular {timeframe} segment")
    numeric = ["open", "high", "low", "close", "volume"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    invalid = (~np.isfinite(frame[numeric])).any(axis=1) | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1) | (frame["volume"] < 0)
    frame = frame.loc[~invalid].copy()
    frame["timestamp"] = frame["open_time"] + pd.to_timedelta(INTERVAL_MINUTES[timeframe], unit="m")
    close, volume = frame["close"], frame["volume"]
    result = pd.DataFrame({"timestamp": frame["timestamp"], "source_open_time": frame["open_time"]})
    result["return_1"] = close.pct_change()
    result["log_return_1"] = np.log(close / close.shift(1))
    result["volatility_20"] = result["log_return_1"].rolling(20, min_periods=20).std(ddof=0)
    result["rsi_14"] = _rsi(close)
    atr = _atr(frame)
    result["atr_fraction_14"] = atr / close
    ema12, ema26 = _ema(close, 12), _ema(close, 26)
    macd = ema12 - ema26
    signal = _ema(macd, 9)
    result["macd_fraction"] = macd / close
    result["macd_hist_fraction"] = (macd - signal) / close
    result["adx_14"] = _adx(frame) / 100
    ema20, sma20 = _ema(close, 20), close.rolling(20, min_periods=20).mean()
    result["ema_distance_20"] = close / ema20 - 1
    result["sma_distance_20"] = close / sma20 - 1
    deviation = close.rolling(20, min_periods=20).std(ddof=0)
    width = (4 * deviation).replace(0, np.nan)
    result["bollinger_position_20"] = (close - (sma20 - 2 * deviation)) / width
    recent_high = frame["high"].rolling(20, min_periods=20).max()
    recent_low = frame["low"].rolling(20, min_periods=20).min()
    channel = (recent_high - recent_low).replace(0, np.nan)
    result["donchian_position_20"] = (close - recent_low) / channel
    result["distance_recent_high_20"] = close / recent_high - 1
    result["distance_recent_low_20"] = close / recent_low - 1
    result["supertrend_state"] = _supertrend_state(frame)
    result["volume_change"] = volume.pct_change().replace([np.inf, -np.inf], np.nan)
    volume_mean = volume.rolling(20, min_periods=20).mean()
    volume_std = volume.rolling(20, min_periods=20).std(ddof=0).replace(0, np.nan)
    result["volume_zscore_20"] = (volume - volume_mean) / volume_std
    result["high_low_range"] = (frame["high"] - frame["low"]) / close
    result["momentum_4"] = close / close.shift(4) - 1
    result["trend_slope_20"] = (close / close.shift(19) - 1) / 19
    result["close"] = close.to_numpy()
    return result.replace([np.inf, -np.inf], np.nan)


def merge_multitimeframe(frames: dict[str, pd.DataFrame], base_timeframe: str) -> pd.DataFrame:
    """Backward as-of joins ensure higher-timeframe bars were closed at T."""
    if set(frames) != set(INTERVAL_MINUTES):
        raise ValueError("15m, 1h and 4h feature frames are required")
    base = frames[base_timeframe].sort_values("timestamp").copy()
    base_columns = [column for column in base.columns if column not in {"source_open_time", "close"}]
    result = base[["timestamp", "source_open_time", "close"] + [c for c in base_columns if c != "timestamp"]].copy()
    rename = {column: f"{column}_{base_timeframe}" for column in base_columns if column != "timestamp"}
    result = result.rename(columns=rename)
    result[f"source_timestamp_{base_timeframe}"] = result["timestamp"]
    for timeframe in INTERVAL_MINUTES:
        if timeframe == base_timeframe:
            continue
        other = frames[timeframe].sort_values("timestamp")
        feature_columns = [c for c in other.columns if c not in {"source_open_time", "close", "timestamp"}]
        selected = other[["timestamp"] + feature_columns].copy()
        selected[f"source_timestamp_{timeframe}"] = selected["timestamp"]
        selected = selected.rename(columns={c: f"{c}_{timeframe}" for c in feature_columns})
        result = pd.merge_asof(result.sort_values("timestamp"), selected.sort_values("timestamp"), on="timestamp", direction="backward", allow_exact_matches=True)
    return result


def market_feature_names(frame: pd.DataFrame) -> list[str]:
    excluded = {"timestamp", "source_open_time", "close", "symbol", "timeframe", "future_close",
                "future_net_return", "target_timestamp", "label", "split"}
    return [column for column in frame.columns if column not in excluded and not column.startswith("source_timestamp_")]
