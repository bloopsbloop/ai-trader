from datetime import datetime, timedelta, timezone
import gzip
import json

import requests

from app.ai.http import RateLimited
from app.news.adapters import GDELTAdapter
from app.news.backfill import run_backfill
from app.news.coverage import coverage_report
from app.news.export import export_archive
from app.news.models import RawNewsItem
from app.news.normalize import map_symbols, normalize_item
from app.news.provenance import SourceProvenance
from app.news.readiness import readiness_report
from app.news.storage import NewsArchive
from app.news.validate import validation_report

NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def item(source="official", title="BTC release", url="https://example.test/a", published=None):
    return RawNewsItem(source, "official_project_release", title, url,
                       (published or NOW - timedelta(days=1)).isoformat(), raw_payload={"title": title})


def trusted(raw=None, collected=NOW):
    provenance = SourceProvenance("github_releases", "official_project_release", True,
                                  "authoritative_publication", True, "high")
    return normalize_item(raw or item(), collected, historical_import=True, provenance=provenance)


def test_provenance_can_qualify_authoritative_historical_publication():
    article = trusted()
    assert article.ml_eligible
    assert article.first_seen_at is None
    assert article.availability_timestamp == article.published_at
    assert article.availability_basis == "authoritative_publication"
    assert article.historical_availability_guarantee


def test_successful_unknown_history_is_never_eligible():
    provenance = SourceProvenance("index", "search", True, "third_party_index_timestamp", False, "low")
    article = normalize_item(item(), NOW, historical_import=True, provenance=provenance)
    assert not article.ml_eligible and article.availability_timestamp is None


def test_ticker_false_positives_and_mapping_v2():
    assert "SOL" not in map_symbols("El sol shines over the city")
    assert "SOL" in map_symbols("SOL upgrade announced")
    assert "SOL" in map_symbols("$sol upgrade announced")


def test_cross_source_dedup_preserves_source_evidence(tmp_path):
    archive = NewsArchive(tmp_path / "archive.sqlite3")
    one = trusted(item("source-a", url="https://a.test/story"))
    two = trusted(item("source-b", url="https://b.test/story"))
    assert archive.ingest([one, two]) == {"fetched": 2, "inserted": 1, "duplicates": 1}
    with archive.connect() as connection:
        evidence = connection.execute("SELECT source,canonical_story_id FROM news_source_evidence").fetchone()
    assert evidence[0] == "source-b" and evidence[1] == one.canonical_story_id


class Response:
    def raise_for_status(self): pass
    def json(self): return {"articles": []}


class RetrySession:
    def __init__(self): self.calls = 0
    def get(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1: raise requests.Timeout("temporary")
        return Response()


def test_gdelt_bounded_retry():
    session = RetrySession()
    adapter = GDELTAdapter(session=session, sleep=lambda _: None, attempts=3)
    assert adapter.fetch(NOW - timedelta(days=1), NOW, ("BTC",), 10) == []
    assert session.calls == 2


class LimitedAdapter:
    def source_name(self): return "limited"
    def supports_backfill(self): return True
    def backfill_window_days(self): return 1
    def rate_limit_info(self): return {"minimum_interval_seconds": 0}
    def fetch(self, *args, **kwargs): raise RateLimited("later")


def test_rate_limited_checkpoint_is_resumable(tmp_path):
    archive = NewsArchive(tmp_path / "archive.sqlite3")
    result = run_backfill(archive, LimitedAdapter(), NOW - timedelta(days=1), NOW,
                          resume=True, sleep=lambda _: None)
    assert result["windows_rate_limited"] == 1
    assert archive.checkpoint_counts()["rate_limited"] == 1


def test_monthly_coverage_and_provenance(tmp_path):
    archive = NewsArchive(tmp_path / "archive.sqlite3")
    archive.ingest([trusted()])
    report = coverage_report(archive)
    assert report["articles_per_month"]["2026-09"] == 1
    assert report["monthly_matrix"]["2026-09"]["BTC"] == 1
    assert report["availability_basis"]["authoritative_publication"] == 1
    assert report["source_provenance"]


def test_readiness_uses_explicit_criteria(tmp_path):
    archive = NewsArchive(tmp_path / "archive.sqlite3")
    archive.ingest([trusted()])
    report = readiness_report(archive, "2026-09", "2026-09", 1, 1, 1, 1)
    assert report["status"] == "PARTIAL"
    assert report["monthly"]["2026-09"]["BTC"]["passes"]
    assert report["monthly"]["2026-09"]["ETH"]["missing_days"] == 30


def test_validation_detects_no_integrity_errors(tmp_path):
    archive = NewsArchive(tmp_path / "archive.sqlite3")
    archive.ingest([trusted()])
    assert validation_report(archive, NOW)["valid"]


def test_ml_safe_export_contains_provenance_without_raw_payload(tmp_path):
    archive = NewsArchive(tmp_path / "archive.sqlite3")
    archive.ingest([trusted()])
    output = tmp_path / "eligible.jsonl.gz"
    result = export_archive(archive, output, ml_eligible_only=True)
    with gzip.open(output, "rt", encoding="utf-8") as source:
        row = json.loads(source.readline())
    assert result["rows"] == 1
    assert row["availability_timestamp"] and row["provenance_version"]
    assert "raw_payload" not in row
