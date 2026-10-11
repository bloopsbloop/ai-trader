from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from app.research.market_capture.book import BookState
from app.research.market_capture.engine import CaptureConfig, CaptureEngine
from app.research.market_capture.envelope import ControlRecord, RawEnvelope, SyntheticClock
from app.research.market_capture.replay import replay_run
from app.research.market_capture.storage import (
    BoundedRunWriter, IntegrityError, RecordTooLargeError, StorageLimitError,
    iter_jsonl_records,
)
from app.research.market_capture.transport import MockTransport, TransportMessage

BASE_MS = 1_800_000_000_000


def snapshot(last=100, bids=None, asks=None):
    return {"symbol":"BTCUSDT", "lastUpdateId":last,
            "bids":bids or [["100.00","2.0"]], "asks":asks or [["101.00","4.0"]]}


def depth(U, u, bids=(), asks=()):
    return {"e":"depthUpdate", "E":BASE_MS, "s":"BTCUSDT", "U":U, "u":u,
            "b":list(bids), "a":list(asks)}


def ticker(u, b="100", B="2", a="101", A="4"):
    return {"s":"BTCUSDT", "u":u, "b":b, "B":B, "a":a, "A":A}


def envelope(seq, stream, payload, mono, session="s1", utc=None):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return RawEnvelope(session, seq, stream, "mock", utc or f"2027-01-15T00:00:{mono:02d}Z",
                       mono * 1_000_000_000, raw)


def finalized_run(tmp_path: Path, config=CaptureConfig()):
    messages = [TransportMessage("snapshot", json.dumps(snapshot())),
                TransportMessage("depth", json.dumps(depth(101,101,bids=[["100","3"]])))]
    clock = SyntheticClock((("2027-01-15T00:00:00Z",0),("2027-01-15T00:00:01Z",1_000_000_000)))
    stop = SyntheticClock((("2027-01-15T00:00:03Z",3_000_000_000),))
    writer = BoundedRunWriter(tmp_path,"run",max_total_bytes=100_000,reserve_final_bytes=20_000,
                              max_record_bytes=config.max_record_bytes,free_space=lambda _:1_000_000)
    engine=CaptureEngine(config,writer=writer); engine.run(MockTransport(messages),clock,stop_clock=stop)
    report=engine.finalize(); return tmp_path/"run",report


def test_bbo_numeric_equivalence_and_real_mismatch():
    book=BookState(); book.load_snapshot(snapshot())
    assert book.compare_ticker(ticker(100,b="100.000",B="2.0000",a="101.0",A="4.000")) == "comparable"
    assert book.compare_ticker(ticker(100,A="4.0001")) == "inconsistent"


@pytest.mark.parametrize("field,value", [("b","NaN"),("B","Infinity"),("a","-1"),("A","bad")])
def test_bbo_rejects_nonfinite_negative_and_invalid(field,value):
    book=BookState(); book.load_snapshot(snapshot()); event=ticker(100); event[field]=value
    with pytest.raises(ValueError,match="invalid bookTicker numeric value"): book.compare_ticker(event)


def test_depth_validation_is_atomic_and_invalidates_continuity():
    engine=CaptureEngine(CaptureConfig()); engine.process_envelope(envelope(1,"snapshot",snapshot(),0),persist=False)
    before=(list(engine.book.serializable()["bids"]),list(engine.book.serializable()["asks"]))
    bad=depth(101,101,bids=[["99","7"]],asks=[["102","-1"]])
    engine.process_envelope(envelope(2,"depth",bad,1),persist=False)
    after=(engine.book.serializable()["bids"],engine.book.serializable()["asks"])
    assert after == before
    assert not engine.book.valid and engine.book.invalid_reason == "rejected_depth"
    assert engine.counters.rejected == 1


