from dataclasses import dataclass


@dataclass
class Settings:
    SYMBOL = "BTCUSDT"

    INTERVAL = "1h"

    CANDLES = 500

    START_BALANCE = 1000

    RISK_PER_TRADE = 0.02