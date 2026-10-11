from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from app.research.market_capture.book import BookState
from app.research.market_capture.engine import CaptureConfig, CaptureEngine
from app.research.market_capture.envelope import RawEnvelope, SyntheticClock
from app.research.market_capture.queueing import BackpressureError, BoundedEventQueue
from app.research.market_capture.replay import replay_run
from app.research.market_capture.storage import (
    BoundedRunWriter, FreeSpaceError, StorageLimitError, read_jsonl_with_tail_status,
)
from app.research.market_capture.transport import (
    MockTransport, NetworkTransportForbidden, TransportMessage, create_transport,
)

BASE_MS = 1_800_000_000_000


def depth(U: int, u: int, bids=(), asks=(), E: int = BASE_MS) -> dict[str, object]:
    return {"e": "depthUpdate", "E": E, "s": "BTCUSDT", "U": U, "u": u,
            "b": list(bids), "a": list(asks)}


def snapshot(last: int = 100) -> dict[str, object]:
    return {"symbol": "BTCUSDT", "lastUpdateId": last,
            "bids": [["100.00", "2.0"], ["99.00", "3.0"]],
            "asks": [["101.00", "4.0"], ["102.00", "5.0"]]}


def ticker(u: int, b="100.00", B="2.0", a="101.00", A="4.0") -> dict[str, object]:
    return {"s": "BTCUSDT", "u": u, "b": b, "B": B, "a": a, "A": A}


def env(seq: int, stream: str, payload: object, mono: int | None = None,
        utc: str | None = None, session: str = "s1", unit: str = "MILLISECOND") -> RawEnvelope:
    return RawEnvelope(session, seq, stream, "mock", utc or f"2027-01-15T00:00:0{seq}Z",
                       mono if mono is not None else seq * 1_000_000_000,
                       payload if isinstance(payload, str) else json.dumps(payload),
                       timestamp_unit=unit)


def test_snapshot_race_overlap_and_predefined_book() -> None:
    book = BookState()
    assert book.apply_depth(depth(99, 102, asks=[["101.00", "1.5"]])) == "buffered"
    assert book.load_snapshot(snapshot()) == ["applied"]
    assert book.valid and book.update_id == 102
    assert book.serializable()["asks"][0] == ["101.00", "1.5"]


def test_stale_duplicate_overlap_gap_and_resnapshot() -> None:
    book = BookState(); book.load_snapshot(snapshot())
    assert book.apply_depth(depth(101, 102, bids=[["100.00", "1.0"]])) == "applied"
    assert book.apply_depth(depth(100, 101)) == "stale"
    assert book.apply_depth(depth(101, 102)) == "duplicate"
    assert book.apply_depth(depth(102, 104, asks=[["101.00", "0"]])) == "applied"
    assert book.update_id == 104
    assert book.apply_depth(depth(106, 106)) == "gap"
    assert not book.valid and book.invalid_reason == "sequence_gap"
    assert book.apply_depth(depth(105, 105)) == "buffered_invalid"
    book.load_snapshot({**snapshot(110), "asks": [["103", "2"]]})
    assert book.valid and book.update_id == 110


def test_decimal_keys_and_zero_removal_are_exact() -> None:
    book = BookState(); book.load_snapshot(snapshot())
    book.apply_depth(depth(101, 101, bids=[["100.00000000000000001", "7"]]))
    states = book.serializable()["bids"]
    assert ["100.00000000000000001", "7"] in states
    book.apply_depth(depth(102, 102, bids=[["100.00000000000000001", "0"]]))
    assert ["100.00000000000000001", "7"] not in book.serializable()["bids"]


def test_bookticker_categories_and_batching_low_exact_id_coverage() -> None:
    book = BookState(checkpoint_limit=2); book.load_snapshot(snapshot())
    book.apply_depth(depth(101, 103, asks=[["101", "3"]]))
    assert book.compare_ticker(ticker(102)) == "unmatched"  # intra-batch state does not exist
    assert book.compare_ticker(ticker(103, A="3")) == "comparable"
    assert book.compare_ticker(ticker(103, A="999")) == "inconsistent"
    book.apply_depth(depth(104, 104)); book.apply_depth(depth(105, 105))
    assert book.compare_ticker(ticker(100)) == "stale"
    # Correct streams can expose only one exact state among several asynchronously delivered tickers.
    categories = [book.compare_ticker(ticker(u, A="3") if u == 105 else ticker(u))
                  for u in (101, 102, 105, 106)]
    assert categories == ["stale", "stale", "comparable", "unmatched"]


