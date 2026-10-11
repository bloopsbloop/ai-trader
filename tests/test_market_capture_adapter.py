from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path

import pytest

from app.research.market_capture.engine import CaptureConfig,CaptureEngine
from app.research.market_capture.public_adapter import (
    PublicAdapterConfig,PublicMarketAdapter,SnapshotExhausted,SnapshotResponse,
    SnapshotTimeout,TransportDisconnected,TransportEnd,preflight,
)
from app.research.market_capture.replay import replay_run
from app.research.market_capture.storage import BoundedRunWriter,FreeSpaceError


@pytest.fixture(autouse=True)
def block_all_network(monkeypatch):
    import socket
    original_socket=socket.socket
    def forbidden(*args,**kwargs): raise AssertionError("network/DNS attempted in hermetic adapter test")
    def guarded_socket(family=socket.AF_INET,*args,**kwargs):
        if family in {socket.AF_INET,socket.AF_INET6}: forbidden()
        return original_socket(family,*args,**kwargs)
    monkeypatch.setattr(socket,"socket",guarded_socket)
    monkeypatch.setattr(socket,"create_connection",forbidden)
    monkeypatch.setattr(socket,"getaddrinfo",forbidden)


class ManualClock:
    kind="synthetic"
    def __init__(self): self.ns=0; self.utc_shift=0
    def advance(self,seconds): self.ns+=int(seconds*1e9)
    def step_utc(self,seconds): self.utc_shift+=seconds
    def sample(self):
        dt=datetime(2027,1,15,tzinfo=timezone.utc)+timedelta(seconds=self.ns/1e9+self.utc_shift)
        return dt.isoformat().replace("+00:00","Z"),self.ns


class FakeConnection:
    def __init__(self,clock,actions): self.clock=clock; self.actions=deque(actions); self.closed=False
    async def recv(self):
        await asyncio.sleep(0)
        if not self.actions: raise TransportEnd()
        advance,value=self.actions.popleft(); self.clock.advance(advance)
        if callable(value): value=value()
        if isinstance(value,BaseException): raise value
        return value
    async def close(self): self.closed=True


class FakeConnector:
    def __init__(self,connections): self.items=deque(connections); self.calls=[]
    async def connect(self,url,*,open_timeout,max_size,max_queue):
        self.calls.append((url,open_timeout,max_size,max_queue))
        item=self.items.popleft()
        if isinstance(item,BaseException): raise item
        return item


class FakeSnapshots:
    def __init__(self,clock,items): self.clock=clock; self.items=deque(items); self.calls=[]; self.cancelled=0
    async def fetch(self,url,params,timeout):
        self.calls.append((url,params,timeout)); item=self.items.popleft()
        if item=="BLOCK":
            try: await asyncio.Event().wait()
            except asyncio.CancelledError: self.cancelled+=1; raise
        advance,value=item; self.clock.advance(advance); await asyncio.sleep(0)
        if isinstance(value,BaseException): raise value
        return value


def snap(last=100): return json.dumps({"lastUpdateId":last,"bids":[["100","2"]],"asks":[["101","4"]]},separators=(",",":"))
def combined(stream,data): return json.dumps({"stream":"btcusdt@"+stream,"data":data},separators=(",",":"))
def dep(U,u,b=(),a=()): return {"e":"depthUpdate","E":1800000000000,"s":"BTCUSDT","U":U,"u":u,"b":list(b),"a":list(a)}
def trade(i=1): return {"e":"trade","E":1800000000000,"s":"BTCUSDT","t":i,"p":"100","q":"1","T":1800000000000,"m":False}
def tick(u,b="100",B="2",a="101",A="4"): return {"s":"BTCUSDT","u":u,"b":b,"B":B,"a":a,"A":A}


