"""Incremental RSS collection: python -m app.news.collect."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json

from .adapters import ADAPTERS
from .storage import DEFAULT_PATH, NewsArchive


def collect_once(archive, adapters, limit=250, collected_at=None):
    collected_at = collected_at or datetime.now(timezone.utc)
    result = {}
    for adapter in adapters:
        try:
            raw = adapter.fetch(limit=limit)
            normalized = [adapter.normalize(item, collected_at, historical_import=False) for item in raw]
            result[adapter.source_name()] = {"ok": True, **archive.ingest(normalized),
                                             "supports_backfill": adapter.supports_backfill()}
        except Exception as exc:
            result[adapter.source_name()] = {"ok": False, "error": type(exc).__name__,
                                             "supports_backfill": adapter.supports_backfill()}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Append current public news feeds to the historical archive")
    parser.add_argument("--source", action="append", choices=("coindesk", "cointelegraph"))
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("limit must be positive")
    names = args.source or ["coindesk", "cointelegraph"]
    result = collect_once(NewsArchive(args.db), [ADAPTERS[name]() for name in names], args.limit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
