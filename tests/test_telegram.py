from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.portfolio import Portfolio
from app.models.order import Order, OrderSide
from app.paper.models import PaperExperiment
from app.paper.storage import PaperStorage
from app.telegram.client import TelegramClient
from app.telegram.formatter import trade_message
from app.telegram.notifier import TelegramNotifier
from app.telegram.reports import report_data
from app.telegram.settings import TelegramSettings
from app.telegram.chunking import SAFE_TELEGRAM_TEXT_LENGTH, chunk_message


class Response:
    ok = True
    def json(self): return {"ok": True}


class Session:
    def post(self, *args, **kwargs): return Response()


def test_telegram_json_ok_true_is_success_even_without_http_ok_flag():
    class JsonSuccessResponse:
        def json(self):
            return {"ok": True, "result": {"message_id": 123}}
    class JsonSuccessSession:
        def post(self, *args, **kwargs):
            return JsonSuccessResponse()
    client = TelegramClient(TelegramSettings("token", "chat"), JsonSuccessSession())
    assert client.send_message("hello") == (True, None)
    assert client.last_message_id == 123


def test_telegram_json_rejection_returns_description():
    class RejectedResponse:
        def json(self):
            return {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
    class RejectedSession:
        def post(self, *args, **kwargs):
            return RejectedResponse()
    success, error = TelegramClient(TelegramSettings("token", "chat"), RejectedSession()).send_message("hello")
    assert not success
    assert "Bad Request: chat not found" in error


def test_permanent_rejection_is_persisted_as_failed_not_pending():
    class RejectedClient:
        last_message_id = None
        def send_message(self, text):
            return False, "Telegram API rejected notification: forbidden"
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        notifier = TelegramNotifier(storage, TelegramSettings("token", "chat", retry_count=1), RejectedClient())
        assert not notifier.send("rejected")
        assert storage.pending_notifications() == []
        assert storage.notification_queue()[0]["status"] == "FAILED"


def test_formatter_and_client_do_not_expose_token():
    settings = TelegramSettings("secret-token", "123")
    success, error = TelegramClient(settings, Session()).send_message("hello")
    assert success and error is None
    assert "secret-token" not in (error or "")


def test_missing_configuration_and_cooldown_persist():
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        notifier = TelegramNotifier(storage, TelegramSettings(None, None))
        assert not notifier.send("x")
        assert storage.claim_alert("drawdown", 60)
        assert not storage.claim_alert("drawdown", 60)
        assert "last_error" in PaperStorage(storage.path).notification_state()


def test_buy_notification_and_daily_report_data():
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        experiment = storage.create_experiment(PaperExperiment("BTCUSDT", "1h", "EMAStrategy", {"fast": 10, "slow": 50}))
        order = type("Order", (), {"side": type("Side", (), {"value": "BUY"})(), "price": 100.0, "quantity": 2.0, "commission": 0.1, "realized_pnl": 0.0})()
        text = trade_message(experiment, order, {"cash": 800}, 1000)
        assert "PAPER BUY" in text and "BTCUSDT" in text
        rows, totals = report_data(storage)
        assert len(rows) == 1 and totals["equity"] == 1000
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from app.models.candle import Candle
from app.models.signal import Signal
from app.paper.models import PaperExperiment
from app.paper.runner import PaperRunner, STRATEGIES
from app.paper.storage import PaperStorage
from app.strategies.base import BaseStrategy
from app.telegram.diagnostics import startup_checks
from app.telegram.reports import format_report


class BuyStrategy(BaseStrategy):
    def analyze(self, prices):
        return Signal.BUY


class RecoveringFeed:
    def __init__(self, candles):
        self.candles = candles
        self.failed = False

    def closed_candles(self, symbol, timeframe, limit=500):
        if self.failed:
            raise OSError("offline")
        return self.candles


def candle(index):
    price = 100 + index
    return Candle(datetime(2024, 1, 1) + timedelta(hours=index), price, price, price, price, 1)


def test_restart_recovery_processes_every_missing_closed_candle():
    STRATEGIES["BuyStrategy"] = BuyStrategy
    try:
        with TemporaryDirectory() as directory:
            storage = PaperStorage(Path(directory) / "paper.sqlite3")
            experiment = storage.create_experiment(PaperExperiment("BTCUSDT", "1h", "BuyStrategy", {}))
            feed = RecoveringFeed([candle(0)])
            PaperRunner(storage, feed).run_once()
            feed.failed = True
            try:
                PaperRunner(storage, feed).run_once()
            except OSError:
                pass
            feed.failed = False
            feed.candles.extend([candle(1), candle(2), candle(3)])
            PaperRunner(storage, feed).run_once()
            _, state = storage.get_experiment(experiment.experiment_id)
            assert state["last_processed_candle"] == candle(3).open_time.isoformat()
            assert len(state["equity_curve"]) == 3
    finally:
        STRATEGIES.pop("BuyStrategy", None)


def test_expanded_reports_and_startup_checks_are_readable():
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        storage.create_experiment(PaperExperiment("BTCUSDT", "1h", "EMAStrategy", {"fast": 10, "slow": 50}))
        text = format_report("DAILY", storage)
        assert "Cash:" in text and "Position value:" in text and "Unrealized PnL:" in text
        checks = startup_checks(storage)
        assert checks["SQLite"] == "OK" and checks["Paper storage"] == "OK"


def test_notification_queue_retries_and_preserves_delivery_audit():
    class FlakyClient:
        def __init__(self):
            self.calls = 0
            self.last_message_id = None
        def send_message(self, text):
            self.calls += 1
            if self.calls == 1:
                return False, "offline"
            self.last_message_id = 42
            return True, None
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        notifier = TelegramNotifier(storage, TelegramSettings("token", "chat", retry_count=1), FlakyClient())
        assert not notifier.send("queued")
        assert len(storage.pending_notifications()) == 1
        assert notifier.flush_queue()
        record = storage.notification_queue()[0]
        assert record["status"] == "DELIVERED" and record["message_id"] == "42" and record["retries"] == 1


def test_recovery_history_persists():
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        storage.log_recovery("outage", 3, 0.1)
        assert PaperStorage(storage.path).recovery_history()[0]["candles_recovered"] == 3


def test_short_report_is_one_message():
    assert chunk_message("short report", "Daily Report") == ["short report"]


def test_long_report_is_ordered_and_keeps_every_experiment_block():
    blocks = [f"Experiment {index}\n" + (f"metric-{index} " * 80) for index in range(30)]
    chunks = chunk_message("Summary\n\nBatch 1\n\n" + "\n\n".join(blocks), "Daily Report")
    assert len(chunks) > 1
    assert all(len(chunk) <= SAFE_TELEGRAM_TEXT_LENGTH for chunk in chunks)
    assert [chunk.splitlines()[0] for chunk in chunks] == [f"Daily Report ({index}/{len(chunks)})" for index in range(1, len(chunks) + 1)]
    combined = "\n".join(chunks)
    assert all(f"Experiment {index}\n" in combined for index in range(30))


def test_multipart_queue_preserves_order_when_middle_chunk_temarily_fails():
    class MiddleFailureClient:
        def __init__(self):
            self.calls = []
            self.failed = False
            self.last_message_id = None
        def send_message(self, text):
            self.calls.append(text)
            if text == "part 2" and not self.failed:
                self.failed = True
                return False, "temporary network failure"
            self.last_message_id = len(self.calls)
            return True, None
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        client = MiddleFailureClient()
        notifier = TelegramNotifier(storage, TelegramSettings("token", "chat", retry_count=1), client)
        assert not notifier.send_many(["part 1", "part 2", "part 3"], "report")
        assert [item["text"] for item in storage.pending_notifications()] == ["part 2", "part 3"]
        assert notifier.flush_queue()
        assert client.calls == ["part 1", "part 2", "part 2", "part 3"]
        assert all(row["status"] == "DELIVERED" for row in storage.notification_queue())
