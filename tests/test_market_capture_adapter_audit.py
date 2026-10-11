from __future__ import annotations

import asyncio
from collections import deque
import json
from pathlib import Path
import threading

import pytest
import requests

from app.research.market_capture.engine import CaptureConfig, CaptureEngine
from app.research.market_capture.public_adapter import (
    PublicAdapterConfig, PublicMarketAdapter, RequestsSnapshotClient,
    SnapshotConnectionError, SnapshotResponse, SnapshotResponseTooLarge, SnapshotTimeout,
    TransportDisconnected, TransportEnd, preflight, run_live_capture,
)
from app.research.market_capture.replay import replay_run
from app.research.market_capture.storage import BoundedRunWriter
from tests.test_market_capture_adapter import (
    FakeConnection, FakeConnector, FakeSnapshots, ManualClock, _advance,
    combined, dep, setup, snap, trade,
)


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    import socket
    original_socket=socket.socket
    def forbidden(*args,**kwargs): raise AssertionError("network/DNS attempted in M20.3R hermetic test")
    def guarded_socket(family=socket.AF_INET,*args,**kwargs):
        if family in {socket.AF_INET,socket.AF_INET6}: forbidden()
        return original_socket(family,*args,**kwargs)
    monkeypatch.setattr(socket,"socket",guarded_socket)
    monkeypatch.setattr(socket,"create_connection",forbidden)
    monkeypatch.setattr(socket,"getaddrinfo",forbidden)


class ControlledConnection:
    def __init__(self) -> None:
        self.results: asyncio.Queue[object] = asyncio.Queue()
        self.calls = 0; self.active = 0; self.max_active = 0; self.closed = False

    async def recv(self):
        self.calls += 1; self.active += 1; self.max_active = max(self.max_active, self.active)
        try:
            value = await self.results.get()
            if isinstance(value, BaseException): raise value
            return value
        finally: self.active -= 1

    async def close(self): self.closed = True


class ControlledSnapshots:
    def __init__(self) -> None: self.future: asyncio.Future[SnapshotResponse] | None = None
    async def fetch(self, url, params, timeout):
        self.future = asyncio.get_running_loop().create_future()
        return await self.future


def _explicit_loop(coro):
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(coro)
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        assert pending == []
        return result
    finally:
        loop.close(); asyncio.set_event_loop(None)


def test_snapshot_completion_keeps_single_pending_reader_and_cleans_tasks(tmp_path):
    async def scenario():
        clock=ManualClock(); ws=ControlledConnection(); snapshots=ControlledSnapshots()
        config=PublicAdapterConfig(max_elapsed_ns=20_000_000_000,max_total_bytes=200000,
            min_free_bytes=1,reserve_final_bytes=30000,queue_capacity=8,max_record_bytes=4096,
            read_poll_seconds=.01,cleanup_timeout_seconds=.1)
        writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=200000,reserve_final_bytes=30000,
            min_free_bytes=1,max_record_bytes=4096,free_space=lambda _:999999)
        engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
        adapter=PublicMarketAdapter(config,engine,FakeConnector([ws]),snapshots,clock,sleep=lambda s:_advance(clock,s))
        task=asyncio.create_task(adapter.run())
        while snapshots.future is None or ws.calls == 0: await asyncio.sleep(0)
        assert ws.calls == 1 and ws.max_active == 1
        snapshots.future.set_result(SnapshotResponse(200,snap(),{}))
        for _ in range(4): await asyncio.sleep(0)
        assert ws.calls == 1 and ws.max_active == 1
        await ws.results.put(TransportEnd())
        report=await task
        assert report.stop_reason=="input_exhausted" and ws.closed
        return ws
    ws=_explicit_loop(scenario())
    assert ws.active==0 and ws.max_active==1