def test_no_comparable_is_not_evaluable() -> None:
    engine = CaptureEngine(CaptureConfig())
    engine.process_envelope(env(1, "bookTicker", ticker(200)), persist=False)
    report = engine.report()
    assert report.comparable_status == "NOT_EVALUABLE"
    assert report.mismatch_rate == {"numerator": 0, "denominator": 0}
    assert report.comparable_share == {"numerator": 0, "denominator": 1}


@pytest.mark.parametrize("stream,payload,reason", [
    ("trade", "{broken", "malformed_payload"),
    ("unknown", {}, "unknown stream"),
    ("depth", {**depth(1, 1), "s": "ETHUSDT"}, "wrong symbol"),
    ("bookTicker", {"s": "BTCUSDT", "u": 1}, "'b'"),
])
def test_rejections_are_counted(stream: str, payload: object, reason: str) -> None:
    engine = CaptureEngine(CaptureConfig())
    engine.process_envelope(env(1, stream, payload), persist=False)
    assert engine.counters.rejected == 1
    assert any(reason in key for key in engine.rejections)


def test_timestamp_unit_validation_and_absent_bookticker_time() -> None:
    ms = CaptureEngine(CaptureConfig(timestamp_unit="MILLISECOND"))
    ms.process_envelope(env(1, "trade", {"s":"BTCUSDT","E":BASE_MS,"T":BASE_MS,"t":1,"p":"1","q":"1","m":False}), persist=False)
    assert ms.counters.rejected == 0
    micro = CaptureEngine(CaptureConfig(timestamp_unit="MICROSECOND"))
    p = {"s":"BTCUSDT","E":BASE_MS,"T":BASE_MS,"t":1,"p":"1","q":"1","m":False}
    micro.process_envelope(env(1, "trade", p, unit="MICROSECOND"), persist=False)
    assert micro.counters.rejected == 1
    micro.process_envelope(env(2, "bookTicker", ticker(1), unit="MICROSECOND"), persist=False)
    assert micro.counters.rejected == 1  # no synthetic E/T required


def test_utc_step_does_not_break_monotonic_order() -> None:
    engine = CaptureEngine(CaptureConfig(utc_step_tolerance_ns=100_000_000))
    engine.process_envelope(env(1, "snapshot", snapshot(), mono=1_000_000_000,
                                utc="2027-01-15T00:00:00Z"), persist=False)
    engine.process_envelope(env(2, "depth", depth(101, 101), mono=2_000_000_000,
                                utc="2027-01-15T00:00:10Z"), persist=False)
    assert engine.counters.utc_steps == 1
    assert engine.book.valid and engine.counters.rejected == 0


def test_bounded_queue_backpressure() -> None:
    queue: BoundedEventQueue[int] = BoundedEventQueue(1)
    queue.put_nowait(1)
    with pytest.raises(BackpressureError): queue.put_nowait(2)
    assert queue.get_nowait() == 1


def test_create_only_namespace_and_prewrite_cap(tmp_path: Path) -> None:
    writer = BoundedRunWriter(tmp_path, "run", max_total_bytes=500, reserve_final_bytes=100,
                              free_space=lambda _: 10_000)
    with pytest.raises(FileExistsError):
        BoundedRunWriter(tmp_path, "run", max_total_bytes=500, reserve_final_bytes=100)
    large = env(1, "bad", "x" * 1000)
    with pytest.raises(StorageLimitError): writer.append(large)
    assert writer.raw_path.stat().st_size == 0
    writer.abort()


def test_minimum_free_space_checked_before_write(tmp_path: Path) -> None:
    writer = BoundedRunWriter(tmp_path, "run", max_total_bytes=10_000, reserve_final_bytes=100,
                              min_free_bytes=100, free_space=lambda _: 100)
    with pytest.raises(FreeSpaceError): writer.append(env(1, "unknown", {}))
    writer.abort()


class FailingFile:
    def write(self, _: bytes) -> int: raise OSError("injected disk failure")
    def close(self) -> None: pass


