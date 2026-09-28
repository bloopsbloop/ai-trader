from datetime import datetime, timedelta, timezone
import json

import pytest

from app.news.backfill import run_backfill
from app.news.collect import collect_once
from app.news.coverage import coverage_report, export_coverage
from app.news.models import RawNewsItem
from app.news.normalize import EVENT_TYPES, map_symbols, normalize_item
from app.news.storage import NewsArchive

NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def raw(title="Bitcoin ETF approved", url="https://news.test/a", published=None, **kwargs):
    return RawNewsItem(source=kwargs.get("source", "source"), source_type=kwargs.get("source_type", "rss"),
                       title=title, url=url,
                       raw_published_at=published or (NOW - timedelta(hours=1)).isoformat(),
                       summary=kwargs.get("summary", ""),
                       source_article_id=kwargs.get("source_id"),
                       raw_updated_at=kwargs.get("updated"), language="en",
                       raw_payload=kwargs.get("payload", {"title": title}),
                       archive_observed_at=kwargs.get("archive_observed"))


def normalized(**kwargs):
    return normalize_item(raw(**kwargs), NOW, historical_import=False)


def test_normalization_timestamp_semantics_and_raw_preservation():
    article = normalized()
    assert article.published_at < article.first_seen_at == NOW.isoformat()
    assert article.timestamp_confidence == "source_reported"
    assert article.first_seen_confidence == "observed_by_collector"
    assert article.ml_eligible
    assert article.raw_published_at and article.raw_payload_hash and article.content_hash
    assert article.scorer_method == "deterministic_headline_rules"
    assert article.scorer_version == "historical-headline-v1"


def test_imported_history_never_silently_uses_publication_as_first_seen():
    item = raw(archive_observed=(NOW - timedelta(minutes=30)).isoformat())
    article = normalize_item(item, NOW, historical_import=True,
                             source_guarantees_availability=False)
    assert article.first_seen_at is None
    assert article.archive_observed_at != article.published_at
    assert not article.ml_eligible
    assert "historical_availability_unknown" in article.ml_ineligible_reason


def test_future_publication_is_ineligible_and_cannot_leak():
    article = normalized(published=(NOW + timedelta(minutes=1)).isoformat())
    assert article.published_at > article.first_seen_at
    assert not article.ml_eligible
    assert "publication_after_availability" in article.ml_ineligible_reason


def test_symbol_market_mapping_event_taxonomy_and_sentiment():
    mapping = map_symbols("$BTC and Ethereum partner with Solana")
    assert mapping == {"BTC": .99, "ETH": .95, "SOL": .95}
    article = normalized(title="Federal Reserve regulation affects crypto market")
    assert article.market_wide and not article.symbols
    assert article.event_type in EVENT_TYPES
    assert -1 <= article.sentiment <= 1 and 0 <= article.importance <= 1


def test_strong_dedup_and_distinct_far_update(tmp_path):
    archive = NewsArchive(tmp_path / "archive.sqlite3")
    first = normalized()
    tracking = normalized(url="https://news.test/a?utm_source=rss")
    syndicated = normalized(url="https://other.test/copy")
    later = normalize_item(raw(url="https://other.test/later",
                               published=(NOW + timedelta(days=2)).isoformat()),
                           NOW + timedelta(days=2, hours=1), historical_import=False)
    stats = archive.ingest([first, tracking, syndicated, later])
    assert stats == {"fetched": 4, "inserted": 2, "duplicates": 2}
    assert len(archive.articles()) == 2
    with archive.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM news_duplicates").fetchone()[0] == 2


class Adapter:
    def __init__(self):
        self.calls = 0
    def source_name(self): return "fixture"
    def supports_backfill(self): return True
    def rate_limit_info(self): return {"minimum_interval_seconds": 0}
    def fetch(self, start=None, end=None, symbols=(), limit=250):
        self.calls += 1
        return [raw(url=f"https://news.test/{start.date() if start else 'current'}")]
    def normalize(self, item, collected_at=None, historical_import=False):
        return normalize_item(item, collected_at, historical_import=historical_import,
                              source_guarantees_availability=False)


def test_backfill_resume_and_duplicate_safe_ingestion(tmp_path):
    archive, adapter = NewsArchive(tmp_path / "archive.sqlite3"), Adapter()
    first = run_backfill(archive, adapter, NOW - timedelta(days=1), NOW,
                         limit=10, resume=True, sleep=lambda _: None)
    second = run_backfill(archive, adapter, NOW - timedelta(days=1), NOW,
                          limit=10, resume=True, sleep=lambda _: None)
    assert first["inserted"] == 1 and second["windows_skipped"] == 1
    assert adapter.calls == 1 and len(archive.articles()) == 1


def test_incremental_collection_preserves_earliest_first_seen(tmp_path):
    archive, adapter = NewsArchive(tmp_path / "archive.sqlite3"), Adapter()
    collect_once(archive, [adapter], collected_at=NOW)
    collect_once(archive, [adapter], collected_at=NOW + timedelta(hours=1))
    rows = archive.articles()
    assert len(rows) == 1 and rows[0]["first_seen_at"] == NOW.isoformat()


def test_coverage_report_and_exports(tmp_path):
    archive = NewsArchive(tmp_path / "archive.sqlite3")
    archive.ingest([normalized(), normalized(title="Ethereum hack", url="https://news.test/b")])
    archive.ingest([normalized()])
    report = coverage_report(archive)
    assert report["article_count"] == 2 and report["duplicates_observed"] == 1
    assert report["articles_per_symbol"]["BTC"] == 1
    assert report["event_type_distribution"]["hack"] == 1
    assert report["integrity"]["passed"]
    files = export_coverage(report, tmp_path / "reports")
    assert {"coverage.json", "coverage.md", "articles_per_day.csv",
            "articles_per_source.csv", "articles_per_symbol.csv"} == set(files)
    json.loads((tmp_path / "reports" / "coverage.json").read_text(encoding="utf-8"))


def test_unknown_timestamp_and_unmapped_story_are_ineligible():
    item = raw(title="Unrelated company earnings", published="not-a-date")
    article = normalize_item(item, NOW, historical_import=False)
    assert article.published_at is None and not article.ml_eligible
    assert "unknown_publication_time" in article.ml_ineligible_reason
    assert "no_symbol_or_market_mapping" in article.ml_ineligible_reason


def test_source_id_dedup_across_changed_urls(tmp_path):
    archive = NewsArchive(tmp_path / "archive.sqlite3")
    one = normalized(url="https://news.test/a", source_id="42")
    two = normalized(title="Bitcoin ETF updated", url="https://news.test/b", source_id="42")
    assert archive.ingest([one, two])["duplicates"] == 1