def test_simultaneous_snapshot_and_recv_results_are_both_consumed(tmp_path):
    async def scenario():
        clock=ManualClock(); ws=ControlledConnection(); snapshots=ControlledSnapshots()
        config=PublicAdapterConfig(max_elapsed_ns=20_000_000_000,max_total_bytes=200000,
            min_free_bytes=1,reserve_final_bytes=30000,queue_capacity=8,max_record_bytes=4096,
            read_poll_seconds=.01,cleanup_timeout_seconds=.1)
        writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=200000,reserve_final_bytes=30000,
            min_free_bytes=1,max_record_bytes=4096,free_space=lambda _:999999)
        engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
        adapter=PublicMarketAdapter(config,engine,FakeConnector([ws]),snapshots,clock,sleep=lambda s:_advance(clock,s))
        task=asyncio.create_task(adapter.run())
        while snapshots.future is None or ws.calls == 0: await asyncio.sleep(0)
        snapshots.future.set_result(SnapshotResponse(200,snap(),{}))
        await ws.results.put(combined("depth@100ms",dep(101,101)))
        for _ in range(5): await asyncio.sleep(0)
        await ws.results.put(TransportEnd())
        report=await task
        assert report.book["valid"] and report.book["update_id"]==101
        assert adapter.telemetry.processed==1
    _explicit_loop(scenario())


def test_gap_recovers_on_same_open_connection(tmp_path):
    clock=ManualClock(); ws=FakeConnection(clock,[(1,combined("depth@100ms",dep(101,101))),
        (1,combined("depth@100ms",dep(103,103))),(1,combined("depth@100ms",dep(104,104))),(0,TransportEnd())])
    adapter,engine,_=setup(tmp_path,clock,[ws],[(0,SnapshotResponse(200,snap(100),{})),
        (0,SnapshotResponse(200,snap(103),{}))])
    report=asyncio.run(adapter.run())
    assert report.stop_reason=="input_exhausted" and engine.book.valid and engine.book.update_id==104
    assert adapter.telemetry.reconnects==0 and adapter.telemetry.recovery_cycles>=1


@pytest.mark.parametrize("first_updates", [[dep(101,101)], [dep(101,101),dep(103,103)]])
def test_too_old_or_buffer_gap_snapshot_retries_until_book_valid(tmp_path,first_updates):
    clock=ManualClock(); actions=[(1,combined("depth@100ms",item)) for item in first_updates]+[(0,TransportEnd())]
    final=max(item["u"] for item in first_updates)
    adapter,engine,_=setup(tmp_path,clock,[FakeConnection(clock,actions)],
        [(0,SnapshotResponse(200,snap(90),{})),(0,SnapshotResponse(200,snap(final),{}))])
    report=asyncio.run(adapter.run())
    assert report.stop_reason=="input_exhausted" and engine.book.valid and engine.book.update_id==final
    assert adapter.telemetry.snapshot_attempts==2


def test_repeated_recovery_and_bounded_exhaustion(tmp_path):
    clock=ManualClock(); ws=FakeConnection(clock,[(1,combined("depth@100ms",dep(103,103))),
        (1,combined("depth@100ms",dep(106,106))),(0,TransportEnd())])
    adapter,engine,_=setup(tmp_path/"ok",clock,[ws],[(0,SnapshotResponse(200,snap(100),{})),
        (0,SnapshotResponse(200,snap(103),{})),(0,SnapshotResponse(200,snap(106),{}))])
    report=asyncio.run(adapter.run())
    assert report.stop_reason=="input_exhausted" and engine.book.update_id==106
    assert adapter.telemetry.recovery_cycles>=2

    clock=ManualClock(); ws=FakeConnection(clock,[(1,combined("depth@100ms",dep(103,103)))]+[(1,asyncio.TimeoutError())]*8)
    adapter,_,_=setup(tmp_path/"bad",clock,[ws],[(0,SnapshotResponse(200,snap(100),{})),
        (0,SnapshotResponse(200,snap(100),{})),(0,SnapshotResponse(200,snap(100),{}))],recovery_limit=2)
    report=asyncio.run(adapter.run())
    assert report.stop_reason=="recovery_exhausted" and adapter.telemetry.snapshot_attempts==2


class FakeResponse:
    def __init__(self,status:int,body:bytes,headers=None,encoding="utf-8"):
        self.status_code=status; self.body=body; self.headers=headers or {}; self.encoding=encoding
    def iter_content(self,chunk_size):
        yield self.body
    def close(self): pass


