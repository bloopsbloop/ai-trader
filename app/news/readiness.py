"""Transparent monthly research-readiness assessment."""
from __future__ import annotations

import argparse
import calendar
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

from .storage import DEFAULT_PATH, PROJECT_ROOT, NewsArchive

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "news" / "reports" / "readiness.json"
DIMENSIONS = ("BTC", "ETH", "SOL", "market_wide")


def _months(start, end):
    year, month = map(int, start.split("-"))
    end_key = tuple(map(int, end.split("-")))
    while (year, month) <= end_key:
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month == 13: year, month = year + 1, 1


def readiness_report(archive, start="2023-01", end="2025-12", min_articles=2,
                     min_eligible=2, min_coverage_days=2, min_sources=1):
    cells = defaultdict(list)
    for row in archive.articles():
        stamp = row["availability_timestamp"] or row["published_at"] or row.get("archive_observed_at")
        if not stamp or not (start <= stamp[:7] <= end): continue
        dimensions = list(row["symbols"])
        if row["market_wide"]: dimensions.append("market_wide")
        for dimension in set(dimensions):
            if dimension in DIMENSIONS: cells[(stamp[:7], dimension)].append(row)
    matrix, passing = {}, 0
    for month in _months(start, end):
        matrix[month] = {}
        days_in_month = calendar.monthrange(*map(int, month.split("-")))[1]
        for dimension in DIMENSIONS:
            rows = cells[(month, dimension)]
            eligible = [row for row in rows if row["ml_eligible"] and row["availability_timestamp"]]
            coverage_days = len({row["availability_timestamp"][:10] for row in eligible})
            sources = len({row["source"] for row in eligible})
            passed = (len(rows) >= min_articles and len(eligible) >= min_eligible and
                      coverage_days >= min_coverage_days and sources >= min_sources)
            passing += int(passed)
            matrix[month][dimension] = {
                "article_count": len(rows), "ml_eligible_count": len(eligible),
                "coverage_days": coverage_days, "missing_days": days_in_month - coverage_days,
                "source_diversity": sources, "passes": passed}
    total = len(matrix) * len(DIMENSIONS)
    eligible_total = sum(cell["ml_eligible_count"] for row in matrix.values() for cell in row.values())
    ratio = passing / total if total else 0
    status = "USABLE_FOR_EXPERIMENT" if ratio >= .8 else "PARTIAL" if eligible_total else "NOT_READY"
    return {"generated_at": datetime.now().astimezone().isoformat(), "status": status,
            "target_period": {"start": start, "end": end},
            "criteria": {"min_articles": min_articles, "min_eligible": min_eligible,
                         "min_coverage_days": min_coverage_days, "min_sources": min_sources,
                         "usable_required_cell_ratio": .8},
            "passing_cells": passing, "total_cells": total, "passing_cell_ratio": ratio,
            "eligible_observations_across_cells": eligible_total, "monthly": matrix}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Assess monthly leakage-safe news research readiness")
    parser.add_argument("--db", default=str(DEFAULT_PATH)); parser.add_argument("--start", default="2023-01")
    parser.add_argument("--end", default="2025-12"); parser.add_argument("--min-articles", type=int, default=2)
    parser.add_argument("--min-eligible", type=int, default=2); parser.add_argument("--min-days", type=int, default=2)
    parser.add_argument("--min-sources", type=int, default=1); parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    report = readiness_report(NewsArchive(args.db), args.start, args.end, args.min_articles,
                              args.min_eligible, args.min_days, args.min_sources)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
