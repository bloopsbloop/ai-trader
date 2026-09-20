from app.core.exchange import client
from app.core.data_provider import DataProvider
from app.strategies.ema_strategy import EMAStrategy
from app.config import Settings
from app.backtest.engine import BacktestEngine


class Application:

    def __init__(self):
        self.data = DataProvider()
        self.strategy = EMAStrategy()

    def run(self):

        candles = self.data.get_candles(
            symbol=Settings.SYMBOL,
            interval=client.KLINE_INTERVAL_1HOUR,
            limit=Settings.CANDLES
        )

        backtest = BacktestEngine(self.strategy)
        backtest.run(candles)