@pytest.mark.parametrize(("status","body","outcome"),[
    (429,b"rate","rate_limited"),(418,b"ban","rate_limited"),(503,b"down","server_error"),
    (200,b"not-json","malformed_json"),
])
def test_requests_wrapper_normalizes_http_and_malformed(monkeypatch,status,body,outcome):
    monkeypatch.setattr(requests,"get",lambda *a,**k:FakeResponse(status,body,{"rEtRy-AfTeR":"7"}))
    response=asyncio.run(RequestsSnapshotClient(max_response_bytes=100).fetch("https://invalid",{},.1))
    assert response.outcome==outcome and response.header("Retry-After")=="7" and response.body==body.decode()


def test_requests_wrapper_normalizes_exceptions_and_bounds_body(monkeypatch):
    client=RequestsSnapshotClient(max_response_bytes=4)
    monkeypatch.setattr(requests,"get",lambda *a,**k:(_ for _ in ()).throw(requests.Timeout("slow")))
    with pytest.raises(SnapshotTimeout): asyncio.run(client.fetch("https://invalid",{},.1))
    monkeypatch.setattr(requests,"get",lambda *a,**k:(_ for _ in ()).throw(requests.ConnectionError("down")))
    with pytest.raises(SnapshotConnectionError): asyncio.run(client.fetch("https://invalid",{},.1))
    monkeypatch.setattr(requests,"get",lambda *a,**k:FakeResponse(200,b"12345",{"Content-Length":"5"}))
    with pytest.raises(SnapshotResponseTooLarge): asyncio.run(client.fetch("https://invalid",{},.1))
    monkeypatch.setattr(requests,"get",lambda *a,**k:FakeResponse(200,b"\xff"))
    assert asyncio.run(RequestsSnapshotClient().fetch("https://invalid",{},.1)).outcome=="malformed_encoding"


def test_requests_cancellation_waits_for_worker_no_hidden_work(monkeypatch):
    entered=threading.Event(); release=threading.Event(); exited=threading.Event()
    def blocking(*args,**kwargs):
        entered.set(); release.wait(2); exited.set(); return FakeResponse(200,snap().encode())
    monkeypatch.setattr(requests,"get",blocking)
    async def scenario():
        task=asyncio.create_task(RequestsSnapshotClient().fetch("https://invalid",{},.1))
        while not entered.is_set(): await asyncio.sleep(0)
        task.cancel(); await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert exited.is_set()
    asyncio.run(scenario())


def test_retry_after_is_case_insensitive_and_capped_by_overall_budget(tmp_path):
    clock=ManualClock(); sleeps=[]
    async def sleep(value): sleeps.append(value); clock.advance(value); await asyncio.sleep(0)
    config=PublicAdapterConfig(max_elapsed_ns=3_000_000_000,max_total_bytes=200000,min_free_bytes=1,
        reserve_final_bytes=30000,queue_capacity=8,max_record_bytes=4096,snapshot_attempt_limit=2,
        reconnect_limit=0,read_poll_seconds=.01)
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=200000,reserve_final_bytes=30000,min_free_bytes=1,
        max_record_bytes=4096,free_space=lambda _:999999)
    engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
    snaps=FakeSnapshots(clock,[(0,SnapshotResponse(429,"rate",{"rEtRy-AfTeR":"99"}))])
    adapter=PublicMarketAdapter(config,engine,FakeConnector([FakeConnection(clock,[(1,asyncio.TimeoutError())])]),snaps,clock,sleep=sleep)
    report=asyncio.run(adapter.run())
    assert sleeps==[3.0] and report.stop_reason=="time_limit"


def test_preflight_preserves_existing_probes_and_rejects_bad_run_ids(tmp_path):
    old=tmp_path/".m20_3_write_probe"; old.write_text("owned elsewhere")
    preflight(PublicAdapterConfig(max_total_bytes=1000,min_free_bytes=1,reserve_final_bytes=10),
              tmp_path,"run",free_space=lambda _:10000)
    assert old.read_text()=="owned elsewhere" and not (tmp_path/"run").exists()
    with pytest.raises(ValueError): preflight(PublicAdapterConfig(),tmp_path,"../escape",free_space=lambda _:10**20)
    assert old.exists()


