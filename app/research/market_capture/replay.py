from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from .engine import CaptureConfig, CaptureEngine, CaptureReport
from .envelope import ControlRecord
from .storage import IntegrityError, hash_file, iter_jsonl_records


_DERIVED_FIELDS = (
    "book", "counters", "rejection_reasons", "valid_duration_ns", "invalid_duration_ns",
    "unknown_duration_intervals", "measured_duration_ns", "comparable_status", "comparable_share",
    "mismatch_rate", "observed_sequence_continuity", "sequence_observation_coverage",
    "book_invariants", "state_transitions", "session_boundaries", "stop_reason",
)


def _load_finalized(run_dir: Path) -> tuple[dict[str, object] | None, bool]:
    manifest_path, marker_path = run_dir / "manifest.json", run_dir / "manifest.sha256"
    if not manifest_path.exists() or not marker_path.exists(): return None, False
    manifest_bytes = manifest_path.read_bytes()
    expected = marker_path.read_text().strip()
    if hash_file(manifest_path) != expected: raise IntegrityError("manifest SHA-256 mismatch")
    try: manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc: raise IntegrityError("invalid finalized manifest") from exc
    if manifest.get("version") != "m20.2r-run-manifest-v2" or manifest.get("finalized") is not True:
        raise IntegrityError("incompatible finalized manifest")
    for name in ("raw.jsonl", "report.json"):
        metadata = manifest["files"][name]; path = run_dir / name
        if not path.is_file(): raise IntegrityError(f"finalized file missing: {name}")
        if path.stat().st_size != metadata["bytes"]: raise IntegrityError(f"size mismatch: {name}")
        if hash_file(path) != metadata["sha256"]: raise IntegrityError(f"SHA-256 mismatch: {name}")
    return manifest, True


def replay_run(run_dir: Path, config: CaptureConfig | None = None) -> CaptureReport:
    manifest, finalized = _load_finalized(run_dir)
    if finalized:
        saved_config = CaptureConfig.from_dict(manifest["effective_config"])  # type: ignore[arg-type,index]
        if config is not None and asdict(config) != asdict(saved_config):
            raise ValueError("replay override is incompatible with saved effective config")
        effective = saved_config
    else:
        effective = config or CaptureConfig()

    records, stream_status = iter_jsonl_records(run_dir / "raw.jsonl",
                                                 max_record_bytes=effective.max_record_bytes)
    engine = CaptureEngine(effective, processing_mode="offline_replay")
    stop_from_control = False
    for record in records:
        if isinstance(record, ControlRecord):
            engine.process_control(record); engine.counters.controls_persisted += 1; stop_from_control = True
        else:
            engine.process_envelope(record, persist=False); engine.counters.persisted += 1
    if not stop_from_control:
        if finalized:
            engine.stop_reason = str(manifest["stop_reason"])  # type: ignore[index]
            engine.stop_reason_source = "capture_metadata"
        else:
            engine.stop_reason = "recovery_unknown"
            engine.stop_reason_source = "unavailable"
    report = engine.report(); report.partial_tail_detected = stream_status.partial_tail
    report.integrity_status = "VERIFIED_FINALIZED" if finalized else "RECOVERY_UNFINALIZED"

    verification: dict[str, object] = {
        "finalized": finalized,
        "hashes_verified": finalized,
        "stop_reason_independently_replayed": stop_from_control,
        "derived_fields_match": None,
    }
    if finalized:
        stored = json.loads((run_dir / "report.json").read_text())
        current = report.as_dict()
        mismatches = [name for name in _DERIVED_FIELDS if stored.get(name) != current.get(name)]
        if mismatches: raise IntegrityError("derived replay mismatch: " + ", ".join(mismatches))
        verification["derived_fields_match"] = True
    report.replay_verification = verification
    return report