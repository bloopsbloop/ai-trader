from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time
from typing import Awaitable, Callable, Protocol
from uuid import uuid4

from .engine import CaptureConfig, CaptureEngine, CaptureReport
from .envelope import ControlRecord, RawEnvelope
from .queueing import BackpressureError, BoundedEventQueue
from .storage import (BoundedRunWriter, FinalizationMetadataError, FreeSpaceError,
                      MAX_FINALIZATION_BYTES, MAX_FINAL_REPORT_BYTES,
                      RecordTooLargeError, StorageLimitError, WriterClosedError)

WS_URL = "wss://data-stream.binance.vision/stream?streams=btcusdt@trade/btcusdt@bookTicker/btcusdt@depth@100ms"
SNAPSHOT_URL = "https://data-api.binance.vision/api/v3/depth"
STAGE_A_CONFIRM = "PUBLIC_MARKET_CAPTURE_AUTHORIZED"
EXTENDED_CONFIRM = "EXTENDED_PUBLIC_MARKET_CAPTURE_AUTHORIZED"
STAGE_A_MAX_NS = 1_800_000_000_000
STAGE_A_MAX_BYTES = 2 * 1024 ** 3
EXTENDED_MAX_NS = 604_800_000_000_000
EXTENDED_MAX_BYTES = 100_000_000_000
EXTENDED_HOST_RESERVE_BYTES = 50_000_000_000
EXTENDED_LINUX_RESERVE_BYTES = 10_000_000_000


class TransportDisconnected(RuntimeError): pass
class TransportEnd(RuntimeError): pass
class SnapshotTimeout(RuntimeError): pass
class SnapshotConnectionError(RuntimeError): pass
class SnapshotResponseTooLarge(RuntimeError): pass
class SnapshotExhausted(RuntimeError): pass


_STORAGE_ERRORS = (StorageLimitError, FinalizationMetadataError, FreeSpaceError,
                   RecordTooLargeError, WriterClosedError, OSError)


def _storage_reason(exc: BaseException) -> str:
    if isinstance(exc, FinalizationMetadataError): return "final_metadata_limit"
    if isinstance(exc, StorageLimitError): return "storage_cap"
    if isinstance(exc, FreeSpaceError): return "minimum_free_space"
    if isinstance(exc, RecordTooLargeError): return "record_too_large"
    return "disk_write_failure"


class RuntimeClock(Protocol):
    kind: str
    def sample(self) -> tuple[str, int]: ...


class SystemClock:
    kind = "system"
    def sample(self) -> tuple[str, int]:
        return datetime.now(timezone.utc).isoformat(), time.monotonic_ns()


