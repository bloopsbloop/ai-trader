from dataclasses import dataclass
from datetime import datetime


@dataclass
class Trade:
    entry: float
    exit: float

    profit: float
    profit_percent: float
    quantity: float = 0.0
    commission: float = 0.0
    exit_reason: str = "signal"
    entry_time: datetime | None = None
    exit_time: datetime | None = None
