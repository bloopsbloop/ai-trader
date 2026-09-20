from datetime import datetime

from app.core.exchange import client
from app.models.candle import Candle


class MarketData:

    def get_price(self, symbol: str):
        return float(client.get_symbol_ticker(symbol=symbol)["price"])

    def get_klines(self, symbol: str, interval, limit=100):

        data = client.get_klines(
            symbol=symbol,
            interval=interval,
            limit=limit
        )

        candles = []

        for c in data:

            candles.append(
                Candle(
                    open_time=datetime.fromtimestamp(c[0] / 1000),
                    open=float(c[1]),
                    high=float(c[2]),
                    low=float(c[3]),
                    close=float(c[4]),
                    volume=float(c[5]),
                )
            )

        return candles