def test_crossed_depth_and_invalid_ids_do_not_commit():
    book=BookState(); book.load_snapshot(snapshot()); before=book.serializable()
    with pytest.raises(ValueError,match="crossed book"): book.apply_depth(depth(101,101,bids=[["102","1"]]))
    assert book.serializable()["bids"] == before["bids"] and not book.valid
    book=BookState(); book.load_snapshot(snapshot()); before=book.serializable()
    with pytest.raises(ValueError,match="U must not exceed u"): book.apply_depth(depth(102,101,bids=[["99","1"]]))
    assert book.serializable() == before


def test_snapshot_and_buffer_failure_preserve_evidence_for_recovery():
    book=BookState(); assert book.apply_depth(depth(101,101,bids=[["102","1"]])) == "buffered"
    with pytest.raises(ValueError,match="crossed book"): book.load_snapshot(snapshot())
    assert not book.valid and len(book.buffer) == 1
    outcomes=book.load_snapshot(snapshot(101))
    assert outcomes == ["stale"] and book.valid and len(book.buffer) == 0
    before=book.serializable()
    with pytest.raises(ValueError,match="crossed book"):
        book.load_snapshot(snapshot(200,bids=[["102","1"]],asks=[["101","1"]]))
    assert book.serializable() == before


def test_new_session_requires_snapshot_and_clears_checkpoints():
    engine=CaptureEngine(CaptureConfig()); engine.process_envelope(envelope(1,"snapshot",snapshot(),0,"s1"),persist=False)
    assert 100 in engine.book.checkpoints
    engine.process_envelope(envelope(1,"depth",depth(101,101),1,"s2"),persist=False)
    assert not engine.book.valid and engine.book.update_id is None
    assert len(engine.book.checkpoints)==0 and len(engine.book.buffer)==1
    assert engine.book.invalid_reason == "session_change_requires_snapshot"


def test_monotonic_reverse_and_receive_sequence_are_rejected_before_apply():
    engine=CaptureEngine(CaptureConfig()); engine.process_envelope(envelope(1,"snapshot",snapshot(),10),persist=False)
    engine.process_envelope(envelope(2,"depth",depth(101,101,bids=[["100","3"]]),5),persist=False)
    assert engine.counters.monotonic_discontinuities==1 and engine.book.update_id==100 and not engine.book.valid
    engine=CaptureEngine(CaptureConfig()); engine.process_envelope(envelope(1,"snapshot",snapshot(),0),persist=False)
    engine.process_envelope(envelope(3,"depth",depth(101,101),1),persist=False)
    assert engine.counters.receive_sequence_errors==1 and engine.book.update_id==100 and not engine.book.valid


def test_utc_step_with_forward_monotonic_is_processed():
    engine=CaptureEngine(CaptureConfig(utc_step_tolerance_ns=1))
    engine.process_envelope(envelope(1,"snapshot",snapshot(),0,utc="2027-01-15T00:00:00Z"),persist=False)
    engine.process_envelope(envelope(2,"depth",depth(101,101),1,utc="2027-01-15T00:00:10Z"),persist=False)
    assert engine.counters.utc_steps==1 and engine.book.valid and engine.book.update_id==101


def test_duration_uses_previous_state_and_closes_at_injected_stop():
    messages=[TransportMessage("snapshot",json.dumps(snapshot())),
              TransportMessage("depth",json.dumps(depth(101,101,asks=[["102","-1"]])))]
    clock=SyntheticClock((("2027-01-15T00:00:00Z",0),("2027-01-15T00:00:01Z",1_000_000_000)))
    stop=SyntheticClock((("2027-01-15T00:00:04Z",4_000_000_000),))
    report=CaptureEngine(CaptureConfig()).run(MockTransport(messages),clock,stop_clock=stop)
    assert report.valid_duration_ns==1_000_000_000
    assert report.invalid_duration_ns==3_000_000_000
    assert report.measured_duration_ns==4_000_000_000
    assert report.unknown_duration_intervals==0


