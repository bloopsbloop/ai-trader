from app.core.market import MarketData


class DataProvider:

    def __init__(self):
        self.market = MarketData()

    def get_candles(self, symbol, interval, limit):
        return self.market.get_klines(symbol, interval, limit)