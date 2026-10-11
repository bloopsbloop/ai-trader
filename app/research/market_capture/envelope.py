from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Protocol


class Clock(Protocol):
    kind: str
    def sample(self) -> tuple[str, int]: ...


@dataclass(frozen=True)
class SyntheticClock:
    samples: tuple[tuple[str, int], ...]
    kind: str = "synthetic"
    _index: int = 0

    def sample(self) -> tuple[str, int]:
        index = object.__getattribute__(self, "_index")
        if index >= len(self.samples): raise RuntimeError("synthetic clock exhausted")
        object.__setattr__(self, "_index", index + 1)
        return self.samples[index]


def _validate_clock(utc: str, monotonic_ns: int) -> None:
    if isinstance(monotonic_ns, bool) or not isinstance(monotonic_ns, int) or monotonic_ns < 0:
        raise ValueError("monotonic timestamp must be a non-negative integer")
    dt = datetime.fromisoformat(utc.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(dt):
        raise ValueError("receive_utc must be timezone-aware UTC")


@dataclass(frozen=True)
class RawEnvelope:
    session_id: str
    receive_sequence: int
    stream: str
    source: str
    receive_utc: str
    receive_monotonic_ns: int
    payload_raw: str
    schema_version: str = "m20.2r-envelope-v2"
    clock_kind: str = "synthetic"
    timestamp_unit: str = "MILLISECOND"
    record_type: str = "event"

    def __post_init__(self) -> None:
        if isinstance(self.receive_sequence, bool) or not isinstance(self.receive_sequence, int) or self.receive_sequence < 1:
            raise ValueError("receive sequence must be a positive integer")
        _validate_clock(self.receive_utc, self.receive_monotonic_ns)
        if self.timestamp_unit not in {"MILLISECOND", "MICROSECOND"}:
            raise ValueError("timestamp_unit must be MILLISECOND or MICROSECOND")
        if self.record_type != "event": raise ValueError("invalid event record type")

    def as_dict(self) -> dict[str, object]: return asdict(self)
    def to_json_line(self) -> bytes:
        return (json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode()

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RawEnvelope":
        cleaned = dict(value); cleaned.setdefault("record_type", "event")
        return cls(**cleaned)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ControlRecord:
    kind: str
    receive_utc: str
    receive_monotonic_ns: int
    detail: dict[str, object]
    schema_version: str = "m20.2r-control-v1"
    clock_kind: str = "synthetic"
    record_type: str = "control"

    def __post_init__(self) -> None:
        _validate_clock(self.receive_utc, self.receive_monotonic_ns)
        if self.kind not in {"stop"}: raise ValueError("unknown control kind")
        if self.record_type != "control": raise ValueError("invalid control record type")

    def as_dict(self) -> dict[str, object]: return asdict(self)
    def to_json_line(self) -> bytes:
        return (json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode()

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ControlRecord":
        return cls(**value)  # type: ignore[arg-type]