def test_clock_epoch_break_is_unknown_not_negative_or_valid_duration():
    engine=CaptureEngine(CaptureConfig())
    engine.process_envelope(envelope(1,"snapshot",snapshot(),5,utc="2027-01-15T00:00:05Z"),persist=False)
    engine.process_envelope(envelope(2,"depth",depth(101,101),3,utc="2027-01-15T00:00:06Z"),persist=False)
    engine.process_control(ControlRecord(
        "stop","2027-01-15T00:00:09Z",6_000_000_000,{"reason":"input_exhausted"}))
    report=engine.report()
    assert report.unknown_duration_intervals==1
    assert report.valid_duration_ns==0 and report.invalid_duration_ns==3_000_000_000
    assert report.measured_duration_ns==3_000_000_000


def test_buffered_and_applied_are_stages_not_unique_observation_counts():
    engine=CaptureEngine(CaptureConfig())
    engine.process_envelope(envelope(1,"depth",depth(101,101),0),persist=False)
    engine.process_envelope(envelope(2,"snapshot",snapshot(),1),persist=False)
    report=engine.report()
    assert report.counters["depth_observations"]==1
    assert report.counters["depth_buffered"]==1 and report.counters["depth_applied"]==1
    assert report.sequence_observation_coverage == {"numerator":1,"denominator":1}
    assert report.observed_sequence_continuity == {"numerator":1,"denominator":1,"gaps":0}


def test_outcome_dimensions_and_message_limit_reason():
    msg=TransportMessage("snapshot",json.dumps(snapshot()))
    clock=SyntheticClock((("2027-01-15T00:00:00Z",0),("2027-01-15T00:00:01Z",1)))
    report=CaptureEngine(CaptureConfig()).run(MockTransport([msg,msg]),clock,max_messages=1)
    assert report.status=="STOPPED" and report.processing_mode=="mock_capture"
    assert report.run_outcome=="stopped" and report.stop_reason=="message_limit"
    assert report.integrity_status=="CAPTURE_NOT_REPLAY_VERIFIED"


def test_finalized_replay_uses_saved_nondefault_config_and_verifies_hashes(tmp_path):
    config=CaptureConfig(checkpoint_limit=3,buffer_limit=7,max_record_bytes=4096)
    run,capture=finalized_run(tmp_path,config); replay=replay_run(run)
    assert capture.stop_reason_source == "runtime"
    assert replay.stop_reason_source == "control_record"
    assert replay.effective_config==asdict(config)
    assert replay.integrity_status=="VERIFIED_FINALIZED"
    assert replay.replay_verification["derived_fields_match"] is True
    assert replay.replay_verification["stop_reason_independently_replayed"] is True
    assert replay.book==capture.book and replay.valid_duration_ns==3_000_000_000
    with pytest.raises(ValueError,match="incompatible"): replay_run(run,CaptureConfig())


def test_raw_and_manifest_single_byte_changes_are_detected(tmp_path):
    run,_=finalized_run(tmp_path/"raw_case")
    raw=run/"raw.jsonl"; data=bytearray(raw.read_bytes()); data[10]^=1; raw.write_bytes(data)
    with pytest.raises(IntegrityError,match="raw.jsonl"): replay_run(run)
    run,_=finalized_run(tmp_path/"manifest_case")
    manifest=run/"manifest.json"; manifest.write_bytes(manifest.read_bytes()+b" ")
    with pytest.raises(IntegrityError,match="manifest"): replay_run(run)


def test_unfinalized_partial_tail_is_recovery_and_source_is_unchanged(tmp_path):
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=10000,reserve_final_bytes=2000,free_space=lambda _:99999)
    writer.append(envelope(1,"snapshot",snapshot(),0)); writer.abort()
    raw=tmp_path/"run"/"raw.jsonl"; raw.write_bytes(raw.read_bytes()+b'{"partial":')
    before=raw.read_bytes(); replay=replay_run(tmp_path/"run")
    assert replay.integrity_status=="RECOVERY_UNFINALIZED" and replay.partial_tail_detected
    assert replay.stop_reason=="recovery_unknown" and raw.read_bytes()==before


