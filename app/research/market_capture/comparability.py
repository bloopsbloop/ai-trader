from __future__ import annotations

from bisect import bisect_left
from collections import Counter
import json
from pathlib import Path

from .book import BookState
from .envelope import ControlRecord, RawEnvelope
from .storage import hash_file, iter_jsonl_records


def _logical(envelope: RawEnvelope) -> tuple[str, dict[str, object]]:
    payload = json.loads(envelope.payload_raw)
    if envelope.stream == "snapshot": return "snapshot", payload
    if envelope.stream != "combined": return envelope.stream, payload
    stream = str(payload["stream"]).split("@", 1)[1]
    return ("depth" if stream.startswith("depth") else stream), payload["data"]


def audit_bookticker_comparability(run_dir: Path) -> dict[str, object]:
    raw_path = run_dir / "raw.jsonl"
    records, status = iter_jsonl_records(raw_path, max_record_bytes=2 * 1024 * 1024)
    materialized = list(records)
    depths: dict[str, list[tuple[int, int, int]]] = {}
    snapshot_counts: list[dict[str, int]] = []
    for position, record in enumerate(materialized):
        if not isinstance(record, RawEnvelope): continue
        stream, payload = _logical(record)
        if stream == "depth":
            depths.setdefault(record.session_id, []).append(
                (int(payload["u"]), position, int(payload["U"])))
        elif stream == "snapshot":
            snapshot_counts.append({"bids": len(payload["bids"]), "asks": len(payload["asks"]),
                                    "last_update_id": int(payload["lastUpdateId"])})
    monotonic_sessions = {
        session: all(items[index - 1][0] <= items[index][0] for index in range(1, len(items)))
        for session, items in depths.items()
    }

    def future_relation(session: str, position: int, update_id: int) -> str | None:
        items = depths.get(session, [])
        if not items or not monotonic_sessions.get(session, False): return None
        ends = [item[0] for item in items]
        index = bisect_left(ends, update_id)
        for end, depth_position, start in items[max(0, index - 1):index + 3]:
            if depth_position > position and start <= update_id <= end:
                return "exact" if end == update_id else "inside_packet"
        return None

    book = BookState(checkpoint_limit=32, buffer_limit=256)
    current_session: str | None = None
    exact_seen: dict[str, set[int]] = {}
    online = Counter(); causes = Counter(); unavailable_future = Counter()
    for position, record in enumerate(materialized):
        if isinstance(record, ControlRecord):
            if record.kind == "continuity_reset": book.start_epoch("continuity_reset")
            continue
        if current_session != record.session_id:
            if current_session is not None: book.start_epoch("session_change_requires_snapshot")
            current_session = record.session_id
        stream, payload = _logical(record)
        if stream == "snapshot":
            book.load_snapshot(payload); continue
        if stream == "depth":
            book.apply_depth(payload)
            exact_seen.setdefault(record.session_id, set()).add(int(payload["u"]))
            continue
        if stream != "bookTicker": continue
        update_id = int(payload["u"]); outcome = book.compare_ticker(payload); online[outcome] += 1
        if outcome != "unmatched": continue
        future = future_relation(record.session_id, position, update_id)
        if not book.valid or book.update_id is None:
            causes["book_unavailable"] += 1
            unavailable_future[future or "no_observed_future_depth"] += 1
        elif update_id > book.update_id:
            causes[("ticker_before_future_" + future) if future else
                   "ticker_ahead_without_observed_depth"] += 1
        elif update_id in exact_seen.get(record.session_id, set()):
            causes["retention_exact_checkpoint"] += 1
        elif any(first <= update_id < last for first, last in book.batch_ranges):
            causes["inside_previously_received_depth_packet"] += 1
        else:
            causes["other_or_retention_unknown"] += 1

    stored = json.loads((run_dir / "report.json").read_text())
    original = {name: int(stored["counters"][name])
                for name in ("comparable", "unmatched", "stale", "inconsistent")}
    return {
        "version": "m20.5-bookticker-comparability-v1",
        "run_path": str(run_dir),
        "raw_sha256": hash_file(raw_path),
        "records_read": len(materialized),
        "partial_tail": status.partial_tail,
        "chronological_online_outcomes": dict(sorted(online.items())),
        "stored_capture_outcomes": original,
        "online_outcomes_match_stored": all(online.get(key, 0) == value for key, value in original.items()),
        "unmatched_denominator": original["unmatched"],
        "unmatched_cause_counts": dict(sorted(causes.items())),
        "book_unavailable_retrospective_future_relation": dict(sorted(unavailable_future.items())),
        "retrospective_future_message_knowledge_used": True,
        "retrospective_warning": (
            "Future depth records are used only to explain capture comparability and were unavailable "
            "at the time of an online decision."
        ),
        "depth_end_ids_monotonic_by_session": monotonic_sessions,
        "raw_snapshot_level_counts": snapshot_counts,
        "stored_stage_a_known_snapshot_levels": stored["book"].get("known_snapshot_levels"),
        "known_snapshot_levels_warning": (
            "The legacy Stage A field was measured after buffered depth updates and is not the raw REST snapshot size."
        ),
    }
