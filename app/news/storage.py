"""Canonical SQLite news archive with conservative, auditable deduplication."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .models import NormalizedArticle, canonical_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_ROOT / "data" / "news" / "archive.sqlite3"


class NewsArchive:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS archive_identity (
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_articles (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_article_id TEXT,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    published_at TEXT,
                    first_seen_at TEXT,
                    archive_observed_at TEXT,
                    collected_at TEXT NOT NULL,
                    updated_at TEXT,
                    raw_published_at TEXT,
                    raw_updated_at TEXT,
                    language TEXT NOT NULL,
                    symbols_json TEXT NOT NULL,
                    symbol_confidence_json TEXT NOT NULL,
                    mapping_method TEXT NOT NULL,
                    mapping_version TEXT NOT NULL,
                    market_wide INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    sentiment REAL NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    scorer_method TEXT NOT NULL,
                    scorer_version TEXT NOT NULL,
                    timestamp_confidence TEXT NOT NULL,
                    first_seen_confidence TEXT NOT NULL,
                    availability_timestamp TEXT,
                    availability_basis TEXT NOT NULL,
                    availability_confidence TEXT NOT NULL,
                    source_supports_backfill INTEGER NOT NULL,
                    source_timestamp_basis TEXT NOT NULL,
                    historical_availability_guarantee INTEGER NOT NULL,
                    provenance_version TEXT NOT NULL,
                    raw_payload_hash TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    title_key TEXT NOT NULL,
                    canonical_story_id TEXT NOT NULL,
                    ml_eligible INTEGER NOT NULL,
                    ml_ineligible_reason TEXT,
                    raw_payload_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS news_canonical_url ON news_articles(canonical_url);
                CREATE UNIQUE INDEX IF NOT EXISTS news_source_article_id
                    ON news_articles(source, source_article_id) WHERE source_article_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS news_content_hash ON news_articles(content_hash);
                CREATE INDEX IF NOT EXISTS news_title_time ON news_articles(title_key, published_at);
                CREATE INDEX IF NOT EXISTS news_published ON news_articles(published_at);
                CREATE TABLE IF NOT EXISTS news_duplicates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    canonical_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    raw_payload_hash TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_source_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_story_id TEXT NOT NULL,
                    canonical_article_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_article_id TEXT,
                    url TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    dedup_reason TEXT NOT NULL,
                    raw_payload_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_sources (
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    supports_backfill INTEGER NOT NULL,
                    timestamp_basis TEXT NOT NULL,
                    historical_availability_guarantee INTEGER NOT NULL,
                    timestamp_confidence TEXT NOT NULL,
                    provenance_version TEXT NOT NULL,
                    PRIMARY KEY(source_name, source_type, provenance_version)
                );
                CREATE TABLE IF NOT EXISTS news_backfill_checkpoints (
                    source TEXT NOT NULL,
                    query_hash TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fetched INTEGER NOT NULL,
                    inserted INTEGER NOT NULL,
                    duplicates INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(source, query_hash, window_start, window_end)
                );
            """)
            versions = connection.execute("SELECT schema_version FROM archive_identity").fetchall()
            if versions and versions not in ([(1,)], [(2,)]):
                raise ValueError("Unsupported news archive schema")
            if not versions:
                connection.execute("INSERT INTO archive_identity VALUES (2)")
            elif versions == [(1,)]:
                self._migrate_v1(connection)
            connection.execute("""
                INSERT OR IGNORE INTO news_sources
                SELECT DISTINCT source,source_type,source_supports_backfill,source_timestamp_basis,
                       historical_availability_guarantee,timestamp_confidence,provenance_version
                FROM news_articles
            """)

    @staticmethod
    def _migrate_v1(connection):
        columns = {row[1] for row in connection.execute("PRAGMA table_info(news_articles)")}
        additions = {
            "mapping_method": "TEXT NOT NULL DEFAULT 'legacy_entity_rules'",
            "mapping_version": "TEXT NOT NULL DEFAULT 'entity-rules-v1'",
            "availability_timestamp": "TEXT",
            "availability_basis": "TEXT NOT NULL DEFAULT 'unknown_historical_availability'",
            "availability_confidence": "TEXT NOT NULL DEFAULT 'unknown'",
            "source_supports_backfill": "INTEGER NOT NULL DEFAULT 0",
            "source_timestamp_basis": "TEXT NOT NULL DEFAULT 'unknown'",
            "historical_availability_guarantee": "INTEGER NOT NULL DEFAULT 0",
            "provenance_version": "TEXT NOT NULL DEFAULT 'news-provenance-v1-migrated'",
            "canonical_story_id": "TEXT NOT NULL DEFAULT ''",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE news_articles ADD COLUMN {name} {declaration}")
        connection.execute("""
            UPDATE news_articles SET
              availability_timestamp=CASE WHEN first_seen_at IS NOT NULL THEN first_seen_at END,
              availability_basis=CASE WHEN first_seen_at IS NOT NULL THEN 'local_observation'
                                      ELSE 'unknown_historical_availability' END,
              availability_confidence=CASE WHEN first_seen_at IS NOT NULL THEN 'exact' ELSE 'unknown' END,
              source_supports_backfill=CASE WHEN first_seen_at IS NULL THEN 1 ELSE 0 END,
              source_timestamp_basis=CASE WHEN first_seen_at IS NULL THEN 'third_party_index_timestamp'
                                          ELSE 'source_reported_publication' END,
              canonical_story_id=id
        """)
        connection.execute("UPDATE archive_identity SET schema_version=2")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _time_close(left, right, seconds=21600):
        if not left or not right:
            return False
        return abs((datetime.fromisoformat(left) - datetime.fromisoformat(right)).total_seconds()) <= seconds

    def _duplicate(self, connection, article):
        exact = connection.execute("SELECT id FROM news_articles WHERE id=? OR canonical_url=? LIMIT 1",
                                   (article.id, canonical_url(article.url))).fetchone()
        if exact:
            return exact[0], "identity_or_url"
        if article.source_article_id:
            exact = connection.execute("SELECT id FROM news_articles WHERE source=? AND source_article_id=?",
                                       (article.source, article.source_article_id)).fetchone()
            if exact:
                return exact[0], "source_article_id"
        candidates = connection.execute(
            "SELECT id,published_at,content_hash,title_key FROM news_articles WHERE content_hash=? OR title_key=?",
            (article.content_hash, article.title_key)).fetchall()
        for candidate_id, published, content_hash, title_key in candidates:
            if self._time_close(published, article.published_at):
                return candidate_id, "content_time" if content_hash == article.content_hash else "title_time"
        return None, None

    def ingest(self, articles):
        result = {"fetched": 0, "inserted": 0, "duplicates": 0}
        columns = ("id", "source", "source_type", "source_article_id", "title", "summary", "url",
                   "canonical_url", "published_at", "first_seen_at", "archive_observed_at", "collected_at", "updated_at",
                   "raw_published_at", "raw_updated_at", "language", "symbols_json",
                   "symbol_confidence_json", "mapping_method", "mapping_version", "market_wide", "event_type", "sentiment", "importance",
                   "confidence", "scorer_method", "scorer_version", "timestamp_confidence",
                   "first_seen_confidence", "availability_timestamp", "availability_basis",
                   "availability_confidence", "source_supports_backfill", "source_timestamp_basis",
                   "historical_availability_guarantee", "provenance_version",
                   "raw_payload_hash", "content_hash", "title_key", "canonical_story_id",
                   "ml_eligible", "ml_ineligible_reason", "raw_payload_json")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for article in articles:
                result["fetched"] += 1
                connection.execute("""
                    INSERT OR IGNORE INTO news_sources VALUES (?,?,?,?,?,?,?)
                """, (article.source, article.source_type, int(article.source_supports_backfill),
                      article.source_timestamp_basis, int(article.historical_availability_guarantee),
                      article.timestamp_confidence, article.provenance_version))
                canonical_id, reason = self._duplicate(connection, article)
                if canonical_id:
                    result["duplicates"] += 1
                    connection.execute("""
                        INSERT INTO news_duplicates(candidate_id,canonical_id,reason,raw_payload_hash,observed_at)
                        VALUES (?,?,?,?,?)
                    """, (article.id, canonical_id, reason, article.raw_payload_hash, article.collected_at))
                    story = connection.execute("SELECT canonical_story_id FROM news_articles WHERE id=?",
                                               (canonical_id,)).fetchone()[0]
                    connection.execute("""
                        INSERT INTO news_source_evidence
                        (canonical_story_id,canonical_article_id,candidate_id,source,source_article_id,
                         url,observed_at,dedup_reason,raw_payload_hash)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (story, canonical_id, article.id, article.source, article.source_article_id,
                          article.url, article.collected_at, reason, article.raw_payload_hash))
                    if article.first_seen_at:
                        connection.execute("""
                            UPDATE news_articles SET first_seen_at=?
                            WHERE id=? AND first_seen_at IS NOT NULL AND first_seen_at>?
                        """, (article.first_seen_at, canonical_id, article.first_seen_at))
                    continue
                record = article.to_record()
                values = {**record, "canonical_url": canonical_url(article.url),
                          "symbols_json": json.dumps(record["symbols"]),
                          "symbol_confidence_json": json.dumps(record["symbol_confidence"], sort_keys=True),
                          "market_wide": int(record["market_wide"]),
                          "source_supports_backfill": int(record["source_supports_backfill"]),
                          "historical_availability_guarantee": int(record["historical_availability_guarantee"]),
                          "ml_eligible": int(record["ml_eligible"]),
                          "raw_payload_json": json.dumps(record["raw_payload"], ensure_ascii=False, sort_keys=True)}
                connection.execute(
                    f"INSERT INTO news_articles ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    tuple(values[column] for column in columns))
                result["inserted"] += 1
        return result

    def articles(self):
        with self.connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute("SELECT * FROM news_articles ORDER BY COALESCE(published_at,collected_at),id")]
        for row in rows:
            row["symbols"] = json.loads(row.pop("symbols_json"))
            row["symbol_confidence"] = json.loads(row.pop("symbol_confidence_json"))
            row["raw_payload"] = json.loads(row.pop("raw_payload_json"))
            row["market_wide"] = bool(row["market_wide"])
            row["source_supports_backfill"] = bool(row["source_supports_backfill"])
            row["historical_availability_guarantee"] = bool(row["historical_availability_guarantee"])
            row["ml_eligible"] = bool(row["ml_eligible"])
        return rows

    def checkpoint_complete(self, source, query_hash, start, end):
        with self.connect() as connection:
            row = connection.execute("""
                SELECT status FROM news_backfill_checkpoints
                WHERE source=? AND query_hash=? AND window_start=? AND window_end=?
            """, (source, query_hash, start, end)).fetchone()
            return bool(row and row[0] == "COMPLETE")

    def save_checkpoint(self, source, query_hash, start, end, stats, status="COMPLETE"):
        with self.connect() as connection:
            connection.execute("""
                INSERT OR REPLACE INTO news_backfill_checkpoints
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (source, query_hash, start, end, status, stats["fetched"], stats["inserted"],
                  stats["duplicates"], datetime.now(timezone.utc).isoformat()))

    def checkpoint_counts(self):
        with self.connect() as connection:
            return dict(connection.execute(
                "SELECT lower(status),COUNT(*) FROM news_backfill_checkpoints GROUP BY lower(status)"))