def test_metadata_stop_reason_is_labeled_not_independently_replayed(tmp_path):
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=100000,reserve_final_bytes=10000,
                            free_space=lambda _:999999)
    engine=CaptureEngine(CaptureConfig(),writer=writer)
    engine.process_envelope(envelope(1,"snapshot",snapshot(),0))
    engine.finalize()
    replay=replay_run(tmp_path/"run")
    assert replay.stop_reason_source=="capture_metadata"
    assert replay.replay_verification["stop_reason_independently_replayed"] is False


def test_streaming_reader_and_writer_enforce_record_bound(tmp_path):
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=10000,reserve_final_bytes=100,
                            max_record_bytes=100,free_space=lambda _:99999)
    with pytest.raises(RecordTooLargeError): writer.append(envelope(1,"trade","x"*500,0))
    writer.abort(); path=tmp_path/"oversize.jsonl"; path.write_bytes(b"x"*101+b"\n")
    records,_=iter_jsonl_records(path,max_record_bytes=100)
    with pytest.raises(RecordTooLargeError): list(records)


def test_finalize_budget_failure_leaves_unfinalized_run(tmp_path):
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=900,reserve_final_bytes=100,
                            free_space=lambda _:99999)
    writer.append(envelope(1,"snapshot",snapshot(),0))
    with pytest.raises(StorageLimitError): writer.finalize({"large":"x"*700},"input_exhausted",asdict(CaptureConfig()))
    assert not (tmp_path/"run"/"manifest.sha256").exists()
    replay=replay_run(tmp_path/"run")
    assert replay.integrity_status=="RECOVERY_UNFINALIZED"


def test_raw_fsync_failure_closes_writer_and_leaves_no_final_marker(tmp_path,monkeypatch):
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=100000,reserve_final_bytes=10000,
                            free_space=lambda _:999999)
    writer.append(envelope(1,"snapshot",snapshot(),0))
    monkeypatch.setattr("app.research.market_capture.storage.os.fsync",
                        lambda _: (_ for _ in ()).throw(OSError("injected fsync failure")))
    with pytest.raises(OSError,match="fsync"): writer.finalize({"ok":True},"input_exhausted",asdict(CaptureConfig()))
    assert writer.closed and not (tmp_path/"run"/"manifest.sha256").exists()


class FinalFailFile:
    def __init__(self,path,mode,*,short=False,fail_flush=False):
        self.handle=open(path,mode); self.short=short; self.fail_flush=fail_flush; self.closed_flag=False
    def write(self,data):
        if self.short: self.handle.write(data[:-1]); return len(data)-1
        return self.handle.write(data)
    def flush(self):
        if self.fail_flush: raise OSError("injected flush failure")
        return self.handle.flush()
    def fileno(self): return self.handle.fileno()
    def close(self): self.closed_flag=True; self.handle.close()


@pytest.mark.parametrize("short,fail_flush", [(True,False),(False,True)])
def test_finalization_write_failures_close_resource_and_do_not_finalize(tmp_path,short,fail_flush):
    opened=[]
    def opener(path,mode):
        item=FinalFailFile(path,mode,short=short,fail_flush=fail_flush); opened.append(item); return item
    writer=BoundedRunWriter(tmp_path,"run",max_total_bytes=100000,reserve_final_bytes=10000,
                            free_space=lambda _:999999,final_opener=opener)
    writer.append(envelope(1,"snapshot",snapshot(),0))
    with pytest.raises(OSError): writer.finalize({"ok":True},"input_exhausted",asdict(CaptureConfig()))
    assert opened and all(item.closed_flag for item in opened)
    assert not (tmp_path/"run"/"manifest.sha256").exists()
