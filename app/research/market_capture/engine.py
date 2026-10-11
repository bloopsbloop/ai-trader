from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Iterable

from .book import BookState, QualityCounters
from .envelope import Clock, ControlRecord, RawEnvelope
from .queueing import BackpressureError, BoundedEventQueue
from .storage import BoundedRunWriter, FreeSpaceError, RecordTooLargeError, StorageLimitError
from .transport import MockTransport, TransportMessage


@dataclass(frozen=True)
class CaptureConfig:
    symbol: str = "BTCUSDT"
    timestamp_unit: str = "MILLISECOND"
    checkpoint_limit: int = 32
    buffer_limit: int = 256
    queue_capacity: int = 64
    utc_step_tolerance_ns: int = 1_000_000_000
    snapshot_retry_limit: int = 3
    max_record_bytes: int = 1024 * 1024

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "CaptureConfig":
        return cls(**value)  # type: ignore[arg-type]


@dataclass
class CaptureReport:
    status: str
    processing_mode: str
    run_outcome: str
    stop_reason: str
    stop_reason_source: str
    data_quality: str
    integrity_status: str
    counters: dict[str, int]
    rejection_reasons: dict[str, int]
    book: dict[str, object]
    valid_duration_ns: int
    invalid_duration_ns: int
    unknown_duration_intervals: int
    measured_duration_ns: int
    comparable_status: str
    comparable_share: dict[str, int]
    mismatch_rate: dict[str, int]
    observed_sequence_continuity: dict[str, int]
    sequence_observation_coverage: dict[str, int]
    book_invariants: dict[str, int]
    state_transitions: list[dict[str, object]]
    session_boundaries: list[str]
    effective_config: dict[str, object]
    bytes_written: int = 0
    partial_tail_detected: bool = False
    replay_verification: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]: return asdict(self)


