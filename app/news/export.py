"""Leakage-safe compressed JSONL export."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

from .storage import DEFAULT_PATH, PROJECT_ROOT, NewsArchive

DEFAULT_DIR = PROJECT_ROOT / "data" / "news" / "exports"
EXPORT_FIELDS = ("id", "canonical_story_id", "source", "source_type", "source_article_id",
                 "title", "summary", "url", "published_at", "availability_timestamp",
                 "availability_basis", "availability_confidence", "collected_at", "symbols",
                 "symbol_confidence", "mapping_method", "mapping_version", "market_wide",
                 "event_type", "sentiment", "importance", "confidence", "scorer_method",
                 "scorer_version", "source_timestamp_basis", "historical_availability_guarantee",
                 "provenance_version", "ml_eligible", "ml_ineligible_reason", "content_hash")


def export_archive(archive, output=None, ml_eligible_only=False):
    output = Path(output or (DEFAULT_DIR / ("ml_eligible.jsonl.gz" if ml_eligible_only else "archive.jsonl.gz")))
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [row for row in archive.articles() if not ml_eligible_only or row["ml_eligible"]]
    if ml_eligible_only:
        rows = [row for row in rows if row["availability_timestamp"]]
    with gzip.open(output, "wt", encoding="utf-8", newline="\n") as target:
        for row in rows:
            target.write(json.dumps({field: row.get(field) for field in EXPORT_FIELDS}, ensure_ascii=False) + "\n")
    return {"path": str(output), "rows": len(rows), "ml_eligible_only": ml_eligible_only,
            "generated_at": datetime.now(timezone.utc).isoformat()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export normalized historical news")
    parser.add_argument("--db", default=str(DEFAULT_PATH)); parser.add_argument("--output")
    parser.add_argument("--ml-eligible-only", action="store_true"); args = parser.parse_args(argv)
    print(json.dumps(export_archive(NewsArchive(args.db), args.output, args.ml_eligible_only), indent=2))


if __name__ == "__main__": main()