def setup(tmp_path,clock,connections,snapshots,**overrides):
    values={"max_elapsed_ns":20_000_000_000,"max_total_bytes":200_000,"min_free_bytes":1,
            "reserve_final_bytes":30_000,"queue_capacity":8,"max_record_bytes":4096,
            "snapshot_attempt_limit":3,"reconnect_limit":2,"read_poll_seconds":0.01,
            "idle_diagnostic_ns":2_000_000_000}
    values.update(overrides); config=PublicAdapterConfig(**values)
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=config.max_total_bytes,
        reserve_final_bytes=config.reserve_final_bytes,min_free_bytes=config.min_free_bytes,
        max_record_bytes=config.max_record_bytes,free_space=lambda _:999999)
    engine=CaptureEngine(CaptureConfig(queue_capacity=config.queue_capacity,max_record_bytes=config.max_record_bytes),writer=writer)
    adapter=PublicMarketAdapter(config,engine,FakeConnector(connections),FakeSnapshots(clock,snapshots),clock,
                                sleep=lambda seconds:_advance(clock,seconds))
    return adapter,engine,writer


async def _advance(clock,seconds): clock.advance(seconds); await asyncio.sleep(0)
def run(adapter,**kwargs): return asyncio.run(adapter.run(**kwargs))


def test_preflight_is_offline_and_shows_resource_decision(tmp_path,monkeypatch):
    config=PublicAdapterConfig(); result=preflight(config,tmp_path,"future-run",free_space=lambda _:7*1024**3)
    assert result["network_performed"] is False
    assert result["minimum_free_at_start_bytes"]==6*1024**3
    assert result["storage_cap_bytes"]==2*1024**3 and not (tmp_path/"future-run").exists()
    with pytest.raises(FreeSpaceError): preflight(config,tmp_path,"low",free_space=lambda _:5*1024**3)


def test_normal_adapter_writer_book_report_and_verified_replay(tmp_path):
    clock=ManualClock(); ws=FakeConnection(clock,[(1,combined("trade",trade())),(1,combined("depth@100ms",dep(101,101,b=[["100","3"]]))),(1,combined("bookTicker",tick(101,B="3"))),(0,TransportEnd())])
    adapter,engine,_=setup(tmp_path,clock,[ws],[(0,SnapshotResponse(200,snap(),{"X":"y"}))])
    report=run(adapter); capture=engine.finalize(); replay=replay_run(tmp_path/"run")
    assert report.stop_reason=="input_exhausted" and ws.closed
    assert capture.book["valid"] and capture.book["update_id"]==101
    assert capture.counters["comparable"]==1
    assert capture.transport_telemetry["received"]==3
    assert replay.integrity_status=="VERIFIED_FINALIZED" and replay.replay_verification["derived_fields_match"] is True
    records=[json.loads(line) for line in (tmp_path/"run"/"raw.jsonl").read_text().splitlines()]
    snapshot_record=next(item for item in records if item.get("stream")=="snapshot")
    assert snapshot_record["payload_raw"]==snap() and '"symbol":"BTCUSDT"' not in snap()


def test_snapshot_race_cross_stream_order_and_repeat_after_disconnect(tmp_path):
    clock=ManualClock(); first=FakeConnection(clock,[(1,combined("bookTicker",tick(101))),(1,combined("depth@100ms",dep(101,101))),(0,TransportDisconnected("drop during recovery"))])
    second=FakeConnection(clock,[(1,combined("depth@100ms",dep(201,201,b=[["100","5"]]))),(0,TransportEnd())])
    snaps=FakeSnapshots(clock,["BLOCK",(0,SnapshotResponse(200,snap(200),{}))])
    config=PublicAdapterConfig(max_elapsed_ns=20_000_000_000,max_total_bytes=200000,min_free_bytes=1,reserve_final_bytes=30000,queue_capacity=8,max_record_bytes=4096,snapshot_attempt_limit=2,reconnect_limit=2,read_poll_seconds=.01,idle_diagnostic_ns=2_000_000_000)
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=200000,reserve_final_bytes=30000,min_free_bytes=1,max_record_bytes=4096,free_space=lambda _:999999)
    engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
    adapter=PublicMarketAdapter(config,engine,FakeConnector([first,second]),snaps,clock,sleep=lambda s:_advance(clock,s))
    report=run(adapter)
    assert report.stop_reason=="input_exhausted" and report.session_boundaries==["public-1","public-2"]
    assert snaps.cancelled==1 and adapter.telemetry.reconnects==1
    assert report.book["valid"] and report.book["update_id"]==201


