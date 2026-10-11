from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _levels(rows: Iterable[Iterable[object]], *, allow_zero: bool) -> list[tuple[Decimal, Decimal]]:
    result: list[tuple[Decimal, Decimal]] = []
    for row in rows:
        values = list(row)
        if len(values) != 2:
            raise ValueError("book level must contain price and quantity")
        try:
            price, qty = Decimal(str(values[0])), Decimal(str(values[1]))
        except InvalidOperation as exc:
            raise ValueError("invalid decimal book level") from exc
        if (not price.is_finite() or not qty.is_finite() or price <= 0
                or qty < 0 or (not allow_zero and qty == 0)):
            raise ValueError("invalid book price or quantity")
        result.append((price, qty))
    return result


def _side(rows: Iterable[Iterable[object]]) -> dict[Decimal, Decimal]:
    return dict(_levels(rows, allow_zero=False))


def _check_book(bids: dict[Decimal, Decimal], asks: dict[Decimal, Decimal]) -> None:
    if bids and asks and max(bids) >= min(asks):
        raise ValueError("crossed book")


@dataclass
class QualityCounters:
    received: int = 0
    persisted: int = 0
    rejected: int = 0
    controls_persisted: int = 0
    depth_observations: int = 0
    depth_applied: int = 0
    depth_buffered: int = 0
    depth_invalid_buffered: int = 0
    depth_stale: int = 0
    depth_duplicates: int = 0
    sequence_checks: int = 0
    sequence_passes: int = 0
    sequence_gaps: int = 0
    snapshot_retries_exhausted: int = 0
    comparable: int = 0
    unmatched: int = 0
    stale: int = 0
    inconsistent: int = 0
    utc_steps: int = 0
    monotonic_discontinuities: int = 0
    receive_sequence_errors: int = 0
    queue_saturation: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class Checkpoint:
    update_id: int
    bid_price: Decimal | None
    bid_qty: Decimal | None
    ask_price: Decimal | None
    ask_qty: Decimal | None


