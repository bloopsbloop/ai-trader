from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable, Iterator

from .envelope import ControlRecord, RawEnvelope


class StorageLimitError(RuntimeError): pass
class FreeSpaceError(RuntimeError): pass
class WriterClosedError(RuntimeError): pass
class RecordTooLargeError(RuntimeError): pass
class IntegrityError(RuntimeError): pass


def hash_file(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_bytes): digest.update(chunk)
    return digest.hexdigest()


@dataclass
class StreamStatus:
    partial_tail: bool = False
    records: int = 0


def iter_jsonl_records(path: Path, *, max_record_bytes: int = 1024 * 1024) -> tuple[Iterator[RawEnvelope | ControlRecord], StreamStatus]:
    status = StreamStatus()
    def records() -> Iterator[RawEnvelope | ControlRecord]:
        with path.open("rb") as handle:
            while True:
                line = handle.readline(max_record_bytes + 1)
                if not line: break
                if len(line) > max_record_bytes:
                    raise RecordTooLargeError("raw record exceeds configured maximum")
                if not line.endswith(b"\n"):
                    status.partial_tail = True
                    break
                value = json.loads(line)
                if value.get("record_type", "event") == "control": yield ControlRecord.from_dict(value)
                else: yield RawEnvelope.from_dict(value)
                status.records += 1
    return records(), status


def read_jsonl_with_tail_status(path: Path, *, max_record_bytes: int = 1024 * 1024) -> tuple[list[RawEnvelope | ControlRecord], bool]:
    records, status = iter_jsonl_records(path, max_record_bytes=max_record_bytes)
    result = list(records)
    return result, status.partial_tail


class BoundedRunWriter:
    """Create-only bounded JSONL writer; finalized only when the manifest exists."""
    def __init__(self, root: Path, run_id: str, *, max_total_bytes: int,
                 reserve_final_bytes: int = 4096, min_free_bytes: int = 0,
                 max_record_bytes: int = 1024 * 1024,
                 free_space: Callable[[Path], int] | None = None,
                 raw_opener: Callable[..., object] = open,
                 final_opener: Callable[..., object] = open) -> None:
        if max_total_bytes <= reserve_final_bytes or reserve_final_bytes < 1:
            raise ValueError("budget must exceed positive final reserve")
        if max_record_bytes < 1: raise ValueError("max_record_bytes must be positive")
        self.path = root / run_id; self.path.mkdir(parents=True, exist_ok=False)
        self.raw_path = self.path / "raw.jsonl"
        self._raw = raw_opener(self.raw_path, "xb")
        self.final_opener = final_opener
        self.max_total_bytes = max_total_bytes; self.reserve_final_bytes = reserve_final_bytes
        self.min_free_bytes = min_free_bytes; self.max_record_bytes = max_record_bytes
        self.free_space = free_space or (lambda p: shutil.disk_usage(p).free)
        self.closed = False; self.bytes_written = 0; self.event_records = 0; self.control_records = 0

    def _disk_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.path.rglob("*") if p.is_file())

    def _preflight(self, size: int, *, keep_reserve: bool) -> None:
        used = max(self.bytes_written, self._disk_bytes())
        reserve = self.reserve_final_bytes if keep_reserve else 0
        if used + size + reserve > self.max_total_bytes:
            raise StorageLimitError("next write would exceed total storage budget")
        if self.free_space(self.path) - size < self.min_free_bytes:
            raise FreeSpaceError("next write would cross minimum free-space stop")

    def append(self, record: RawEnvelope | ControlRecord) -> int:
        if self.closed: raise WriterClosedError("writer is closed")
        data = record.to_json_line()
        if len(data) > self.max_record_bytes: raise RecordTooLargeError("raw record exceeds configured maximum")
        self._preflight(len(data), keep_reserve=True)
        written = self._raw.write(data)  # type: ignore[attr-defined]
        if written != len(data): raise OSError("partial raw record write")
        self._raw.flush()  # type: ignore[attr-defined]
        self.bytes_written += written
        if isinstance(record, ControlRecord): self.control_records += 1
        else: self.event_records += 1
        return written

    def _close_raw(self) -> None:
        if self.closed: return
        try:
            self._raw.flush()  # type: ignore[attr-defined]
            os.fsync(self._raw.fileno())  # type: ignore[attr-defined]
        finally:
            self._raw.close()  # type: ignore[attr-defined]
            self.closed = True

    def _write_final(self, path: Path, data: bytes) -> None:
        handle = self.final_opener(path, "xb")
        try:
            written = handle.write(data)  # type: ignore[attr-defined]
            if written != len(data): raise OSError("partial finalization write")
            handle.flush()  # type: ignore[attr-defined]
            os.fsync(handle.fileno())  # type: ignore[attr-defined]
        finally:
            handle.close()  # type: ignore[attr-defined]

    def finalize(self, report: dict[str, object], stop_reason: str,
                 effective_config: dict[str, object]) -> dict[str, object]:
        if self.closed: raise WriterClosedError("writer is closed")
        self._close_raw()
        report_bytes = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
        raw_hash = hash_file(self.raw_path)
        provisional = {
            "version": "m20.2r-run-manifest-v2", "finalized": True,
            "stop_reason": stop_reason, "effective_config": effective_config,
            "schema_versions": {"event": "m20.2r-envelope-v2", "control": "m20.2r-control-v1"},
            "records": {"events": self.event_records, "controls": self.control_records},
            "files": {
                "raw.jsonl": {"bytes": self.raw_path.stat().st_size, "sha256": raw_hash},
                "report.json": {"bytes": len(report_bytes), "sha256": hashlib.sha256(report_bytes).hexdigest()},
            },
        }
        manifest_bytes = (json.dumps(provisional, indent=2, sort_keys=True) + "\n").encode()
        marker_bytes = (hashlib.sha256(manifest_bytes).hexdigest() + "\n").encode()
        self._preflight(len(report_bytes) + len(manifest_bytes) + len(marker_bytes), keep_reserve=False)
        self._write_final(self.path / "report.json", report_bytes)
        self.bytes_written += len(report_bytes)
        self._preflight(len(manifest_bytes), keep_reserve=False)
        self._write_final(self.path / "manifest.json", manifest_bytes)
        self.bytes_written += len(manifest_bytes)
        self._preflight(len(marker_bytes), keep_reserve=False)
        self._write_final(self.path / "manifest.sha256", marker_bytes)
        self.bytes_written += len(marker_bytes)
        return provisional

    def abort(self) -> None:
        if not self.closed:
            try: self._raw.close()  # type: ignore[attr-defined]
            finally: self.closed = True