def test_http_timeout_rate_limit_and_success_use_bounded_retries(tmp_path):
    clock=ManualClock(); ws=FakeConnection(clock,[(1,asyncio.TimeoutError()),(1,asyncio.TimeoutError()),(0,TransportEnd())])
    items=[(0,SnapshotTimeout("slow")),(0,SnapshotResponse(429,"rate",{"Retry-After":"2"})),(0,SnapshotResponse(200,snap(),{}))]
    adapter,engine,_=setup(tmp_path,clock,[ws],items)
    report=run(adapter)
    assert adapter.telemetry.snapshot_attempts==3
    assert report.counters["controls_persisted"]>=5
    assert report.stop_reason=="input_exhausted"


def test_snapshot_retry_exhaustion_is_terminal(tmp_path):
    clock=ManualClock(); ws=FakeConnection(clock,[(1,asyncio.TimeoutError())]*5)
    adapter,_,_=setup(tmp_path,clock,[ws],[(0,SnapshotTimeout("a")),(0,SnapshotResponse(500,"x",{}))],snapshot_attempt_limit=2)
    report=run(adapter)
    assert report.stop_reason=="snapshot_retry_exhausted" and adapter.telemetry.snapshot_attempts==2


def test_silent_connection_deadline_and_idle_do_not_invalidate_book(tmp_path):
    clock=ManualClock(); ws=FakeConnection(clock,[(2,asyncio.TimeoutError()),(2,asyncio.TimeoutError()),(2,asyncio.TimeoutError())])
    adapter,engine,_=setup(tmp_path,clock,[ws],[(0,SnapshotResponse(200,snap(),{}))],max_elapsed_ns=5_000_000_000,idle_diagnostic_ns=1_000_000_000)
    report=run(adapter)
    assert report.stop_reason=="time_limit" and adapter.telemetry.idle_diagnostics>=1
    assert engine.book.valid


def test_gap_malformed_utc_step_and_out_of_order_ticker(tmp_path):
    clock=ManualClock()
    def utc_step(): clock.step_utc(10); return combined("depth@100ms",dep(103,103))
    ws=FakeConnection(clock,[(1,combined("bookTicker",tick(102))),(1,combined("trade","bad")),(1,utc_step),(0,TransportEnd())])
    adapter,_,_=setup(tmp_path,clock,[ws],[(0,SnapshotResponse(200,snap(),{})),
        (0,SnapshotResponse(200,snap(103),{}))])
    report=run(adapter)
    assert report.counters["unmatched"]==1 and report.counters["rejected"]==1
    assert report.counters["sequence_gaps"]==1 and report.counters["utc_steps"]>=1
    assert report.book["valid"] and report.book["update_id"]==103


def test_queue_saturation_and_oversize_are_explicit_local_losses(tmp_path):
    clock=ManualClock(); batch=[combined("trade",trade(1)),combined("trade",trade(2))]
    ws=FakeConnection(clock,[(1,batch)])
    adapter,engine,_=setup(tmp_path/"queue",clock,[ws],[(0,SnapshotResponse(200,snap(),{})),
        (0,SnapshotResponse(200,snap(),{}))],queue_capacity=1,reconnect_limit=0)
    report=run(adapter)
    assert adapter.telemetry.local_drops==1 and report.counters["queue_saturation"]==1
    assert not engine.book.valid and report.stop_reason=="input_exhausted"
    clock=ManualClock(); huge=combined("trade",trade())+"x"*5000; ws=FakeConnection(clock,[(1,huge)])
    adapter,engine,_=setup(tmp_path/"large",clock,[ws],[(0,SnapshotResponse(200,snap(),{})),
        (0,SnapshotResponse(200,snap(),{}))],max_record_bytes=1024,reconnect_limit=0)
    report=run(adapter)
    assert adapter.telemetry.oversized_messages==1 and adapter.telemetry.local_drops==1
    assert report.stop_reason=="input_exhausted"


