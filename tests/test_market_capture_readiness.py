from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.research.market_capture.book import BookState
from app.research.market_capture.comparability import audit_bookticker_comparability
from app.research.market_capture.engine import CaptureConfig, CaptureEngine
from app.research.market_capture.envelope import ControlRecord, RawEnvelope
from app.research.market_capture.host_space import HostSpaceUnavailableError, WindowsVhdxSpaceMonitor
from app.research.market_capture.process_wrapper import run_measured
from app.research.market_capture.public_adapter import (
    EXTENDED_CONFIRM, EXTENDED_HOST_RESERVE_BYTES, EXTENDED_MAX_BYTES, EXTENDED_MAX_NS,
    STAGE_A_CONFIRM, STAGE_A_MAX_BYTES, STAGE_A_MAX_NS, PublicMarketAdapter,
    SnapshotResponse, TransportDisconnected, TransportEnd, capture_profile, preflight,
)
from app.research.market_capture.replay import replay_run
from app.research.market_capture.storage import (
    BoundedRunWriter, FinalizationMetadataError, FreeSpaceError,
    MAX_FINALIZATION_BYTES, MAX_FINAL_REPORT_BYTES,
)
from tests.test_market_capture_adapter import (
    FakeConnection, FakeConnector, FakeSnapshots, ManualClock, _advance, combined, dep, setup, snap,
)


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    import socket
    original_socket = socket.socket
    def forbidden(*args, **kwargs): raise AssertionError("network/DNS attempted in M20.5 offline test")
    def guarded_socket(family=socket.AF_INET, *args, **kwargs):
        if family in {socket.AF_INET, socket.AF_INET6}: forbidden()
        return original_socket(family, *args, **kwargs)
    monkeypatch.setattr(socket, "socket", guarded_socket)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def test_stage_a_and_extended_profiles_are_separate_and_bounded(tmp_path):
    stage = capture_profile("stage-a")
    assert stage.max_elapsed_ns == STAGE_A_MAX_NS
    assert stage.max_total_bytes == STAGE_A_MAX_BYTES
    assert STAGE_A_CONFIRM != EXTENDED_CONFIRM

    extended = capture_profile("extended")
    assert extended.max_elapsed_ns == EXTENDED_MAX_NS
    assert extended.max_total_bytes == EXTENDED_MAX_BYTES
    assert extended.host_min_free_bytes == EXTENDED_HOST_RESERVE_BYTES
    with pytest.raises(FreeSpaceError, match="physical-host"):
        preflight(extended, tmp_path / "blocked", "run", free_space=lambda _: 200_000_000_000,
                  host_free_space=lambda: 149_999_999_999)
    result = preflight(extended, tmp_path / "ready", "run",
        free_space=lambda _: 200_000_000_000, host_free_space=lambda: 150_000_000_000)
    assert result["network_performed"] is False
    assert result["physical_host_minimum_at_start_bytes"] == 150_000_000_000
    assert result["bounded_finalization_max_bytes"] == MAX_FINALIZATION_BYTES


def test_windows_vhdx_monitor_is_shell_free_and_fails_closed():
    calls = []
    def ok(argv, **kwargs):
        calls.append((argv, kwargs)); return subprocess.CompletedProcess(argv, 0, "132435476480", "")
    monitor = WindowsVhdxSpaceMonitor(
        r"C:\Users\ostap\AppData\Local\wsl\id\ext4.vhdx", runner=ok,
        powershell="powershell.exe")
    assert monitor() == 132_435_476_480
    assert isinstance(calls[0][0], list) and "shell" not in calls[0][1]

    def bad(argv, **kwargs): return subprocess.CompletedProcess(argv, 45, "", "missing")
    failed = WindowsVhdxSpaceMonitor(r"C:\wsl\ext4.vhdx", runner=bad,
                                     powershell="powershell.exe")
    with pytest.raises(HostSpaceUnavailableError): failed()


def test_secondary_space_monitor_stops_writes_and_monitor_failure_is_closed(tmp_path):
    def writer(name, callback):
        return BoundedRunWriter(tmp_path, name, max_total_bytes=700_000,
            reserve_final_bytes=600_000, min_free_bytes=1, max_record_bytes=4096,
            free_space=lambda _: 1_000_000, secondary_free_space=callback,
            secondary_min_free_bytes=50_000, secondary_check_interval_bytes=1)
    record = ControlRecord("session_start", "2027-01-01T00:00:00+00:00", 1, {})
    with pytest.raises(FreeSpaceError, match="secondary minimum"):
        writer("low", lambda: 49_999).append(record)
    def unavailable(): raise RuntimeError("host query failed")
    with pytest.raises(FreeSpaceError, match="monitor unavailable"):
        writer("missing", unavailable).append(record)