@dataclass(frozen=True)
class PublicAdapterConfig:
    profile: str = "stage-a"
    symbol: str = "BTCUSDT"
    max_elapsed_ns: int = 1_800_000_000_000
    max_total_bytes: int = 2 * 1024 ** 3
    min_free_bytes: int = 4 * 1024 ** 3
    reserve_final_bytes: int = 1024 * 1024
    queue_capacity: int = 4096
    max_record_bytes: int = 2 * 1024 * 1024
    snapshot_limit: int = 5000
    snapshot_timeout_seconds: float = 10.0
    snapshot_attempt_limit: int = 3
    recovery_limit: int = 3
    reconnect_limit: int = 3
    connect_timeout_seconds: float = 10.0
    cleanup_timeout_seconds: float = 2.0
    read_poll_seconds: float = 5.0
    idle_diagnostic_ns: int = 30_000_000_000
    host_min_free_bytes: int = 0
    host_check_interval_bytes: int = 64 * 1024 * 1024
    max_final_report_bytes: int = MAX_FINAL_REPORT_BYTES
    report_schema_version: str = "bounded-book-v2"
    diagnostic_history_limit: int = 256
    book_level_limit: int = 20_000
    strict_profile_limits: bool = False

    def validate(self) -> None:
        if self.symbol != "BTCUSDT": raise ValueError("public capture is BTCUSDT-only")
        if self.profile not in {"stage-a", "extended"}: raise ValueError("unknown capture profile")
        max_ns = STAGE_A_MAX_NS if self.profile == "stage-a" else EXTENDED_MAX_NS
        max_bytes = STAGE_A_MAX_BYTES if self.profile == "stage-a" else EXTENDED_MAX_BYTES
        if self.max_elapsed_ns <= 0 or self.max_elapsed_ns > max_ns:
            raise ValueError("deadline exceeds selected profile")
        if self.max_total_bytes <= 0 or self.max_total_bytes > max_bytes:
            raise ValueError("storage cap exceeds selected profile")
        positive = (self.min_free_bytes, self.reserve_final_bytes, self.queue_capacity,
                    self.max_record_bytes, self.snapshot_attempt_limit, self.recovery_limit,
                    self.connect_timeout_seconds, self.cleanup_timeout_seconds,
                    self.read_poll_seconds, self.snapshot_timeout_seconds)
        if min(positive) <= 0: raise ValueError("limits must be positive")
        if self.snapshot_limit != 5000: raise ValueError("snapshot limit is frozen at 5000")
        if self.reconnect_limit < 0: raise ValueError("reconnect limit must be non-negative")
        if self.strict_profile_limits and self.reserve_final_bytes < MAX_FINALIZATION_BYTES:
            raise ValueError("final reserve is smaller than the bounded final metadata budget")
        if self.max_final_report_bytes > MAX_FINAL_REPORT_BYTES or self.max_final_report_bytes < 1:
            raise ValueError("final report bound is invalid")
        if self.report_schema_version != "bounded-book-v2":
            raise ValueError("live profiles require bounded-book-v2 reports")
        if self.diagnostic_history_limit < 1 or self.book_level_limit < self.snapshot_limit:
            raise ValueError("bounded diagnostic or book limit is invalid")
        if self.host_check_interval_bytes < 1 or self.host_min_free_bytes < 0:
            raise ValueError("host-space limits are invalid")
        if self.profile == "extended" and self.host_min_free_bytes < EXTENDED_HOST_RESERVE_BYTES:
            raise ValueError("extended profile requires the frozen physical-host reserve")


def capture_profile(name: str, *, duration_seconds: float | None = None) -> PublicAdapterConfig:
    if name == "stage-a":
        seconds = 1800.0 if duration_seconds is None else duration_seconds
        return PublicAdapterConfig(profile=name, max_elapsed_ns=int(seconds * 1_000_000_000),
                                   strict_profile_limits=True)
    if name == "extended":
        seconds = 604800.0 if duration_seconds is None else duration_seconds
        return PublicAdapterConfig(profile=name, max_elapsed_ns=int(seconds * 1_000_000_000),
            max_total_bytes=EXTENDED_MAX_BYTES, min_free_bytes=EXTENDED_LINUX_RESERVE_BYTES,
            host_min_free_bytes=EXTENDED_HOST_RESERVE_BYTES, strict_profile_limits=True)
    raise ValueError("unknown capture profile")


@dataclass(frozen=True)
class SnapshotResponse:
    status: int
    body: str
    headers: dict[str, str]
    outcome: str = "ok"

    def header(self, name: str) -> str | None:
        wanted = name.casefold()
        return next((value for key, value in self.headers.items() if key.casefold() == wanted), None)


class SnapshotClient(Protocol):
    async def fetch(self, url: str, params: dict[str, object], timeout: float) -> SnapshotResponse: ...


class WebSocketConnection(Protocol):
    async def recv(self) -> str | list[str]: ...
    async def close(self) -> None: ...


class WebSocketConnector(Protocol):
    async def connect(self, url: str, *, open_timeout: float, max_size: int,
                      max_queue: int) -> WebSocketConnection: ...


@dataclass
class AdapterTelemetry:
    received: int = 0
    queued: int = 0
    processed: int = 0
    received_bytes: int = 0
    queued_bytes: int = 0
    local_drops: int = 0
    queue_high_water: int = 0
    reconnects: int = 0
    consecutive_reconnects_current: int = 0
    max_consecutive_reconnects: int = 0
    snapshot_attempts: int = 0
    recovery_cycles: int = 0
    consecutive_recoveries_current: int = 0
    max_consecutive_recoveries: int = 0
    idle_diagnostics: int = 0
    oversized_messages: int = 0
    cpu_peak_percent: str = "UNAVAILABLE"
    ram_peak_bytes: str = "UNAVAILABLE"


