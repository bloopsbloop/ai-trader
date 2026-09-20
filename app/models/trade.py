from dataclasses import dataclass


@dataclass
class Trade:
    entry: float
    exit: float

    profit: float

    profit_percent: float