"""Coverage and timestamp-integrity report for the canonical news archive."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
import csv
import json
from pathlib import Path

from .storage import DEFAULT_PATH, PROJECT_ROOT, NewsArchive

DEFAULT_REPORTS = PROJECT_ROOT / "data" / "news" / "reports"


def _range(values):
    values = sorted(value for value in values if value)
    return {"start": values[0] if values else None, "end": values[-1] if values else None}


def _days_between(start, end):
    if not start or not end:
        return []
    cursor, finish = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    result = []
    while cursor <= finish:
        result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def coverage_report(archive):
    rows = archive.articles()
    with archive.connect() as connection:
        duplicates = connection.execute("SELECT COUNT(*) FROM news_duplicates").fetchone()[0]
    known_dates = [row["published_at"] or row.get("archive_observed_at") for row in rows]
    per_day = Counter(value[:10] for value in known_dates if value)
    observed_range = _range(known_dates)
    missing_days = [day for day in _days_between(observed_range["start"], observed_range["end"])
                    if not per_day[day]]
    sources = Counter(row["source"] for row in rows)
    symbols = Counter(symbol for row in rows for symbol in row["symbols"])
    timestamp_confidence = Counter(row["timestamp_confidence"] for row in rows)
    first_seen_confidence = Counter(row["first_seen_confidence"] for row in rows)
    sentiment = Counter("positive" if row["sentiment"] > 0 else "negative" if row["sentiment"] < 0 else "neutral"
                        for row in rows)
    events = Counter(row["event_type"] for row in rows)
    reasons = Counter(row["ml_ineligible_reason"] or "eligible" for row in rows)
    published_after_collection = sum(bool(row["published_at"] and row["published_at"] > row["collected_at"])
                                     for row in rows)
    availability_before_publication = sum(bool(row["published_at"] and row["first_seen_at"] and
                                               row["first_seen_at"] < row["published_at"]) for row in rows)
    imported_claiming_source_time = sum(bool(row["first_seen_confidence"] == "unknown_historical" and
                                             row["first_seen_at"]) for row in rows)
    return {"generated_at": datetime.now().astimezone().isoformat(), "article_count": len(rows),
            "ml_eligible_count": sum(row["ml_eligible"] for row in rows),
            "market_wide_count": sum(row["market_wide"] for row in rows),
            "published_range": _range([row["published_at"] for row in rows]),
            "archive_observed_range": _range([row.get("archive_observed_at") for row in rows]),
            "collection_range": _range([row["collected_at"] for row in rows]),
            "coverage_date_range": observed_range,
            "articles_per_day": dict(sorted(per_day.items())),
            "articles_per_source": dict(sorted(sources.items())),
            "articles_per_symbol": dict(sorted(symbols.items())),
            "missing_day_gaps": missing_days,
            "timestamp_confidence": dict(sorted(timestamp_confidence.items())),
            "first_seen_confidence": dict(sorted(first_seen_confidence.items())),
            "duplicates_observed": duplicates,
            "duplicate_rate": duplicates / (len(rows) + duplicates) if rows or duplicates else 0.0,
            "sentiment_distribution": dict(sorted(sentiment.items())),
            "event_type_distribution": dict(sorted(events.items())),
            "ml_eligibility_reasons": dict(sorted(reasons.items())),
            "integrity": {"published_after_collected": published_after_collection,
                          "first_seen_before_published": availability_before_publication,
                          "unknown_historical_with_claimed_first_seen": imported_claiming_source_time,
                          "passed": not (published_after_collection or availability_before_publication or
                                         imported_claiming_source_time)}}


def _csv(path, mapping, first, second):
    with Path(path).open("w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target)
        writer.writerow((first, second))
        writer.writerows(mapping.items())


def export_coverage(report, output=DEFAULT_REPORTS):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "coverage.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    markdown = ["# Historical news coverage", "",
                f"- Articles: {report['article_count']}",
                f"- ML eligible: {report['ml_eligible_count']}",
                f"- Coverage: {report['coverage_date_range']['start']} — {report['coverage_date_range']['end']}",
                f"- Market-wide: {report['market_wide_count']}",
                f"- Duplicates observed: {report['duplicates_observed']} ({report['duplicate_rate']:.2%})",
                f"- Missing days: {len(report['missing_day_gaps'])}",
                f"- Integrity checks passed: {report['integrity']['passed']}", "",
                "Imported archive timestamps are reported separately from source publication and local first-seen times."]
    (output / "coverage.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    _csv(output / "articles_per_day.csv", report["articles_per_day"], "date", "articles")
    _csv(output / "articles_per_source.csv", report["articles_per_source"], "source", "articles")
    _csv(output / "articles_per_symbol.csv", report["articles_per_symbol"], "symbol", "articles")
    return sorted(path.name for path in output.iterdir())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analyze historical news archive coverage")
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--output", default=str(DEFAULT_REPORTS))
    args = parser.parse_args(argv)
    report = coverage_report(NewsArchive(args.db))
    report["files"] = export_coverage(report, args.output)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
