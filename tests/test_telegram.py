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


class Response:
    ok = True
    def json(self): return {"ok": True}


class Session:
    def post(self, *args, **kwargs): return Response()


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
