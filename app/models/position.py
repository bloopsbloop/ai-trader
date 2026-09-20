from dataclasses import dataclass


@dataclass
class Position:
    symbol: str
    entry: float
    quantity: float
    cost_basis: float
    entry_commission: float

    @property
    def amount(self):
        return self.cost_basis