def test_disk_write_failure_stops_engine(tmp_path: Path) -> None:
    writer = BoundedRunWriter(tmp_path, "run", max_total_bytes=10_000,
                              raw_opener=lambda *args: FailingFile(), free_space=lambda _: 99_999)
    clock = SyntheticClock((("2027-01-15T00:00:00Z", 1),))
    report = CaptureEngine(CaptureConfig(), writer=writer).run(
        MockTransport([TransportMessage("snapshot", json.dumps(snapshot()))]), clock)
    assert report.stop_reason == "disk_write_failure"
    writer.abort()


def test_partial_tail_is_detected_not_replayed(tmp_path: Path) -> None:
    path = tmp_path / "raw.jsonl"
    complete = env(1, "snapshot", snapshot()).to_json_line()
    path.write_bytes(complete + b'{"partial":')
    records, partial = read_jsonl_with_tail_status(path)
    assert partial and len(records) == 1


def test_mock_online_and_offline_replay_equivalence(tmp_path: Path) -> None:
    messages = [
        TransportMessage("depth", json.dumps(depth(101, 102, asks=[["101", "3"]]))),
        TransportMessage("snapshot", json.dumps(snapshot())),
        TransportMessage("bookTicker", json.dumps(ticker(102, A="3"))),
        TransportMessage("bookTicker", json.dumps(ticker(101))),
    ]
    samples = tuple((f"2027-01-15T00:00:0{i}Z", i * 1_000_000_000) for i in range(1, 5))
    writer = BoundedRunWriter(tmp_path, "run", max_total_bytes=100_000,
                              reserve_final_bytes=10_000, free_space=lambda _: 1_000_000)
    engine = CaptureEngine(CaptureConfig(), writer=writer)
    engine.run(MockTransport(messages), SyntheticClock(samples))
    online = engine.finalize()
    offline = replay_run(tmp_path / "run")
    assert offline.book == online.book
    assert offline.counters == online.counters
    assert offline.observed_sequence_continuity == online.observed_sequence_continuity
    assert offline.book_invariants == online.book_invariants
    assert offline.state_transitions == online.state_transitions
    assert offline.valid_duration_ns == online.valid_duration_ns
    assert offline.invalid_duration_ns == online.invalid_duration_ns
    assert offline.stop_reason == online.stop_reason == "input_exhausted"


def test_user_stop_reconnect_sessions_and_time_stop() -> None:
    engine = CaptureEngine(CaptureConfig()); engine.request_stop()
    report = engine.run(MockTransport([TransportMessage("snapshot", json.dumps(snapshot()))]),
                        SyntheticClock((("2027-01-15T00:00:00Z", 1),)))
    assert report.stop_reason == "user_request" and report.counters["received"] == 0
    engine = CaptureEngine(CaptureConfig())
    messages = [TransportMessage("snapshot", json.dumps(snapshot()), "s1"),
                TransportMessage("snapshot", json.dumps(snapshot(200)), "s2")]
    samples = (("2027-01-15T00:00:00Z", 1), ("2027-01-15T00:00:01Z", 2))
    report = engine.run(MockTransport(messages), SyntheticClock(samples))
    assert report.session_boundaries == ["s1", "s2"]

    engine = CaptureEngine(CaptureConfig())
    messages = [TransportMessage("snapshot", json.dumps(snapshot())),
                TransportMessage("depth", json.dumps(depth(101, 101)))]
    samples = (("2027-01-15T00:00:00Z", 1_000_000_000),
               ("2027-01-15T00:00:02Z", 3_000_000_000))
    report = engine.run(MockTransport(messages), SyntheticClock(samples),
                        max_duration_ns=2_000_000_000)
    assert report.stop_reason == "time_limit"
    assert report.counters["received"] == 1
    assert report.book["update_id"] == 100


def test_snapshot_retry_exhaustion_is_explicit() -> None:
    engine = CaptureEngine(CaptureConfig(snapshot_retry_limit=2))
    crossed = {**snapshot(), "asks": [["99", "1"]]}
    for i in range(1, 4): engine.process_envelope(env(i, "snapshot", crossed), persist=False)
    assert engine.counters.snapshot_retries_exhausted == 1
    assert engine.counters.rejected == 3


def test_network_transport_is_explicitly_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network attempted")))
    assert list(create_transport("mock", [])) == []
    with pytest.raises(NetworkTransportForbidden): create_transport("binance")