def test_bounded_report_keeps_counts_and_finalizes_inside_small_cap(tmp_path):
    config = CaptureConfig(report_schema_version="bounded-book-v2", diagnostic_history_limit=256,
                           book_level_limit=20_000, report_top_levels=5)
    writer = BoundedRunWriter(tmp_path, "run", max_total_bytes=700_000,
        reserve_final_bytes=MAX_FINALIZATION_BYTES, min_free_bytes=1,
        free_space=lambda _: 10_000_000)
    engine = CaptureEngine(config, writer=writer)
    engine.book.valid = True; engine.book.invalid_reason = None; engine.book.update_id = 123
    engine.book.snapshot_bid_levels = 5000; engine.book.snapshot_ask_levels = 5000
    engine.book.bids = {Decimal(100_000 - i): Decimal("1") for i in range(20_000)}
    engine.book.asks = {Decimal(100_001 + i): Decimal("1") for i in range(20_000)}
    for index in range(1000):
        before = (engine.book.valid, engine.book.invalid_reason, engine.book.update_id)
        engine.book.valid = not engine.book.valid
        engine.book.invalid_reason = None if engine.book.valid else f"synthetic-{index}"
        engine._transition(before)
    report = engine.report(); encoded = (json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n").encode()
    assert len(encoded) < MAX_FINAL_REPORT_BYTES
    assert len(report.book["top_bids"]) == len(report.book["top_asks"]) == 5
    assert report.book["level_counts"] == {"bids": 20_000, "asks": 20_000}
    assert len(report.state_transitions) == 256
    assert report.state_transition_total == 1001 and report.state_transition_omitted == 745
    engine.finalize()
    assert (tmp_path / "run" / "manifest.sha256").is_file()


def test_oversized_final_metadata_is_rejected_without_false_marker(tmp_path):
    writer = BoundedRunWriter(tmp_path, "run", max_total_bytes=1_200_000,
        reserve_final_bytes=600_000, min_free_bytes=1, free_space=lambda _: 10_000_000)
    with pytest.raises(FinalizationMetadataError):
        writer.finalize({"blob": "x" * MAX_FINAL_REPORT_BYTES}, "test", {})
    assert not (tmp_path / "run" / "manifest.sha256").exists()


def test_snapshot_counts_are_raw_counts_and_book_growth_is_separate():
    book = BookState(level_limit=10)
    book.apply_depth({"U": 101, "u": 101, "b": [["98", "1"]], "a": []})
    book.load_snapshot({"lastUpdateId": 100, "bids": [["100", "1"], ["99", "1"]],
                        "asks": [["101", "1"], ["102", "1"]]})
    summary = book.report_summary()
    assert summary["snapshot_level_counts"] == {"bids": 2, "asks": 2}
    assert summary["level_counts"] == {"bids": 3, "asks": 2}
    assert book.known_snapshot_levels == 2


def test_total_reconnects_are_distinct_from_consecutive_streak(tmp_path):
    clock = ManualClock()
    connections = [
        FakeConnection(clock, [(1, asyncio.TimeoutError()), (1, TransportDisconnected("one"))]),
        FakeConnection(clock, [(1, asyncio.TimeoutError()), (1, TransportDisconnected("two"))]),
        FakeConnection(clock, [(1, asyncio.TimeoutError()), (0, TransportEnd())]),
    ]
    config = capture_profile("stage-a", duration_seconds=30)
    config = type(config)(**{**config.__dict__, "max_total_bytes": 2_000_000,
        "min_free_bytes": 1, "reserve_final_bytes": 1_048_576,
        "queue_capacity": 8, "max_record_bytes": 4096, "reconnect_limit": 1,
        "read_poll_seconds": .01})
    writer = BoundedRunWriter(tmp_path, "run", max_total_bytes=config.max_total_bytes,
        reserve_final_bytes=config.reserve_final_bytes, min_free_bytes=1,
        max_record_bytes=4096, free_space=lambda _: 10_000_000)
    engine = CaptureEngine(CaptureConfig(report_schema_version="bounded-book-v2",
        diagnostic_history_limit=256, book_level_limit=20_000, max_record_bytes=4096), writer=writer)
    adapter = PublicMarketAdapter(config, engine, FakeConnector(connections),
        FakeSnapshots(clock, [(0, SnapshotResponse(200, snap(), {}))] * 3), clock,
        sleep=lambda seconds: _advance(clock, seconds))
    report = asyncio.run(adapter.run()); engine.finalize(); replay = replay_run(tmp_path / "run")
    telemetry = report.transport_telemetry
    assert report.stop_reason == "input_exhausted"
    assert telemetry["reconnects"] == 2 and telemetry["max_consecutive_reconnects"] == 1
    assert replay.integrity_status == "VERIFIED_FINALIZED"


def test_total_recoveries_are_distinct_from_consecutive_streak(tmp_path):
    clock = ManualClock()
    ws = FakeConnection(clock, [
        (1, combined("depth@100ms", dep(101, 101))),
        (1, combined("depth@100ms", dep(103, 103))),
        (1, combined("depth@100ms", dep(105, 105))),
        (0, TransportEnd()),
    ])
    adapter, _, _ = setup(tmp_path, clock, [ws], [
        (0, SnapshotResponse(200, snap(100), {})),
        (0, SnapshotResponse(200, snap(103), {})),
        (0, SnapshotResponse(200, snap(105), {})),
    ])
    report = asyncio.run(adapter.run()); telemetry = report.transport_telemetry
    assert report.stop_reason == "input_exhausted"
    assert telemetry["recovery_cycles"] == 2
    assert telemetry["max_consecutive_recoveries"] == 1
    assert telemetry["consecutive_recoveries_current"] == 0


def test_book_level_limit_invalidates_instead_of_silently_pruning():
    engine = CaptureEngine(CaptureConfig(book_level_limit=2))
    engine._validate_and_apply("snapshot", {"lastUpdateId": 100,
        "bids": [["100", "1"], ["99", "1"]], "asks": [["101", "1"]]},
        {"symbol": "BTCUSDT"})
    envelope = RawEnvelope("s1", 1, "depth", "test", "2027-01-01T00:00:00+00:00", 1,
        json.dumps({"e":"depthUpdate","E":1800000000000,"s":"BTCUSDT","U":101,"u":101,
                    "b":[["98","1"]],"a":[]}))
    engine.process_envelope(envelope, persist=False)
    assert not engine.book.valid and engine.book.invalid_reason == "rejected_depth"
    assert engine.counters.rejected == 1


def test_measurement_wrapper_preserves_times_elapsed_and_exit_codes():
    ok = run_measured([sys.executable, "-c", "pass"])
    failed = run_measured([sys.executable, "-c", "raise SystemExit(7)"])
    assert ok["exit_code"] == 0 and failed["exit_code"] == 7
    assert ok["start_utc"].endswith("Z") and ok["end_utc"].endswith("Z")
    assert ok["elapsed_ns"] >= 0 and failed["elapsed_ns"] >= 0


def test_comparability_audit_separates_online_and_retrospective_causes(tmp_path):
    run = tmp_path / "run"; run.mkdir(); records = []
    def envelope(sequence, stream, payload):
        return RawEnvelope("s1", sequence, stream, "test", "2027-01-01T00:00:00+00:00",
            sequence, json.dumps(payload, separators=(",", ":")))
    ticker = lambda update: {"s": "BTCUSDT", "u": update, "b": "100", "B": "1", "a": "101", "A": "1"}
    records.append(envelope(1, "combined", {"stream":"btcusdt@bookTicker","data":ticker(100)}))
    records.append(envelope(2, "snapshot", {"lastUpdateId":100,"bids":[["100","1"]],"asks":[["101","1"]]}))
    records.append(envelope(3, "combined", {"stream":"btcusdt@bookTicker","data":ticker(102)}))
    records.append(envelope(4, "combined", {"stream":"btcusdt@depth@100ms","data":dep(101,103)}))
    records.append(envelope(5, "combined", {"stream":"btcusdt@bookTicker","data":ticker(102)}))
    records.append(envelope(6, "combined", {"stream":"btcusdt@bookTicker","data":ticker(104)}))
    records.append(envelope(7, "combined", {"stream":"btcusdt@depth@100ms","data":dep(104,104)}))
    records.append(envelope(8, "combined", {"stream":"btcusdt@bookTicker","data":ticker(104)}))
    (run / "raw.jsonl").write_bytes(b"".join(item.to_json_line() for item in records))
    (run / "report.json").write_text(json.dumps({"counters":{"comparable":1,"unmatched":4,
        "stale":0,"inconsistent":0},"book":{"known_snapshot_levels":1}}))
    result = audit_bookticker_comparability(run)
    assert result["online_outcomes_match_stored"] is True
    assert result["unmatched_cause_counts"] == {
        "book_unavailable": 1,
        "inside_previously_received_depth_packet": 1,
        "ticker_before_future_exact": 1,
        "ticker_before_future_inside_packet": 1,
    }
    assert result["retrospective_future_message_knowledge_used"] is True
