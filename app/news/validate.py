"""Archive structural and temporal integrity checks."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json

from .storage import DEFAULT_PATH, NewsArchive

VALID_CHECKPOINTS = {"PENDING", "COMPLETE", "FAILED", "RATE_LIMITED"}


def validation_report(archive, now=None):
    now = now or datetime.now(timezone.utc)
    rows = archive.articles()
    issues = []
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)): issues.append({"code": "duplicate_canonical_id"})
    for row in rows:
        def dt(name):
            value = row.get(name)
            return datetime.fromisoformat(value) if value else None
        published, available, collected = dt("published_at"), dt("availability_timestamp"), dt("collected_at")
        if published and published > now + timedelta(minutes=5):
            issues.append({"code": "future_published_at", "id": row["id"]})
        if available and available > now + timedelta(minutes=5):
            issues.append({"code": "future_availability", "id": row["id"]})
        if published and available and published > available:
            issues.append({"code": "availability_before_publication", "id": row["id"]})
        if available and collected and available > collected:
            issues.append({"code": "availability_after_collection", "id": row["id"]})
        if row["ml_eligible"] and not available:
            issues.append({"code": "eligible_without_availability", "id": row["id"]})
        if not row["provenance_version"] or not row["source_timestamp_basis"]:
            issues.append({"code": "missing_source_metadata", "id": row["id"]})
        if set(row["symbols"]) - {"BTC", "ETH", "SOL"}:
            issues.append({"code": "broken_symbol_mapping", "id": row["id"]})
    with archive.connect() as connection:
        statuses = [row[0] for row in connection.execute("SELECT DISTINCT status FROM news_backfill_checkpoints")]
        orphaned = connection.execute("""
            SELECT COUNT(*) FROM news_source_evidence e
            LEFT JOIN news_articles a ON a.id=e.canonical_article_id WHERE a.id IS NULL
        """).fetchone()[0]
    for status in statuses:
        if status.upper() not in VALID_CHECKPOINTS: issues.append({"code": "checkpoint_corruption", "status": status})
    if orphaned: issues.append({"code": "orphaned_source_evidence", "count": orphaned})
    counts = {}
    for issue in issues: counts[issue["code"]] = counts.get(issue["code"], 0) + 1
    return {"valid": not issues, "article_count": len(rows), "issue_count": len(issues),
            "issues_by_code": counts, "issues": issues, "checkpoint_statuses": archive.checkpoint_counts()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate news archive integrity")
    parser.add_argument("--db", default=str(DEFAULT_PATH)); args = parser.parse_args(argv)
    report = validation_report(NewsArchive(args.db)); print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__": raise SystemExit(main())