def test_cancellation_during_pending_snapshot_closes_connection(tmp_path):
    clock=ManualClock(); cancellation=asyncio.Event()
    def cancel_now(): cancellation.set(); return asyncio.TimeoutError()
    ws=FakeConnection(clock,[(1,cancel_now)])
    snaps=FakeSnapshots(clock,["BLOCK"])
    config=PublicAdapterConfig(max_elapsed_ns=20_000_000_000,max_total_bytes=200000,min_free_bytes=1,reserve_final_bytes=30000,queue_capacity=8,max_record_bytes=4096,snapshot_attempt_limit=2,reconnect_limit=1,read_poll_seconds=.01,idle_diagnostic_ns=2_000_000_000)
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=200000,reserve_final_bytes=30000,min_free_bytes=1,max_record_bytes=4096,free_space=lambda _:999999)
    engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
    adapter=PublicMarketAdapter(config,engine,FakeConnector([ws]),snaps,clock,sleep=lambda s:_advance(clock,s))
    report=run(adapter,cancellation=cancellation)
    assert report.stop_reason=="cancelled" and ws.closed and snaps.cancelled==1


def test_stop_during_reconnect_and_writer_failure_are_bounded(tmp_path):
    clock=ManualClock(); holder={}
    async def stop_sleep(seconds): clock.advance(seconds); holder["adapter"].cancel(); await asyncio.sleep(0)
    config=PublicAdapterConfig(max_elapsed_ns=20_000_000_000,max_total_bytes=200000,min_free_bytes=1,reserve_final_bytes=30000,queue_capacity=8,max_record_bytes=4096,reconnect_limit=2)
    writer=BoundedRunWriter(tmp_path/"reconnect","run",max_total_bytes=200000,reserve_final_bytes=30000,min_free_bytes=1,max_record_bytes=4096,free_space=lambda _:999999)
    engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
    adapter=PublicMarketAdapter(config,engine,FakeConnector([TransportDisconnected("connect")]),FakeSnapshots(clock,[]),clock,sleep=stop_sleep); holder["adapter"]=adapter
    assert run(adapter).stop_reason=="cancelled"

    class FailFile:
        def write(self,data): raise OSError("disk")
        def close(self): pass
    clock=ManualClock(); ws=FakeConnection(clock,[(1,combined("trade",trade()))])
    writer=BoundedRunWriter(tmp_path/"writer","run",max_total_bytes=200000,reserve_final_bytes=30000,min_free_bytes=1,max_record_bytes=4096,free_space=lambda _:999999,raw_opener=lambda *a:FailFile())
    engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
    adapter=PublicMarketAdapter(config,engine,FakeConnector([ws]),FakeSnapshots(clock,[(0,SnapshotResponse(200,snap(),{}))]),clock,sleep=lambda s:_advance(clock,s))
    report=run(adapter)
    assert report.stop_reason=="disk_write_failure" and writer.closed


def test_cap_and_free_space_stops_remain_explicit(tmp_path):
    clock=ManualClock(); raw=combined("trade",trade())
    ws=FakeConnection(clock,[(1,raw)])
    config=PublicAdapterConfig(max_elapsed_ns=20_000_000_000,max_total_bytes=900,min_free_bytes=1,reserve_final_bytes=100,queue_capacity=8,max_record_bytes=4096,reconnect_limit=0)
    writer=BoundedRunWriter(tmp_path/"cap","run",max_total_bytes=900,reserve_final_bytes=100,min_free_bytes=1,max_record_bytes=4096,free_space=lambda _:999999)
    engine=CaptureEngine(CaptureConfig(max_record_bytes=4096),writer=writer)
    adapter=PublicMarketAdapter(config,engine,FakeConnector([ws]),FakeSnapshots(clock,[(0,SnapshotResponse(200,snap(),{}))]),clock,sleep=lambda s:_advance(clock,s))
    assert run(adapter).stop_reason=="storage_cap"

    with pytest.raises(FreeSpaceError): preflight(PublicAdapterConfig(),tmp_path/"free","run",free_space=lambda _:1)