class CaptureEngine:
    def __init__(self, config: CaptureConfig, *, writer: BoundedRunWriter | None = None,
                 processing_mode: str = "mock_capture") -> None:
        self.config = config; self.writer = writer; self.processing_mode = processing_mode
        self.book = BookState(checkpoint_limit=config.checkpoint_limit, buffer_limit=config.buffer_limit)
        self.counters = QualityCounters(); self.rejections: dict[str, int] = {}
        self.session_boundaries: list[str] = []; self._capture_sequences: dict[str, int] = {}
        self._observed_sequences: dict[str, int] = {}; self._current_session: str | None = None
        self._last_utc_ns: int | None = None; self._last_mono_ns: int | None = None
        self.valid_duration_ns = 0; self.invalid_duration_ns = 0; self.unknown_duration_intervals = 0
        self.stop_reason = "input_exhausted"; self.stop_reason_source = "runtime"
        self._stop_requested = False; self.snapshot_attempts = 0
        self.state_transitions: list[dict[str, object]] = [{"record": 0, "valid": False,
            "reason": "awaiting_snapshot", "update_id": None}]

    def request_stop(self) -> None: self._stop_requested = True

    def _reject(self, reason: str) -> None:
        self.counters.rejected += 1; self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def _transition(self, before: tuple[bool, str | None, int | None]) -> None:
        after = (self.book.valid, self.book.invalid_reason, self.book.update_id)
        if after[:2] != before[:2]:
            self.state_transitions.append({"record": self.counters.received, "valid": after[0],
                                           "reason": after[1], "update_id": after[2]})

    def make_envelope(self, message: TransportMessage, clock: Clock) -> RawEnvelope:
        utc, mono = clock.sample(); seq = self._capture_sequences.get(message.session_id, 0) + 1
        self._capture_sequences[message.session_id] = seq
        return RawEnvelope(message.session_id, seq, message.stream, "mock", utc, mono,
                           message.payload_raw, clock_kind=clock.kind,
                           timestamp_unit=self.config.timestamp_unit)

    @staticmethod
    def _utc_ns(value: str) -> int:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000_000_000)

    def _advance_clock(self, utc: str, mono: int) -> bool:
        utc_ns = self._utc_ns(utc)
        if self._last_mono_ns is not None:
            if mono < self._last_mono_ns:
                before = (self.book.valid, self.book.invalid_reason, self.book.update_id)
                self.unknown_duration_intervals += 1; self.counters.monotonic_discontinuities += 1
                self.book.invalidate("monotonic_discontinuity"); self._transition(before)
                self._last_utc_ns, self._last_mono_ns = utc_ns, mono
                return False
            delta = mono - self._last_mono_ns
            if self.book.valid: self.valid_duration_ns += delta
            else: self.invalid_duration_ns += delta
            utc_delta = utc_ns - (self._last_utc_ns if self._last_utc_ns is not None else utc_ns)
            if abs(utc_delta - delta) > self.config.utc_step_tolerance_ns: self.counters.utc_steps += 1
        self._last_utc_ns, self._last_mono_ns = utc_ns, mono
        return True

    def _invalidate_rejected_market_state(self, stream: str, reason: str) -> None:
        if stream in {"depth", "snapshot"}:
            self.book.invalidate(reason)

    def process_envelope(self, envelope: RawEnvelope, *, persist: bool = True) -> None:
        self.counters.received += 1
        if persist and self.writer:
            self.writer.append(envelope); self.counters.persisted += 1
        if not self._advance_clock(envelope.receive_utc, envelope.receive_monotonic_ns):
            self._reject("monotonic_discontinuity"); return

        if self._current_session != envelope.session_id:
            if self._current_session is not None:
                before = (self.book.valid, self.book.invalid_reason, self.book.update_id)
                self.book.start_epoch("session_change_requires_snapshot"); self._transition(before)
            self._current_session = envelope.session_id; self.session_boundaries.append(envelope.session_id)
        expected = self._observed_sequences.get(envelope.session_id, 0) + 1
        if envelope.receive_sequence != expected:
            before = (self.book.valid, self.book.invalid_reason, self.book.update_id)
            self.counters.receive_sequence_errors += 1
            self.book.invalidate("receive_sequence_error"); self._transition(before)
            self._observed_sequences[envelope.session_id] = envelope.receive_sequence
            self._reject("receive_sequence_error"); return
        self._observed_sequences[envelope.session_id] = envelope.receive_sequence

        try: payload = json.loads(envelope.payload_raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            before = (self.book.valid, self.book.invalid_reason, self.book.update_id)
            self._invalidate_rejected_market_state(envelope.stream, "rejected_malformed_payload")
            self._transition(before); self._reject("malformed_payload"); return
        if not isinstance(payload, dict):
            self._reject("payload_not_object"); return
        before = (self.book.valid, self.book.invalid_reason, self.book.update_id)
        try: self._validate_and_apply(envelope.stream, payload)
        except (KeyError, TypeError, ValueError, BufferError) as exc:
            self._invalidate_rejected_market_state(envelope.stream, "rejected_" + envelope.stream)
            self._reject(type(exc).__name__ + ":" + str(exc))
        finally: self._transition(before)

    def _validate_time(self, value: object) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("exchange timestamp must be non-negative integer")
        if self.config.timestamp_unit == "MILLISECOND" and value > 9_999_999_999_999:
            raise ValueError("timestamp inconsistent with MILLISECOND configuration")
        if self.config.timestamp_unit == "MICROSECOND" and value < 100_000_000_000_000:
            raise ValueError("timestamp inconsistent with MICROSECOND configuration")

    def _symbol(self, payload: dict[str, object], snapshot: bool = False) -> None:
        key = "symbol" if snapshot else "s"
        if payload[key] != self.config.symbol: raise ValueError("wrong symbol")

    def _depth_outcome(self, outcome: str) -> None:
        if outcome == "applied":
            self.counters.depth_applied += 1; self.counters.sequence_checks += 1; self.counters.sequence_passes += 1
        elif outcome == "buffered": self.counters.depth_buffered += 1
        elif outcome == "buffered_invalid": self.counters.depth_invalid_buffered += 1
        elif outcome == "stale":
            self.counters.depth_stale += 1; self.counters.sequence_checks += 1; self.counters.sequence_passes += 1
        elif outcome == "duplicate":
            self.counters.depth_duplicates += 1; self.counters.sequence_checks += 1; self.counters.sequence_passes += 1
        elif outcome == "gap": self.counters.sequence_checks += 1; self.counters.sequence_gaps += 1

    def _validate_and_apply(self, stream: str, p: dict[str, object]) -> None:
        if stream == "trade":
            self._symbol(p); [p[k] for k in ("E", "T", "t", "p", "q", "m")]
            self._validate_time(p["E"]); self._validate_time(p["T"]); return
        if stream == "depth":
            self.counters.depth_observations += 1
            self._symbol(p); [p[k] for k in ("E", "U", "u", "b", "a")]; self._validate_time(p["E"])
            self._depth_outcome(self.book.apply_depth(p)); return
        if stream == "snapshot":
            self._symbol(p, True); [p[k] for k in ("lastUpdateId", "bids", "asks")]
            self.snapshot_attempts += 1
            if self.snapshot_attempts > self.config.snapshot_retry_limit:
                self.counters.snapshot_retries_exhausted += 1
                raise ValueError("snapshot retry exhaustion")
            outcomes = self.book.load_snapshot(p); self.snapshot_attempts = 0
            for outcome in outcomes: self._depth_outcome(outcome)
            return
        if stream == "bookTicker":
            self._symbol(p); [p[k] for k in ("u", "b", "B", "a", "A")]
            category = self.book.compare_ticker(p)
            setattr(self.counters, category, getattr(self.counters, category) + 1); return
        raise ValueError("unknown stream")

    def process_control(self, control: ControlRecord) -> None:
        if control.kind != "stop": raise ValueError("unknown control")
        if not self._advance_clock(control.receive_utc, control.receive_monotonic_ns):
            self._reject("control_monotonic_discontinuity")
        self.stop_reason = str(control.detail["reason"]); self.stop_reason_source = "control_record"

    def _persist_stop(self, utc: str, mono: int, clock_kind: str) -> None:
        control = ControlRecord("stop", utc, mono, {"reason": self.stop_reason}, clock_kind=clock_kind)
        if not self._advance_clock(control.receive_utc, control.receive_monotonic_ns):
            self._reject("control_monotonic_discontinuity")
        if self.writer:
            self.writer.append(control); self.counters.controls_persisted += 1

    def run(self, transport: MockTransport, clock: Clock, *, max_messages: int | None = None,
            max_duration_ns: int | None = None, stop_clock: Clock | None = None) -> CaptureReport:
        queue: BoundedEventQueue[RawEnvelope] = BoundedEventQueue(self.config.queue_capacity)
        started: int | None = None; stop_sample: tuple[str, int] | None = None
        try:
            for index, message in enumerate(transport):
                if self._stop_requested: self.stop_reason = "user_request"; break
                if max_messages is not None and index >= max_messages:
                    self.stop_reason = "message_limit"; break
                envelope = self.make_envelope(message, clock)
                if started is None: started = envelope.receive_monotonic_ns
                if max_duration_ns is not None and envelope.receive_monotonic_ns - started >= max_duration_ns:
                    self.stop_reason = "time_limit"; stop_sample = (envelope.receive_utc, envelope.receive_monotonic_ns); break
                try: queue.put_nowait(envelope)
                except BackpressureError:
                    self.counters.queue_saturation += 1; self.stop_reason = "backpressure"; break
                self.process_envelope(queue.get_nowait())
            if stop_clock is not None: stop_sample = stop_clock.sample()
            elif stop_sample is None and self._last_mono_ns is not None and self._last_utc_ns is not None:
                stop_sample = (datetime.fromtimestamp(self._last_utc_ns / 1e9, timezone.utc).isoformat(), self._last_mono_ns)
            if stop_sample is not None: self._persist_stop(*stop_sample, getattr(stop_clock or clock, "kind", "synthetic"))
        except StorageLimitError: self.stop_reason = "storage_cap"
        except FreeSpaceError: self.stop_reason = "minimum_free_space"
        except RecordTooLargeError: self.stop_reason = "record_too_large"
        except OSError:
            self.stop_reason = "disk_write_failure"
            if self.writer: self.writer.abort()
        return self.report()

    def report(self) -> CaptureReport:
        eligible = self.counters.comparable + self.counters.unmatched + self.counters.stale + self.counters.inconsistent
        exact = self.counters.comparable + self.counters.inconsistent
        outcome = "completed" if self.stop_reason == "input_exhausted" else "stopped"
        return CaptureReport(
            status=outcome.upper(), processing_mode=self.processing_mode, run_outcome=outcome,
            stop_reason=self.stop_reason, stop_reason_source=self.stop_reason_source,
            data_quality=("INVALID" if not self.book.valid else
                          "VALID_WITH_REJECTIONS" if self.counters.rejected else "VALID"),
            integrity_status="CAPTURE_NOT_REPLAY_VERIFIED",
            counters=self.counters.as_dict(), rejection_reasons=dict(self.rejections), book=self.book.serializable(),
            valid_duration_ns=self.valid_duration_ns, invalid_duration_ns=self.invalid_duration_ns,
            unknown_duration_intervals=self.unknown_duration_intervals,
            measured_duration_ns=self.valid_duration_ns + self.invalid_duration_ns,
            comparable_status="NOT_EVALUABLE" if exact == 0 else "EVALUABLE",
            comparable_share={"numerator": exact, "denominator": eligible},
            mismatch_rate={"numerator": self.counters.inconsistent, "denominator": exact},
            observed_sequence_continuity={"numerator": self.counters.sequence_passes,
                                          "denominator": self.counters.sequence_checks,
                                          "gaps": self.counters.sequence_gaps},
            sequence_observation_coverage={"numerator": self.counters.sequence_checks,
                                           "denominator": self.counters.depth_observations},
            book_invariants={"numerator": self.book.invariant_checks - self.book.invariant_failures,
                             "denominator": self.book.invariant_checks, "failures": self.book.invariant_failures},
            state_transitions=list(self.state_transitions), session_boundaries=list(self.session_boundaries),
            effective_config=asdict(self.config), bytes_written=self.writer.bytes_written if self.writer else 0)

    def finalize(self) -> CaptureReport:
        report = self.report()
        if self.writer:
            self.writer.finalize(report.as_dict(), report.stop_reason, asdict(self.config))
            report.bytes_written = self.writer.bytes_written; report.integrity_status = "FINALIZED_CAPTURE"
        return report
