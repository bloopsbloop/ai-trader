"""Bounded, resumable historical import: python -m app.news.backfill."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import time

from .adapters import ADAPTERS
from .models import digest
from .storage import DEFAULT_PATH, NewsArchive


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def windows(start, end, days=1):
    cursor = start
    while cursor < end:
        following = min(end, cursor + timedelta(days=days))
        yield cursor, following
        cursor = following


def run_backfill(archive, adapter, start, end, symbols=("BTC", "ETH", "SOL"), limit=250,
                 resume=False, sleep=time.sleep):
    if not adapter.supports_backfill():
        raise ValueError(f"{adapter.source_name()} does not provide a historical backfill contract")
    query_hash = digest({"contract_version": 2, "symbols": sorted(symbols), "limit": limit})
    total = {"fetched": 0, "inserted": 0, "duplicates": 0, "windows_completed": 0,
             "windows_skipped": 0}
    intervals = list(windows(start, end))
    for index, (window_start, window_end) in enumerate(intervals):
        start_key, end_key = window_start.isoformat(), window_end.isoformat()
        if resume and archive.checkpoint_complete(adapter.source_name(), query_hash, start_key, end_key):
            total["windows_skipped"] += 1
            continue
        try:
            raw = adapter.fetch(window_start, window_end, symbols, min(limit - total["fetched"], 250))
            collected = datetime.now(timezone.utc)
            normalized = [adapter.normalize(item, collected, historical_import=True) for item in raw]
            stats = archive.ingest(normalized)
            archive.save_checkpoint(adapter.source_name(), query_hash, start_key, end_key, stats)
        except Exception:
            stats = {"fetched": 0, "inserted": 0, "duplicates": 0}
            archive.save_checkpoint(adapter.source_name(), query_hash, start_key, end_key, stats, "FAILED")
            raise
        for key in ("fetched", "inserted", "duplicates"):
            total[key] += stats[key]
        total["windows_completed"] += 1
        if total["fetched"] >= limit:
            break
        if index + 1 < len(intervals):
            sleep(adapter.rate_limit_info().get("minimum_interval_seconds", 0))
    return {**total, "source": adapter.source_name(), "start": start.isoformat(),
            "end": end.isoformat(), "symbols": list(symbols),
            "rate_limit": adapter.rate_limit_info()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded historical news archive import")
    parser.add_argument("--source", choices=ADAPTERS, default="gdelt")
    parser.add_argument("--start", required=True, help="UTC/ISO start")
    parser.add_argument("--end", required=True, help="UTC/ISO end (exclusive)")
    parser.add_argument("--symbols", default="BTC,ETH,SOL")
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    start, end = parse_time(args.start), parse_time(args.end)
    if end <= start or args.limit < 1:
        parser.error("end must be after start and limit must be positive")
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    unsupported = set(symbols) - {"BTC", "ETH", "SOL"}
    if unsupported:
        parser.error(f"unsupported symbols: {sorted(unsupported)}")
    result = run_backfill(NewsArchive(args.db), ADAPTERS[args.source](), start, end,
                          symbols, args.limit, args.resume)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
