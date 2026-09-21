import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.paper.models import ExperimentStatus, PaperExperiment


class PaperStorage:
    def __init__(self, path=None):
        self.path = Path(path or Path(__file__).resolve().parents[2] / "data" / "paper" / "paper.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY, config_json TEXT NOT NULL, status TEXT NOT NULL,
                    state_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, timestamp TEXT NOT NULL,
                    side TEXT NOT NULL, price REAL NOT NULL, quantity REAL NOT NULL, commission REAL NOT NULL,
                    realized_pnl REAL NOT NULL, candle_open_time TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)

    def _connect(self):
        return sqlite3.connect(self.path)

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_experiment(self, experiment):
        state = {"cash": experiment.starting_balance, "realized_pnl": 0.0, "fees": 0.0,
                 "position": None, "last_processed_candle": None, "benchmark_entry": None,
                 "benchmark_equity": experiment.starting_balance, "equity_peak": experiment.starting_balance,
                 "equity_curve": [], "last_price": None}
        with self._connection() as connection:
            connection.execute("INSERT INTO experiments VALUES (?, ?, ?, ?, ?)",
                               (experiment.experiment_id, json.dumps(experiment.as_dict()), experiment.status.value,
                                json.dumps(state), experiment.created_at))
        return experiment

    def get_experiment(self, experiment_id):
        with self._connection() as connection:
            row = connection.execute("SELECT config_json, status, state_json FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
        if row is None:
            return None
        config = json.loads(row[0])
        config["status"] = ExperimentStatus(row[1])
        return PaperExperiment(**config), json.loads(row[2])

    def active_experiments(self):
        with self._connection() as connection:
            ids = [row[0] for row in connection.execute("SELECT experiment_id FROM experiments WHERE status=?", (ExperimentStatus.RUNNING.value,))]
        return [self.get_experiment(experiment_id) for experiment_id in ids]

    def all_experiments(self):
        with self._connection() as connection:
            ids = [row[0] for row in connection.execute("SELECT experiment_id FROM experiments ORDER BY created_at")]
        return [self.get_experiment(experiment_id) for experiment_id in ids]

    def notification_state(self):
        with self._connection() as connection:
            rows = connection.execute("SELECT key, value FROM notification_state").fetchall()
        return dict(rows)

    def notification_result(self, success, error=None):
        from datetime import datetime, timezone
        with self._connection() as connection:
            if success:
                connection.execute("INSERT OR REPLACE INTO notification_state VALUES (?, ?)", ("last_success", datetime.now(timezone.utc).isoformat()))
                connection.execute("DELETE FROM notification_state WHERE key='last_error'")
            else:
                connection.execute("INSERT OR REPLACE INTO notification_state VALUES (?, ?)", ("last_error", error or "Notification failed"))

    def claim_alert(self, key, cooldown_seconds):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        state = self.notification_state()
        previous = state.get(f"alert:{key}")
        if previous and now - datetime.fromisoformat(previous) < timedelta(seconds=cooldown_seconds):
            return False
        with self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO notification_state VALUES (?, ?)", (f"alert:{key}", now.isoformat()))
        return True

    def save_state(self, experiment_id, state):
        with self._connection() as connection:
            connection.execute("UPDATE experiments SET state_json=? WHERE experiment_id=?", (json.dumps(state), experiment_id))

    def set_status(self, experiment_id, status):
        with self._connection() as connection:
            connection.execute("UPDATE experiments SET status=? WHERE experiment_id=?", (ExperimentStatus(status).value, experiment_id))

    def add_trade(self, experiment_id, order, candle_open_time):
        with self._connection() as connection:
            connection.execute("INSERT OR IGNORE INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (order.id, experiment_id, order.created_at.isoformat(), order.side.value, order.price,
                                order.quantity, order.commission, order.realized_pnl, candle_open_time))

    def trades(self, experiment_id):
        with self._connection() as connection:
            rows = connection.execute("SELECT timestamp, side, price, quantity, commission, realized_pnl, candle_open_time FROM trades WHERE experiment_id=? ORDER BY timestamp", (experiment_id,)).fetchall()
        return [dict(zip(("timestamp", "side", "price", "quantity", "commission", "realized_pnl", "candle_open_time"), row)) for row in rows]
