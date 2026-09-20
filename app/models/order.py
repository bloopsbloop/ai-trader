from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    id: str = field(default_factory=lambda: str(uuid4()))
    status: OrderStatus = OrderStatus.FILLED
    commission: float = 0.0
    rejection_reason: str | None = None
    entry_price: float | None = None
    realized_pnl: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def notional(self):
        return self.price * self.quantity
