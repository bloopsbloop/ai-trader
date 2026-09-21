from datetime import datetime, timezone

import requests

from app.models.candle import Candle


class PublicMarketData:
    """Read-only Binance public market-data client; it has no trading endpoints."""

    BASE_URL = "https://data-api.binance.vision/api/v3/klines"
    INTERVAL_SECONDS = {"1h": 3600, "4h": 14400}

    def closed_candles(self, symbol, interval, limit=500, now=None):
        if interval not in self.INTERVAL_SECONDS:
            raise ValueError("Paper trading supports 1h and 4h")
        response = requests.get(self.BASE_URL, params={"symbol": symbol.upper(), "interval": interval, "limit": limit}, timeout=20)
        response.raise_for_status()
        now = now or datetime.now(timezone.utc)
        candles = []
        for row in response.json():
            close_time = datetime.fromtimestamp(row[6] / 1000, timezone.utc)
            if close_time >= now:
                continue
            candles.append(Candle(datetime.fromtimestamp(row[0] / 1000, timezone.utc).replace(tzinfo=None),
                                  float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])))
        return candles