class BookState:
    def __init__(self, *, checkpoint_limit: int = 32, buffer_limit: int = 256,
                 level_limit: int | None = None) -> None:
        if checkpoint_limit < 1 or buffer_limit < 1:
            raise ValueError("retention limits must be positive")
        if level_limit is not None and level_limit < 1:
            raise ValueError("book level limit must be positive")
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.update_id: int | None = None
        self.valid = False
        self.invalid_reason: str | None = "awaiting_snapshot"
        self.checkpoint_limit = checkpoint_limit
        self.buffer_limit = buffer_limit
        self.level_limit = level_limit
        self.buffer: deque[dict[str, object]] = deque()
        self.checkpoints: OrderedDict[int, Checkpoint] = OrderedDict()
        self.batch_ranges: deque[tuple[int, int]] = deque(maxlen=checkpoint_limit)
        self.known_snapshot_levels: int | None = None
        self.snapshot_bid_levels: int | None = None
        self.snapshot_ask_levels: int | None = None
        self.invariant_checks = 0
        self.invariant_failures = 0

    def start_epoch(self, reason: str = "awaiting_snapshot") -> None:
        self.bids.clear(); self.asks.clear(); self.buffer.clear(); self.checkpoints.clear(); self.batch_ranges.clear()
        self.update_id = None; self.known_snapshot_levels = None
        self.snapshot_bid_levels = None; self.snapshot_ask_levels = None
        self.valid = False; self.invalid_reason = reason

    def invalidate(self, reason: str) -> None:
        self.valid = False; self.invalid_reason = reason
        self.checkpoints.clear(); self.batch_ranges.clear()

    def _checkpoint(self) -> Checkpoint:
        bid = max(self.bids) if self.bids else None
        ask = min(self.asks) if self.asks else None
        return Checkpoint(self.update_id or 0, bid, self.bids.get(bid), ask, self.asks.get(ask))

    def _retain_checkpoint(self) -> None:
        assert self.update_id is not None
        self.checkpoints[self.update_id] = self._checkpoint()
        while len(self.checkpoints) > self.checkpoint_limit:
            self.checkpoints.popitem(last=False)

    def _buffer(self, event: dict[str, object]) -> None:
        if len(self.buffer) >= self.buffer_limit:
            self.invalidate("buffer_overflow")
            raise BufferError("depth buffer full")
        self.buffer.append(dict(event))

    @staticmethod
    def _depth_parts(event: dict[str, object]) -> tuple[int, int, list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        U, u = _integer(event["U"], "U"), _integer(event["u"], "u")
        if U > u:
            raise ValueError("U must not exceed u")
        bids = _levels(event["b"], allow_zero=True)  # type: ignore[arg-type]
        asks = _levels(event["a"], allow_zero=True)  # type: ignore[arg-type]
        return U, u, bids, asks

    def _apply_to(self, bids: dict[Decimal, Decimal], asks: dict[Decimal, Decimal],
                  bid_changes: list[tuple[Decimal, Decimal]], ask_changes: list[tuple[Decimal, Decimal]]) -> tuple[dict[Decimal, Decimal], dict[Decimal, Decimal]]:
        next_bids, next_asks = dict(bids), dict(asks)
        for side, changes in ((next_bids, bid_changes), (next_asks, ask_changes)):
            for price, qty in changes:
                if qty == 0: side.pop(price, None)
                else: side[price] = qty
        _check_book(next_bids, next_asks)
        if self.level_limit is not None and (len(next_bids) > self.level_limit
                                              or len(next_asks) > self.level_limit):
            raise BufferError("book level limit exceeded")
        return next_bids, next_asks

    def load_snapshot(self, snapshot: dict[str, object]) -> list[str]:
        last = _integer(snapshot["lastUpdateId"], "lastUpdateId")
        candidate_bids = _side(snapshot["bids"])  # type: ignore[arg-type]
        candidate_asks = _side(snapshot["asks"])  # type: ignore[arg-type]
        if self.level_limit is not None and (len(candidate_bids) > self.level_limit
                                              or len(candidate_asks) > self.level_limit):
            raise BufferError("snapshot exceeds book level limit")
        snapshot_bid_levels, snapshot_ask_levels = len(candidate_bids), len(candidate_asks)
        self.invariant_checks += 1
        try: _check_book(candidate_bids, candidate_asks)
        except ValueError:
            self.invariant_failures += 1
            raise
        outcomes: list[str] = []
        candidate_id = last
        candidate_ranges: list[tuple[int, int]] = []
        try:
            for event in self.buffer:
                U, u = _integer(event["U"], "U"), _integer(event["u"], "u")
                if U > u: raise ValueError("U must not exceed u")
                if u <= candidate_id:
                    outcomes.append("stale"); continue
                if U > candidate_id + 1 or not U <= candidate_id + 1 <= u:
                    raise ValueError("snapshot buffer sequence gap")
                _, _, bid_changes, ask_changes = self._depth_parts(event)
                self.invariant_checks += 1
                try: candidate_bids, candidate_asks = self._apply_to(candidate_bids, candidate_asks, bid_changes, ask_changes)
                except ValueError:
                    self.invariant_failures += 1
                    raise
                candidate_id = u; candidate_ranges.append((U, u)); outcomes.append("applied")
        except (KeyError, TypeError, ValueError):
            self.invalidate("snapshot_buffer_failure")
            raise
        self.bids, self.asks, self.update_id = candidate_bids, candidate_asks, candidate_id
        self.snapshot_bid_levels, self.snapshot_ask_levels = snapshot_bid_levels, snapshot_ask_levels
        self.known_snapshot_levels = max(snapshot_bid_levels, snapshot_ask_levels)
        self.valid, self.invalid_reason = True, None
        self.buffer.clear(); self.checkpoints.clear(); self.batch_ranges.clear()
        for item in candidate_ranges: self.batch_ranges.append(item)
        self._retain_checkpoint()
        return outcomes

    def apply_depth(self, event: dict[str, object]) -> str:
        U, u, bid_changes, ask_changes = self._depth_parts(event)
        if self.update_id is None:
            self._buffer(event); return "buffered"
        if not self.valid:
            self._buffer(event); return "buffered_invalid"
        if u < self.update_id: return "stale"
        if u == self.update_id: return "duplicate"
        if U > self.update_id + 1 or not U <= self.update_id + 1 <= u:
            self._buffer(event); self.invalidate("sequence_gap"); return "gap"
        self.invariant_checks += 1
        try: next_bids, next_asks = self._apply_to(self.bids, self.asks, bid_changes, ask_changes)
        except ValueError:
            self.invariant_failures += 1
            self.invalidate("rejected_depth")
            raise
        self.bids, self.asks, self.update_id = next_bids, next_asks, u
        self.batch_ranges.append((U, u)); self._retain_checkpoint()
        return "applied"

    @staticmethod
    def _ticker_decimal(value: object, *, price: bool) -> Decimal:
        try: number = Decimal(str(value))
        except InvalidOperation as exc: raise ValueError("invalid bookTicker numeric value") from exc
        if not number.is_finite() or number < 0 or (price and number <= 0):
            raise ValueError("invalid bookTicker numeric value")
        return number

    def compare_ticker(self, ticker: dict[str, object]) -> str:
        uid = _integer(ticker["u"], "u")
        actual = (self._ticker_decimal(ticker["b"], price=True), self._ticker_decimal(ticker["B"], price=False),
                  self._ticker_decimal(ticker["a"], price=True), self._ticker_decimal(ticker["A"], price=False))
        checkpoint = self.checkpoints.get(uid)
        if checkpoint is not None and self.valid:
            expected = (checkpoint.bid_price, checkpoint.bid_qty, checkpoint.ask_price, checkpoint.ask_qty)
            return "comparable" if actual == expected else "inconsistent"
        if any(U <= uid < u for U, u in self.batch_ranges): return "unmatched"
        if self.update_id is None or uid > self.update_id or not self.valid: return "unmatched"
        return "stale"

    def serializable(self) -> dict[str, object]:
        return {"valid": self.valid, "invalid_reason": self.invalid_reason, "update_id": self.update_id,
                "bids": [[str(p), str(self.bids[p])] for p in sorted(self.bids, reverse=True)],
                "asks": [[str(p), str(self.asks[p])] for p in sorted(self.asks)],
                "known_snapshot_levels": self.known_snapshot_levels, "buffered_depth": len(self.buffer)}

    def report_summary(self, *, top_levels: int = 5) -> dict[str, object]:
        if top_levels < 1: raise ValueError("top_levels must be positive")
        bids = sorted(self.bids, reverse=True)[:top_levels]
        asks = sorted(self.asks)[:top_levels]
        return {
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
            "update_id": self.update_id,
            "level_counts": {"bids": len(self.bids), "asks": len(self.asks)},
            "snapshot_level_counts": {
                "bids": self.snapshot_bid_levels,
                "asks": self.snapshot_ask_levels,
            },
            "top_bids": [[str(price), str(self.bids[price])] for price in bids],
            "top_asks": [[str(price), str(self.asks[price])] for price in asks],
            "top_levels_limit": top_levels,
            "buffered_depth": len(self.buffer),
            "book_level_limit_per_side": self.level_limit,
        }
