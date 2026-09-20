from typing import List


class Indicators:

    @staticmethod
    def sma(prices: List[float], period: int):

        if len(prices) < period:
            return None

        return sum(prices[-period:]) / period