def test_replay_reconstructs_disconnect_continuity_and_cancellation(tmp_path):
    clock=ManualClock(); first=FakeConnection(clock,[(1,TransportDisconnected("drop"))])
    second=FakeConnection(clock,[(0,TransportEnd())]); snaps=FakeSnapshots(clock,["BLOCK","BLOCK"])
    adapter,engine,_=setup(tmp_path/"disconnect",clock,[first,second],[],reconnect_limit=1)
    adapter.snapshot_client=snaps
    report=asyncio.run(adapter.run()); captured=engine.finalize(); replay=replay_run(tmp_path/"disconnect"/"run")
    assert report.book==replay.book==captured.book
    assert replay.book["invalid_reason"]=="reconnect_requires_snapshot"
    assert replay.state_transitions==captured.state_transitions

    clock=ManualClock(); cancel=asyncio.Event()
    def cancel_now(): cancel.set(); return asyncio.TimeoutError()
    adapter,engine,_=setup(tmp_path/"cancel",clock,[FakeConnection(clock,[(1,cancel_now)])],["BLOCK"])
    report=asyncio.run(adapter.run(cancellation=cancel)); engine.finalize(); replay=replay_run(tmp_path/"cancel"/"run")
    assert report.stop_reason==replay.stop_reason=="cancelled"


def test_incomplete_writer_failure_has_no_false_final_marker(tmp_path):
    class FailFile:
        def write(self,data): raise OSError("disk")
        def close(self): pass
    clock=ManualClock(); config=PublicAdapterConfig(max_elapsed_ns=10_000_000_000,max_total_bytes=200000,
        min_free_bytes=1,reserve_final_bytes=30000,queue_capacity=8,max_record_bytes=4096)
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=200000,reserve_final_bytes=30000,min_free_bytes=1,
        max_record_bytes=4096,free_space=lambda _:999999,raw_opener=lambda *a:FailFile())
    engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
    adapter=PublicMarketAdapter(config,engine,FakeConnector([FakeConnection(clock,[])]),FakeSnapshots(clock,[]),clock)
    report=asyncio.run(adapter.run())
    assert report.stop_reason=="disk_write_failure" and report.integrity_status=="INCOMPLETE_STORAGE_FAILURE"
    assert not (tmp_path/"run"/"manifest.sha256").exists()


def test_websocket_buffer_bound_is_passed_to_connector(tmp_path):
    clock=ManualClock(); connector=FakeConnector([FakeConnection(clock,[(0,TransportEnd())])])
    adapter,engine,_=setup(tmp_path,clock,[],["BLOCK"],queue_capacity=7)
    adapter.connector=connector
    asyncio.run(adapter.run())
    assert connector.calls[0][3]==7


def test_malformed_required_depth_recovers_without_disconnect(tmp_path):
    clock=ManualClock(); malformed=dep(101,101); malformed.pop("b")
    ws=FakeConnection(clock,[(1,combined("depth@100ms",malformed)),
        (1,combined("depth@100ms",dep(102,102))),(0,TransportEnd())])
    adapter,engine,_=setup(tmp_path,clock,[ws],[(0,SnapshotResponse(200,snap(100),{})),
        (0,SnapshotResponse(200,snap(101),{}))])
    report=asyncio.run(adapter.run())
    assert report.stop_reason=="input_exhausted" and engine.book.valid and engine.book.update_id==102
    assert report.counters["rejected"]>=1 and adapter.telemetry.reconnects==0


