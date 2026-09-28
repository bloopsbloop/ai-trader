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
                CREATE TABLE IF NOT EXISTS ml_shadow_predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL,
                    candle_close_timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    base_timeframe TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    feature_version TEXT NOT NULL,
                    probability_positive REAL NOT NULL,
                    decision_threshold REAL NOT NULL,
                    shadow_decision TEXT NOT NULL,
                    market_price REAL NOT NULL,
                    scoring_mode TEXT NOT NULL,
                    feature_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    heuristic_score REAL,
                    heuristic_decision TEXT,
                    target_timestamp TEXT NOT NULL,
                    resolved_at TEXT,
                    future_close REAL,
                    gross_future_return REAL,
                    estimated_cost REAL,
                    actual_net_return REAL,
                    actual_label INTEGER,
                    prediction_correct INTEGER,
                    UNIQUE(experiment_id, model_version, symbol, base_timeframe, candle_close_timestamp)
                );
                CREATE INDEX IF NOT EXISTS ml_shadow_unresolved
                    ON ml_shadow_predictions(symbol, target_timestamp)
                    WHERE resolved_at IS NULL;
                CREATE TABLE IF NOT EXISTS ml_shadow_experiments (
                    experiment_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ml_shadow_cycles (
                    experiment_id TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    cutoff TEXT NOT NULL,
                    status TEXT NOT NULL,
                    eligible_symbols INTEGER NOT NULL,
                    inserted_predictions INTEGER NOT NULL,
                    error_category TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, slot)
                );
            """)
            # Additive migration for databases created by Milestone 11.3.
            prediction_columns = {row[1] for row in connection.execute("PRAGMA table_info(ml_shadow_predictions)")}
            if "experiment_id" not in prediction_columns:
                connection.execute("ALTER TABLE ml_shadow_predictions ADD COLUMN experiment_id TEXT NOT NULL DEFAULT 'ml_v1_market_forward_001'")
            if "future_close" not in prediction_columns:
                connection.execute("ALTER TABLE ml_shadow_predictions ADD COLUMN future_close REAL")

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

    def store_shadow_prediction(self, row):
        columns = ("experiment_id", "candle_close_timestamp", "symbol", "base_timeframe", "model_version",
                   "feature_version", "probability_positive", "decision_threshold",
                   "shadow_decision", "market_price", "scoring_mode", "feature_hash",
                   "created_at", "heuristic_score", "heuristic_decision", "target_timestamp")
        with self._connection() as connection:
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO ml_shadow_predictions ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(row.get(column) for column in columns))
            return bool(cursor.rowcount)

    def unresolved_shadow_predictions(self, symbols=None, experiment_id=None):
        query = "SELECT * FROM ml_shadow_predictions WHERE resolved_at IS NULL"
        parameters = []
        if experiment_id:
            query += " AND experiment_id=?"
            parameters.append(experiment_id)
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            query += f" AND symbol IN ({placeholders})"
            parameters.extend(sorted(symbols))
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, parameters)]

    def resolve_shadow_prediction(self, prediction_id, outcome):
        with self._connection() as connection:
            cursor = connection.execute("""
                UPDATE ml_shadow_predictions SET resolved_at=?, future_close=?, gross_future_return=?,
                    estimated_cost=?, actual_net_return=?, actual_label=?, prediction_correct=?
                WHERE id=? AND resolved_at IS NULL
            """, (outcome["resolved_at"], outcome["future_close"], outcome["gross_future_return"],
                  outcome["estimated_cost"], outcome["actual_net_return"],
                  outcome["actual_label"], outcome["prediction_correct"], prediction_id))
            return bool(cursor.rowcount)

    def ensure_shadow_experiment(self, experiment_id, payload):
        with self._connection() as connection:
            saved = connection.execute("SELECT payload FROM ml_shadow_experiments WHERE experiment_id=?",
                                       (experiment_id,)).fetchone()
            if saved:
                existing = json.loads(saved[0])
                candidate = {**payload, "start_timestamp": existing["start_timestamp"]}
                if candidate != existing:
                    raise ValueError(f"Experiment metadata mismatch: {experiment_id}")
                return existing
            earliest = connection.execute(
                "SELECT MIN(created_at) FROM ml_shadow_predictions WHERE experiment_id=?",
                (experiment_id,)).fetchone()[0]
            if earliest and earliest < payload["start_timestamp"]:
                payload = {**payload, "start_timestamp": earliest}
            serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
            connection.execute("INSERT OR IGNORE INTO ml_shadow_experiments VALUES (?, ?)",
                               (experiment_id, serialized))
            return payload

    def record_shadow_cycle(self, row):
        columns = ("experiment_id", "slot", "cutoff", "status", "eligible_symbols",
                   "inserted_predictions", "error_category", "error", "created_at")
        with self._connection() as connection:
            connection.execute(
                f"INSERT OR IGNORE INTO ml_shadow_cycles ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(row.get(column) for column in columns))

    def record_shadow_failure(self, category, message, created_at):
        key = f"ml_shadow_failure:{category}"
        with self._connection() as connection:
            saved = connection.execute("SELECT payload FROM ai_runtime WHERE key=?", (key,)).fetchone()
            count = (json.loads(saved[0]).get("count", 0) if saved else 0) + 1
            payload = {"count": count, "error": message, "updated_at": created_at}
            connection.execute("INSERT OR REPLACE INTO ai_runtime VALUES (?, ?)", (key, json.dumps(payload)))
            should_alert = category in {"model_load", "disabled"} or count == 3
            alert_key = f"ml_shadow:{category}"
            if should_alert and connection.execute("INSERT OR IGNORE INTO ai_alerts VALUES (?)", (alert_key,)).rowcount:
                connection.execute("INSERT INTO notification_queue (text, reason, status, created_at) VALUES (?, ?, 'PENDING', ?)",
                                   (f"ML shadow system alert: {category}: {message}", alert_key, created_at))
            return count

    def clear_shadow_failure(self, category):
        with self._connection() as connection:
            connection.execute("DELETE FROM ai_runtime WHERE key=?", (f"ml_shadow_failure:{category}",))


def read_dashboard(path=None):
    """Read-only connection: displaying an absent DB never creates one."""
    path = Path(path or DEFAULT_PATH)
    if not path.exists():
        return {"scan": None, "news": [], "accounts": {}, "trades": [], "queue": [], "runtime": None,
                "ml_shadow": []}
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        scan = connection.execute("SELECT payload FROM ai_scans ORDER BY slot DESC LIMIT 1").fetchone()
        runtime = None
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='ai_runtime'").fetchone():
            runtime = connection.execute("SELECT payload FROM ai_runtime WHERE key='runner'").fetchone()
        shadow_table = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ml_shadow_predictions'").fetchone()
        shadow = [] if not shadow_table else [dict(zip(
            ("timestamp", "symbol", "probability", "threshold", "shadow_decision", "heuristic_decision",
             "resolved_at", "actual_net_return", "model_version"), row)) for row in connection.execute("""
                SELECT candle_close_timestamp,symbol,probability_positive,decision_threshold,
                       shadow_decision,heuristic_decision,resolved_at,actual_net_return,model_version
                FROM ml_shadow_predictions ORDER BY candle_close_timestamp DESC,symbol LIMIT 100
            """)]
        return {"scan": json.loads(scan[0]) if scan else None,
                "runtime": json.loads(runtime[0]) if runtime else None,
                "news": [json.loads(row[0]) for row in connection.execute("SELECT payload FROM ai_news ORDER BY first_seen DESC LIMIT 100")],
                "accounts": {name: json.loads(payload) for name, payload in connection.execute("SELECT name,payload FROM ai_accounts")},
                "trades": [json.loads(row[0]) for row in connection.execute("SELECT payload FROM ai_trades ORDER BY slot DESC LIMIT 100")],
                "queue": [dict(zip(("status", "count"), row)) for row in connection.execute("SELECT status, count(*) FROM notification_queue GROUP BY status")],
                "ml_shadow": shadow}
    finally:
        connection.close()
