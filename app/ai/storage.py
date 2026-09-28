import json
from pathlib import Path
import sqlite3

from app.paper.storage import PaperStorage
from app.ai.news import Article, utc

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "ai" / "batch3.sqlite3"
PORTFOLIO_LABELS = {"batch_3_news": "with_news", "batch_3_market": "without_news"}


class AIStorage(PaperStorage):
    """Separate database; never migrate a legacy paper database into AI state."""
    def __init__(self, path=None):
        path = Path(path or DEFAULT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "ai_identity" not in tables:
                raise ValueError("Refusing non-AI database; choose a separate Batch 3 database")
            connection.execute("CREATE TABLE IF NOT EXISTS ai_identity(version INTEGER NOT NULL)")
            versions = connection.execute("SELECT version FROM ai_identity").fetchall()
            if versions and versions != [(1,)]:
                raise ValueError("Unsupported AI database version")
            if not versions:
                connection.execute("INSERT INTO ai_identity VALUES (1)")
        super().__init__(path)
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS ai_news (
                    id TEXT PRIMARY KEY, title_key TEXT UNIQUE NOT NULL,
                    first_seen REAL NOT NULL, published REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_scans (
                    slot INTEGER PRIMARY KEY, cutoff TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_accounts (
                    name TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_trades (
                    id TEXT PRIMARY KEY, slot INTEGER NOT NULL, account TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_alerts (key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS ai_runtime (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def heartbeat(self, status, **details):
        from datetime import datetime, timezone
        payload = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat(), **details}
        with self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO ai_runtime VALUES ('runner', ?)", (json.dumps(payload),))

    def ingest(self, articles):
        from dataclasses import asdict
        with self._connection() as connection:
            for article in articles:
                connection.execute("INSERT OR IGNORE INTO ai_news VALUES (?, ?, ?, ?, ?)",
                                   (article.id, article.title_key, utc(article.first_seen_at).timestamp(),
                                    utc(article.published_at).timestamp(), json.dumps(asdict(article))))

    def articles(self, cutoff):
        timestamp = utc(cutoff).timestamp()
        with self._connection() as connection:
            rows = connection.execute("SELECT payload FROM ai_news WHERE first_seen<=? AND published<=? AND published>=? ORDER BY published DESC LIMIT 1000", (timestamp, timestamp, timestamp - 86400)).fetchall()
        return [Article(**json.loads(row[0])) for row in rows]

    def accounts(self):
        with self._connection() as connection:
            return {name: json.loads(payload) for name, payload in connection.execute("SELECT name,payload FROM ai_accounts")}

    def has_slot(self, slot):
        with self._connection() as connection:
            return connection.execute("SELECT 1 FROM ai_scans WHERE slot=?", (slot,)).fetchone() is not None


def read_dashboard(path=None):
    """Read-only connection: displaying an absent DB never creates one."""
    path = Path(path or DEFAULT_PATH)
    if not path.exists():
        return {"scan": None, "news": [], "accounts": {}, "trades": [], "queue": [], "runtime": None}
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        scan = connection.execute("SELECT payload FROM ai_scans ORDER BY slot DESC LIMIT 1").fetchone()
        runtime = None
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='ai_runtime'").fetchone():
            runtime = connection.execute("SELECT payload FROM ai_runtime WHERE key='runner'").fetchone()
        return {"scan": json.loads(scan[0]) if scan else None,
                "runtime": json.loads(runtime[0]) if runtime else None,
                "news": [json.loads(row[0]) for row in connection.execute("SELECT payload FROM ai_news ORDER BY first_seen DESC LIMIT 100")],
                "accounts": {name: json.loads(payload) for name, payload in connection.execute("SELECT name,payload FROM ai_accounts")},
                "trades": [json.loads(row[0]) for row in connection.execute("SELECT payload FROM ai_trades ORDER BY slot DESC LIMIT 100")],
                "queue": [dict(zip(("status", "count"), row)) for row in connection.execute("SELECT status, count(*) FROM notification_queue GROUP BY status")]}
    finally:
        connection.close()