class RequestsSnapshotClient:
    """Bounded requests wrapper. Cancellation waits for the bounded worker to exit."""
    def __init__(self, *, max_response_bytes: int = 2 * 1024 * 1024) -> None:
        self.max_response_bytes = max_response_bytes

    def _call(self, url: str, params: dict[str, object], timeout: float) -> SnapshotResponse:
        import requests
        try:
            response = requests.get(url, params=params, timeout=(timeout, timeout), stream=True,
                headers={"User-Agent": "AI-Trader-M20.3-public-capture"})
            try:
                headers = {str(k): str(v) for k, v in response.headers.items()}
                length = next((v for k, v in headers.items() if k.casefold() == "content-length"), None)
                if length is not None:
                    try:
                        if int(length) > self.max_response_bytes:
                            raise SnapshotResponseTooLarge("snapshot Content-Length exceeds limit")
                    except ValueError: pass
                body = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk: continue
                    body.extend(chunk)
                    if len(body) > self.max_response_bytes:
                        raise SnapshotResponseTooLarge("snapshot response exceeds limit")
                outcome = "ok"
                try: text = bytes(body).decode(response.encoding or "utf-8", errors="strict")
                except UnicodeDecodeError:
                    text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
                    outcome = "malformed_encoding"
                if response.status_code == 200 and outcome == "ok":
                    try:
                        if not isinstance(json.loads(text), dict): outcome = "malformed_json"
                    except json.JSONDecodeError: outcome = "malformed_json"
                elif response.status_code in {418, 429}: outcome = "rate_limited"
                elif response.status_code >= 500: outcome = "server_error"
                elif response.status_code != 200: outcome = "http_error"
                return SnapshotResponse(response.status_code, text, headers, outcome)
            finally:
                close = getattr(response, "close", None)
                if close is not None: close()
        except requests.Timeout as exc: raise SnapshotTimeout(str(exc)) from exc
        except requests.ConnectionError as exc: raise SnapshotConnectionError(str(exc)) from exc

    async def fetch(self, url: str, params: dict[str, object], timeout: float) -> SnapshotResponse:
        worker = asyncio.create_task(asyncio.to_thread(self._call, url, params, timeout))
        try: return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await worker
            raise


class _WebsocketsConnection:
    def __init__(self, socket: object) -> None: self.socket = socket
    async def recv(self) -> str:
        try: return await self.socket.recv()  # type: ignore[attr-defined,no-any-return]
        except Exception as exc: raise TransportDisconnected(str(exc)) from exc
    async def close(self) -> None: await self.socket.close()  # type: ignore[attr-defined]


class WebsocketsConnector:
    async def connect(self, url: str, *, open_timeout: float, max_size: int,
                      max_queue: int) -> WebSocketConnection:
        import websockets
        socket = await websockets.connect(url, open_timeout=open_timeout, max_size=max_size,
            max_queue=max_queue, ping_interval=None, close_timeout=5)
        return _WebsocketsConnection(socket)


def preflight(config: PublicAdapterConfig, output_root: Path, run_id: str,
              *, free_space: Callable[[Path], int] | None = None,
              host_free_space: Callable[[], int] | None = None) -> dict[str, object]:
    config.validate()
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be one path component")
    root = output_root.resolve(); prospective = (root / run_id).resolve()
    if prospective.parent != root: raise ValueError("run path escapes output root")
    root.mkdir(parents=True, exist_ok=True)
    if prospective.exists(): raise FileExistsError(prospective)
    probe = root / f".m20_3_write_probe.{uuid4().hex}"; created = False
    try:
        with probe.open("xb") as handle: created = True; handle.write(b"")
    finally:
        if created: probe.unlink(missing_ok=True)
    available = (free_space or (lambda path: shutil.disk_usage(path).free))(root)
    required = config.max_total_bytes + config.min_free_bytes
    if available < required: raise FreeSpaceError(f"preflight requires {required} bytes free")
    host_available: int | None = None; host_required: int | None = None
    if config.profile == "extended":
        if host_free_space is None: raise FreeSpaceError("extended preflight requires physical-host monitoring")
        try: host_available = host_free_space()
        except Exception as exc: raise FreeSpaceError("physical-host free-space check unavailable") from exc
        host_required = config.max_total_bytes + config.host_min_free_bytes
        if host_available < host_required:
            raise FreeSpaceError(f"extended preflight requires {host_required} physical-host bytes free")
    return {"network_performed": False, "profile": config.profile,
            "symbol": config.symbol, "websocket_url": WS_URL,
            "snapshot_url": SNAPSHOT_URL, "deadline_seconds": config.max_elapsed_ns / 1e9,
            "storage_cap_bytes": config.max_total_bytes, "minimum_free_after_write_bytes": config.min_free_bytes,
            "minimum_free_at_start_bytes": required, "available_bytes": available,
            "physical_host_available_bytes": host_available,
            "physical_host_minimum_at_start_bytes": host_required,
            "finalization_reserve_bytes": config.reserve_final_bytes,
            "bounded_finalization_max_bytes": MAX_FINALIZATION_BYTES,
            "output_path": str(prospective), "status": "READY_FOR_SEPARATE_LIVE_AUTHORIZATION"}


