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
                    market_wide INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    sentiment REAL NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    scorer_method TEXT NOT NULL,
                    scorer_version TEXT NOT NULL,
                    timestamp_confidence TEXT NOT NULL,
                    first_seen_confidence TEXT NOT NULL,
                    raw_payload_hash TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    title_key TEXT NOT NULL,
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
            if versions and versions != [(1,)]:
                raise ValueError("Unsupported news archive schema")
            if not versions:
                connection.execute("INSERT INTO archive_identity VALUES (1)")

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
                   "symbol_confidence_json", "market_wide", "event_type", "sentiment", "importance",
                   "confidence", "scorer_method", "scorer_version", "timestamp_confidence",
                   "first_seen_confidence", "raw_payload_hash", "content_hash", "title_key",
                   "ml_eligible", "ml_ineligible_reason", "raw_payload_json")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for article in articles:
                result["fetched"] += 1
                canonical_id, reason = self._duplicate(connection, article)
                if canonical_id:
                    result["duplicates"] += 1
                    connection.execute("""
                        INSERT INTO news_duplicates(candidate_id,canonical_id,reason,raw_payload_hash,observed_at)
                        VALUES (?,?,?,?,?)
                    """, (article.id, canonical_id, reason, article.raw_payload_hash, article.collected_at))
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
