from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from uuid import uuid4


class ExperimentStatus(str, Enum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class PaperExperiment:
    symbol: str
    timeframe: str
    strategy: str
    strategy_parameters: dict
    starting_balance: float = 1000.0
    maker_commission: float = 0.0002
    taker_commission: float = 0.0004
    slippage: float = 0.0005
    position_size: float = 0.20
    max_position_fraction: float = 0.20
    experiment_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: ExperimentStatus = ExperimentStatus.RUNNING
    parameter_origin: str = "M6.5 development train/validation selection before forward experiment"
    batch_id: str = "batch_1"
    batch_name: str = "Baseline Control"

    def __post_init__(self):
        object.__setattr__(self, "strategy_parameters", MappingProxyType(dict(self.strategy_parameters)))

    def as_dict(self):
        return {"symbol": self.symbol, "timeframe": self.timeframe, "strategy": self.strategy,
                "strategy_parameters": dict(self.strategy_parameters), "starting_balance": self.starting_balance,
                "maker_commission": self.maker_commission, "taker_commission": self.taker_commission,
                "slippage": self.slippage, "position_size": self.position_size,
                "max_position_fraction": self.max_position_fraction, "experiment_id": self.experiment_id,
                "created_at": self.created_at, "status": self.status.value, "parameter_origin": self.parameter_origin,
                "batch_id": self.batch_id, "batch_name": self.batch_name}