class PublicMarketAdapter:
    def __init__(self, config: PublicAdapterConfig, engine: CaptureEngine,
                 connector: WebSocketConnector, snapshot_client: SnapshotClient,
                 clock: RuntimeClock, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        config.validate(); self.config=config; self.engine=engine; self.writer=engine.writer
        if self.writer is None: raise ValueError("adapter requires bounded writer")
        self.connector=connector; self.snapshot_client=snapshot_client; self.clock=clock; self.sleep=sleep
        self.queue: BoundedEventQueue[RawEnvelope] = BoundedEventQueue(config.queue_capacity)
        self.telemetry=AdapterTelemetry(); self.cancelled=False; self._sequence: dict[str,int]={}
        self._last_message_mono: int | None=None; self._idle_reported=False; self._started_mono=0

    def cancel(self) -> None: self.cancelled=True

    def _remaining_seconds(self) -> float:
        _, now = self.clock.sample()
        return max(0.0, (self.config.max_elapsed_ns - (now - self._started_mono)) / 1e9)

    def _next_envelope(self, session: str, stream: str, source: str, raw: str,
                       metadata: dict[str,object] | None=None) -> RawEnvelope:
        utc,mono=self.clock.sample(); seq=self._sequence.get(session,0)+1; self._sequence[session]=seq
        return RawEnvelope(session,seq,stream,source,utc,mono,raw,clock_kind=self.clock.kind,
                           timestamp_unit=self.engine.config.timestamp_unit,metadata=metadata or {})

    def _control(self, kind: str, detail: dict[str,object]) -> None:
        utc,mono=self.clock.sample(); record=ControlRecord(kind,utc,mono,detail,clock_kind=self.clock.kind)
        self.writer.append(record); self.engine.process_control(record); self.engine.counters.controls_persisted+=1

    async def _sleep_with_budget(self, delay: float) -> bool:
        remaining=self._remaining_seconds()
        if remaining <= 0: return False
        await self.sleep(min(delay,remaining))
        return self._remaining_seconds() > 0

    async def _cancel_task(self, task: asyncio.Task[object] | None) -> None:
        if task is None: return
        if not task.done(): task.cancel()
        try: await task
        except (asyncio.CancelledError, Exception): pass

    async def _close(self, connection: WebSocketConnection) -> None:
        task=asyncio.create_task(connection.close(),name="websocket-close")
        try: await asyncio.wait_for(asyncio.shield(task),timeout=self.config.cleanup_timeout_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError): await self._cancel_task(task)
        else:
            if task.done(): task.exception()

    @staticmethod
    def _is_depth(raw: str) -> bool:
        try:
            value=json.loads(raw)
            return isinstance(value,dict) and str(value.get("stream","")).split("@",1)[-1].startswith("depth")
        except (json.JSONDecodeError,UnicodeDecodeError): return False

    def _ingest_batch(self, session: str, raws: list[str]) -> bool:
        recovery=False
        for raw in raws:
            size=len(raw.encode()); self.telemetry.received+=1; self.telemetry.received_bytes+=size
            if size > self.config.max_record_bytes:
                self.telemetry.oversized_messages+=1; self.telemetry.local_drops+=1
                self._control("book_invalidate",{"reason":"oversized_message_loss"}); recovery=True; continue
            envelope=self._next_envelope(session,"combined","binance_public_ws",raw,{"endpoint":"combined_stream"})
            try: self.queue.put_nowait(envelope)
            except BackpressureError:
                self.telemetry.local_drops+=1; self.engine.counters.queue_saturation+=1
                self._control("book_invalidate",{"reason":"queue_saturation_loss"}); recovery=True; continue
            self.telemetry.queued+=1; self.telemetry.queued_bytes+=size
            self.telemetry.queue_high_water=max(self.telemetry.queue_high_water,len(self.queue))
            self._last_message_mono=envelope.receive_monotonic_ns; self._idle_reported=False
        while len(self.queue):
            envelope=self.queue.get_nowait(); self.engine.process_envelope(envelope); self.telemetry.processed+=1
            if self._is_depth(envelope.payload_raw) and not self.engine.book.valid: recovery=True
        return recovery

    async def _snapshot(self, session: str) -> RawEnvelope:
        last_reason="not_attempted"
        for attempt in range(1,self.config.snapshot_attempt_limit+1):
            remaining=self._remaining_seconds()
            if remaining <= 0: raise SnapshotExhausted("overall deadline exhausted")
            self.telemetry.snapshot_attempts+=1; self._control("snapshot_attempt",{"attempt":attempt})
            response: SnapshotResponse | None=None
            try:
                response=await self.snapshot_client.fetch(SNAPSHOT_URL,{"symbol":"BTCUSDT","limit":5000},
                                                          min(self.config.snapshot_timeout_seconds,remaining))
            except SnapshotTimeout as exc: last_reason="timeout:"+str(exc)
            except SnapshotConnectionError as exc: last_reason="connection:"+str(exc)
            except SnapshotResponseTooLarge as exc: last_reason="response_too_large:"+str(exc)
            except Exception as exc: last_reason="client_error:"+type(exc).__name__+":"+str(exc)
            else:
                last_reason=f"{response.outcome}:http_{response.status}"
                if response.status==200:
                    return self._next_envelope(session,"snapshot","binance_public_rest",response.body,
                        {"endpoint":"depth","symbol":"BTCUSDT","limit":5000,"status":response.status,
                         "headers":response.headers,"attempt":attempt,"http_outcome":response.outcome})
            self._control("snapshot_failure",{"attempt":attempt,"reason":last_reason})
            if attempt < self.config.snapshot_attempt_limit:
                delay=float(2**(attempt-1))
                if response is not None and response.status in {418,429}:
                    try: delay=max(delay,float(response.header("Retry-After") or "0"))
                    except ValueError: pass
                if not await self._sleep_with_budget(delay): break
        raise SnapshotExhausted(last_reason)

    async def _connect(self) -> WebSocketConnection:
        remaining=self._remaining_seconds()
        if remaining <= 0: raise asyncio.TimeoutError("overall deadline exhausted")
        limit=min(self.config.connect_timeout_seconds,remaining)
        return await asyncio.wait_for(self.connector.connect(WS_URL,open_timeout=limit,
            max_size=self.config.max_record_bytes,max_queue=self.config.queue_capacity),timeout=limit)

    async def run(self, *, cancellation: asyncio.Event | None=None) -> CaptureReport:
        _,self._started_mono=self.clock.sample(); session_number=0; reconnects=0
        consecutive_reconnects=0; stop_reason="running"
        storage_failed=False; connection: WebSocketConnection | None=None
        recv_task: asyncio.Task[object] | None=None; snapshot_task: asyncio.Task[object] | None=None
        try:
            while stop_reason == "running":
                if self.cancelled or (cancellation and cancellation.is_set()): stop_reason="cancelled"; break
                if self._remaining_seconds()<=0: stop_reason="time_limit"; break
                session_number+=1; session=f"public-{session_number}"
                try: connection=await self._connect()
                except asyncio.TimeoutError:
                    stop_reason="time_limit" if self._remaining_seconds()<=0 else "connect_timeout"; break
                except Exception as exc:
                    if consecutive_reconnects>=self.config.reconnect_limit: stop_reason="connect_retry_exhausted"; break
                    reconnects+=1; consecutive_reconnects+=1; self.telemetry.reconnects=reconnects
                    self.telemetry.consecutive_reconnects_current=consecutive_reconnects
                    self.telemetry.max_consecutive_reconnects=max(
                        self.telemetry.max_consecutive_reconnects,consecutive_reconnects)
                    self._control("reconnect_wait",{"reason":str(exc),"attempt":consecutive_reconnects,
                                                     "total":reconnects})
                    if not await self._sleep_with_budget(2**(consecutive_reconnects-1)): stop_reason="time_limit"; break
                    continue
                self._control("session_start",{"session":session})
                _,self._last_message_mono=self.clock.sample(); self._idle_reported=False
                snapshot_task=asyncio.create_task(self._snapshot(session),name=f"snapshot:{session}")
                recv_task=None; disconnected=False; recovery_failures=0
                while stop_reason == "running" and not disconnected:
                    if self.cancelled or (cancellation and cancellation.is_set()): stop_reason="cancelled"; break
                    remaining=self._remaining_seconds()
                    if remaining<=0: stop_reason="time_limit"; break
                    if recv_task is None: recv_task=asyncio.create_task(connection.recv(),name=f"recv:{session}")
                    owned={recv_task}
                    if snapshot_task is not None: owned.add(snapshot_task)
                    done,_=await asyncio.wait(owned,return_when=asyncio.FIRST_COMPLETED,
                                              timeout=min(self.config.read_poll_seconds,remaining))
                    if not done:
                        _,idle_now=self.clock.sample()
                        if (self._last_message_mono is not None and idle_now-self._last_message_mono>=self.config.idle_diagnostic_ns and not self._idle_reported):
                            self.telemetry.idle_diagnostics+=1; self._idle_reported=True
                            self._control("idle",{"kind":"stream_inactivity","book_invalidated":False})
                        continue
                    if snapshot_task is not None and snapshot_task in done:
                        task=snapshot_task; snapshot_task=None
                        try: snap=task.result()
                        except SnapshotExhausted as exc:
                            self._control("recovery_exhausted",{"reason":str(exc)})
                            stop_reason="time_limit" if self._remaining_seconds()<=0 else "snapshot_retry_exhausted"
                        else:
                            self.engine.process_envelope(snap)
                            if not self.engine.book.valid:
                                recovery_failures+=1; self.telemetry.recovery_cycles+=1
                                self.telemetry.consecutive_recoveries_current=recovery_failures
                                self.telemetry.max_consecutive_recoveries=max(
                                    self.telemetry.max_consecutive_recoveries,recovery_failures)
                                self._control("recovery_failure",{"attempt":recovery_failures,"reason":self.engine.book.invalid_reason or "snapshot_not_valid"})
                                if recovery_failures>=self.config.recovery_limit:
                                    self._control("recovery_exhausted",{"reason":"snapshot_not_valid"}); stop_reason="recovery_exhausted"
                                else: snapshot_task=asyncio.create_task(self._snapshot(session),name=f"snapshot:{session}:recovery")
                            else:
                                recovery_failures=0; consecutive_reconnects=0
                                self.telemetry.consecutive_recoveries_current=0
                                self.telemetry.consecutive_reconnects_current=0
                    if recv_task in done and stop_reason == "running":
                        task=recv_task; recv_task=None
                        try: value=task.result()
                        except asyncio.TimeoutError:
                            _,idle_now=self.clock.sample()
                            if (self._last_message_mono is not None and idle_now-self._last_message_mono>=self.config.idle_diagnostic_ns and not self._idle_reported):
                                self.telemetry.idle_diagnostics+=1; self._idle_reported=True
                                self._control("idle",{"kind":"stream_inactivity","book_invalidated":False})
                        except TransportEnd: stop_reason="input_exhausted"
                        except Exception as exc:
                            self._control("disconnect",{"reason":str(exc),"session":session})
                            self._control("continuity_reset",{"reason":"reconnect_requires_snapshot"}); disconnected=True
                        else:
                            raws=value if isinstance(value,list) else [value]
                            needs_recovery=self._ingest_batch(session,raws)
                            if needs_recovery and snapshot_task is None:
                                recovery_failures+=1; self.telemetry.recovery_cycles+=1
                                self.telemetry.consecutive_recoveries_current=recovery_failures
                                self.telemetry.max_consecutive_recoveries=max(
                                    self.telemetry.max_consecutive_recoveries,recovery_failures)
                                self._control("recovery_start",{"attempt":recovery_failures,"reason":self.engine.book.invalid_reason or "depth_recovery"})
                                if recovery_failures>self.config.recovery_limit:
                                    self._control("recovery_exhausted",{"reason":"recovery_limit"}); stop_reason="recovery_exhausted"
                                else: snapshot_task=asyncio.create_task(self._snapshot(session),name=f"snapshot:{session}:recovery")
                await self._cancel_task(recv_task); recv_task=None
                await self._cancel_task(snapshot_task); snapshot_task=None
                await self._close(connection); connection=None
                if stop_reason != "running": break
                if disconnected:
                    if consecutive_reconnects>=self.config.reconnect_limit: stop_reason="reconnect_exhausted"; break
                    reconnects+=1; consecutive_reconnects+=1; self.telemetry.reconnects=reconnects
                    self.telemetry.consecutive_reconnects_current=consecutive_reconnects
                    self.telemetry.max_consecutive_reconnects=max(
                        self.telemetry.max_consecutive_reconnects,consecutive_reconnects)
                    self._control("reconnect_wait",{"attempt":consecutive_reconnects,"total":reconnects})
                    if not await self._sleep_with_budget(2**(consecutive_reconnects-1)): stop_reason="time_limit"; break
        except asyncio.CancelledError: stop_reason="cancelled"
        except _STORAGE_ERRORS as exc:
            stop_reason=_storage_reason(exc); storage_failed=True; self.writer.abort()
        finally:
            await self._cancel_task(recv_task); await self._cancel_task(snapshot_task)
            if connection is not None: await self._close(connection)

        self.engine.stop_reason=stop_reason; self.engine.stop_reason_source="runtime"
        self.engine.transport_telemetry=asdict(self.telemetry)
        if storage_failed:
            report=self.engine.report(); report.integrity_status="INCOMPLETE_STORAGE_FAILURE"; return report
        try:
            if stop_reason=="cancelled": self._control("cancellation",{"pending_tasks_cancelled":True})
            utc,mono=self.clock.sample(); self.engine._persist_stop(utc,mono,self.clock.kind)
        except _STORAGE_ERRORS as exc:
            self.engine.stop_reason=_storage_reason(exc); self.writer.abort()
            report=self.engine.report(); report.integrity_status="INCOMPLETE_STORAGE_FAILURE"; return report
        return self.engine.report()


async def run_live_capture(config: PublicAdapterConfig, output_root: Path, run_id: str,
                           *, connector: WebSocketConnector | None=None,
                           snapshot_client: SnapshotClient | None=None,
                           clock: RuntimeClock | None=None,
                           host_free_space: Callable[[], int] | None=None,
                           sleep: Callable[[float], Awaitable[None]]=asyncio.sleep,
                           writer_factory: Callable[..., BoundedRunWriter]=BoundedRunWriter) -> CaptureReport:
    preflight(config,output_root,run_id,host_free_space=host_free_space)
    capture_config=CaptureConfig(symbol="BTCUSDT",queue_capacity=config.queue_capacity,
        max_record_bytes=config.max_record_bytes, report_schema_version=config.report_schema_version,
        diagnostic_history_limit=config.diagnostic_history_limit, book_level_limit=config.book_level_limit)
    writer=writer_factory(output_root,run_id,max_total_bytes=config.max_total_bytes,
                          reserve_final_bytes=config.reserve_final_bytes,min_free_bytes=config.min_free_bytes,
                          max_record_bytes=config.max_record_bytes,
                          secondary_free_space=host_free_space if config.profile=="extended" else None,
                          secondary_min_free_bytes=config.host_min_free_bytes if config.profile=="extended" else 0,
                          secondary_check_interval_bytes=config.host_check_interval_bytes,
                          max_report_bytes=config.max_final_report_bytes)
    engine=CaptureEngine(capture_config,writer=writer,processing_mode="public_capture")
    adapter=PublicMarketAdapter(config,engine,connector or WebsocketsConnector(),
        snapshot_client or RequestsSnapshotClient(max_response_bytes=config.max_record_bytes),clock or SystemClock(),sleep=sleep)
    report=await adapter.run()
    if writer.closed:
        report.integrity_status="INCOMPLETE_STORAGE_FAILURE"; return report
    try: return engine.finalize()
    except _STORAGE_ERRORS as exc:
        engine.stop_reason=_storage_reason(exc); writer.abort(); report=engine.report()
        report.integrity_status="INCOMPLETE_STORAGE_FAILURE"; return report