def test_hung_connect_and_close_are_bounded_and_owned(tmp_path):
    class HungConnector:
        async def connect(self,*args,**kwargs): await asyncio.Event().wait()
    async def connect_case():
        clock=ManualClock(); config=PublicAdapterConfig(max_elapsed_ns=10_000_000_000,
            max_total_bytes=200000,min_free_bytes=1,reserve_final_bytes=30000,queue_capacity=8,
            max_record_bytes=4096,connect_timeout_seconds=.01,cleanup_timeout_seconds=.01)
        writer=BoundedRunWriter(tmp_path/"connect","run",max_total_bytes=200000,reserve_final_bytes=30000,
            min_free_bytes=1,max_record_bytes=4096,free_space=lambda _:999999)
        engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
        report=await PublicMarketAdapter(config,engine,HungConnector(),FakeSnapshots(clock,[]),clock).run()
        assert report.stop_reason=="connect_timeout"
    _explicit_loop(connect_case())

    class HungClose(FakeConnection):
        def __init__(self,clock): super().__init__(clock,[(0,TransportEnd())]); self.close_cancelled=False
        async def close(self):
            try: await asyncio.Event().wait()
            except asyncio.CancelledError: self.close_cancelled=True; raise
    async def close_case():
        clock=ManualClock(); ws=HungClose(clock)
        adapter,_,_=setup(tmp_path/"close",clock,[ws],["BLOCK"],cleanup_timeout_seconds=.01)
        report=await adapter.run()
        assert report.stop_reason=="input_exhausted" and ws.close_cancelled
    _explicit_loop(close_case())


@pytest.mark.parametrize(("writer_kwargs","expected"),[
    ({"free_space":lambda _:0},"minimum_free_space"),
    ({"max_record_bytes":20,"free_space":lambda _:999999},"record_too_large"),
])
def test_control_write_failures_keep_specific_primary_reason(tmp_path,writer_kwargs,expected):
    clock=ManualClock(); config=PublicAdapterConfig(max_elapsed_ns=10_000_000_000,max_total_bytes=200000,
        min_free_bytes=1,reserve_final_bytes=30000,queue_capacity=8,max_record_bytes=4096)
    options={"max_total_bytes":200000,"reserve_final_bytes":30000,"min_free_bytes":1,
             "max_record_bytes":4096,"free_space":lambda _:999999}; options.update(writer_kwargs)
    writer=BoundedRunWriter(tmp_path, "run", **options)
    engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
    adapter=PublicMarketAdapter(config,engine,FakeConnector([FakeConnection(clock,[])]),FakeSnapshots(clock,[]),clock)
    report=asyncio.run(adapter.run())
    assert report.stop_reason==expected and report.integrity_status=="INCOMPLETE_STORAGE_FAILURE" and writer.closed


def test_run_live_capture_does_not_finalize_aborted_writer(tmp_path):
    class FailFile:
        def write(self,data): raise OSError("disk")
        def close(self): pass
    def writer_factory(root,run_id,**kwargs):
        return BoundedRunWriter(root,run_id,free_space=lambda _:999999,raw_opener=lambda *a:FailFile(),**kwargs)
    clock=ManualClock(); config=PublicAdapterConfig(max_elapsed_ns=10_000_000_000,max_total_bytes=200000,
        min_free_bytes=1,reserve_final_bytes=30000,queue_capacity=8,max_record_bytes=4096)
    report=asyncio.run(run_live_capture(config,tmp_path,"run",connector=FakeConnector([FakeConnection(clock,[])]),
        snapshot_client=FakeSnapshots(clock,[]),clock=clock,writer_factory=writer_factory))
    assert report.stop_reason=="disk_write_failure" and report.integrity_status=="INCOMPLETE_STORAGE_FAILURE"
    assert not (tmp_path/"run"/"manifest.sha256").exists()

    class FailFinal:
        def write(self,data): raise OSError("final disk")
        def flush(self): pass
        def close(self): pass
    def final_writer_factory(root,run_id,**kwargs):
        return BoundedRunWriter(root,run_id,free_space=lambda _:999999,
                                final_opener=lambda *a:FailFinal(),**kwargs)
    clock=ManualClock()
    report=asyncio.run(run_live_capture(config,tmp_path,"final-fail",
        connector=FakeConnector([FakeConnection(clock,[(0,TransportEnd())])]),
        snapshot_client=FakeSnapshots(clock,["BLOCK"]),clock=clock,writer_factory=final_writer_factory))
    replay=replay_run(tmp_path/"final-fail")
    assert report.integrity_status=="INCOMPLETE_STORAGE_FAILURE"
    assert replay.integrity_status=="RECOVERY_UNFINALIZED" and replay.replay_verification["finalized"] is False
    assert not (tmp_path/"final-fail"/"manifest.sha256").exists()
