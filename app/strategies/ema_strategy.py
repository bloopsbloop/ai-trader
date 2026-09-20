from app.indicators.ema import EMA
from app.models.signal import Signal

class EMAStrategy:

    def analyze(self, prices):

        ema20 = EMA.calculate(prices, 20)
        ema50 = EMA.calculate(prices, 50)

        if ema20 is None or ema50 is None:
            return Signal.WAIT

        if ema20 > ema50:

            return Signal.BUY

        if ema20 < ema50:
            return Signal.SELL

        return Signal.WAIT