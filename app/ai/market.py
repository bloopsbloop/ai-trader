"""Read-only public market observations and multi-timeframe features."""
from datetime import datetime, timedelta, timezone
import math
import statistics
import requests

from app.models.candle import Candle
from app.ai.news import utc
from app.ai.http import PublicSession

INTERVALS = {"15m": 900, "1h": 3600, "4h": 14400}
STABLES = {"USDC", "USDT", "TUSD", "FDUSD", "DAI", "USDP", "EUR", "EURI", "USTC"}


def finite(value, positive=False):
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError("Invalid market number")
    return value


def select_universe(info, tickers, limit=30, min_volume=10_000_000):
    valid = {row["symbol"] for row in info["symbols"] if row.get("quoteAsset") == "USDT"
             and row.get("status") == "TRADING" and row.get("isSpotTradingAllowed") is True
             and row.get("baseAsset") not in STABLES
             and not row.get("baseAsset", "").endswith(("UP", "DOWN", "BULL", "BEAR"))}
    result = []
    for row in tickers:
        try:
            volume = finite(row["quoteVolume"], True)
            if row["symbol"] in valid and volume >= min_volume:
                result.append({"symbol": row["symbol"], "quote_volume": volume})
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(result, key=lambda row: (-row["quote_volume"], row["symbol"]))[:limit]


def features(candles, interval, cutoff):
    seconds = INTERVALS[interval]
    cutoff = utc(cutoff)
    bars = sorted((c for c in candles if utc(c.open_time) + timedelta(seconds=seconds) <= cutoff), key=lambda c: utc(c.open_time))[-50:]
    if len(bars) < 30:
        raise ValueError("Insufficient closed bars")
    if (cutoff - utc(bars[-1].open_time)).total_seconds() >= 2 * seconds:
        raise ValueError("Stale candles")
    for index, bar in enumerate(bars):
        for value in (bar.open, bar.high, bar.low, bar.close):
            finite(value, True)
        if not math.isfinite(bar.volume) or bar.volume < 0 or not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError("Invalid OHLCV")
        if index and (utc(bar.open_time) - utc(bars[index - 1].open_time)).total_seconds() != seconds:
            raise ValueError("Duplicate or missing bars")
    closes = [bar.close for bar in bars]
    average_volume = statistics.mean(bar.volume for bar in bars[-21:-1])
    if average_volume <= 0:
        raise ValueError("No volume")
    returns = [b / a - 1 for a, b in zip(closes, closes[1:])]
    mean = statistics.mean(closes[-20:])
    ranges = [max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close)) for a, b in zip(bars[-15:-1], bars[-14:])]
    return {"return": returns[-1], "momentum": closes[-1] / closes[-5] - 1,
            "trend": closes[-1] / mean - 1, "volume_ratio": bars[-1].volume / average_volume,
            "volatility": statistics.pstdev(returns[-20:]), "atr_fraction": statistics.mean(ranges) / closes[-1],
            "last_close": closes[-1], "closed_at": (utc(bars[-1].open_time) + timedelta(seconds=seconds)).isoformat()}


class PublicScannerData:
    BASE = "https://data-api.binance.vision/api/v3/"
    ALLOWED = {"exchangeInfo", "ticker/24hr", "ticker/bookTicker", "klines"}

    def __init__(self, session=None):
        self.session = session or PublicSession()

    def _get(self, endpoint, **params):
        if endpoint not in self.ALLOWED:
            raise ValueError("Only public market data endpoints are allowed")
        response = self.session.get(self.BASE + endpoint, params=params, timeout=(5, 15))
        response.raise_for_status()
        return response.json()

    def universe(self, limit=30):
        info = self._get("exchangeInfo")
        tickers = self._get("ticker/24hr")
        return select_universe(info, tickers, limit)

    def candles(self, symbol, interval, cutoff):
        # The heuristic still consumes its latest 50 bars. The longer shared window
        # gives the shadow model the same indicator warm-up definitions as training.
        rows = self._get("klines", symbol=symbol, interval=interval, limit=250, endTime=int(utc(cutoff).timestamp() * 1000))
        return [Candle(datetime.fromtimestamp(row[0] / 1000, timezone.utc), *(float(row[i]) for i in range(1, 6))) for row in rows]

    def quote(self, symbol):
        row = self._get("ticker/bookTicker", symbol=symbol)
        bid, ask = finite(row["bidPrice"], True), finite(row["askPrice"], True)
        if ask < bid or finite(row["bidQty"], True) <= 0 or finite(row["askQty"], True) <= 0:
            raise ValueError("Invalid quote")
        return {"bid": bid, "ask": ask, "observed_at": datetime.now(timezone.utc).isoformat()}
