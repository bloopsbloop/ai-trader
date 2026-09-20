from app.core.market import MarketData
from app.core.exchange import client
from app.indicators.indicators import Indicators
from app.strategies.ema_strategy import EMAStrategy
from app.indicators.ema import EMA
from app.config import Settings
from app.backtest.engine import BacktestEngine

class Application:

    def __init__(self):
        self.market = MarketData()
        self.strategy = EMAStrategy()

    def run(self):

        candles = self.market.get_klines(
            symbol=Settings.SYMBOL,
            interval=client.KLINE_INTERVAL_1HOUR,
            limit=Settings.CANDLES
        )

        prices = [c.close for c in candles]

        price = candles[-1].close


        ema20 = EMA.calculate(prices, 20)

        signal = self.strategy.analyze(prices)



        backtest = BacktestEngine(self.strategy)
        backtest.run(candles)