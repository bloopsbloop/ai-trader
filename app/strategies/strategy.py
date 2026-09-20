class Strategy:

    def analyze(self, price: float, sma: float):

        if price > sma:
            return "BUY"

        elif price < sma:
            return "SELL"

        else:
            return "WAIT"