from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.core.portfolio import Portfolio
from app.models.candle import Candle
from app.models.signal import Signal
from app.paper.broker import PaperBroker
from app.paper.market_data import PublicMarketData
from app.paper.models import PaperExperiment
from app.paper.report import build_report
from app.paper.runner import PaperRunner, STRATEGIES
from app.paper.storage import PaperStorage
from app.strategies.base import BaseStrategy


class AlwaysBuy(BaseStrategy):
    def analyze(self, prices):
        return Signal.BUY


class FakeMarketData:
    def __init__(self, candles):
        self.candles = candles

    def closed_candles(self, symbol, interval, limit=500):
        return self.candles


def candle(index, price):
    return Candle(datetime(2024, 1, 1) + timedelta(hours=index), price, price + 1, price - 1, price, 1)


def test_paper_broker_applies_slippage_commissions_and_equity():
    portfolio = Portfolio(1000)
    broker = PaperBroker(taker_commission=0.001, slippage=0.01)
    buy = broker.market_buy(portfolio, 100, "BTCUSDT")
    assert buy.price == pytest.approx(101)
    assert portfolio.position.quantity == pytest.approx(200 / 101)
    assert portfolio.equity(110) > portfolio.balance
    sell = broker.market_sell(portfolio, 110)
    assert sell.price == pytest.approx(108.9)
    assert portfolio.realized_pnl != 0


def test_storage_restores_state_and_configuration_is_immutable():
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        experiment = storage.create_experiment(PaperExperiment("BTCUSDT", "1h", "EMAStrategy", {"fast": 10, "slow": 50}))
        loaded, state = storage.get_experiment(experiment.experiment_id)
        assert loaded.strategy_parameters == {"fast": 10, "slow": 50}
        state["cash"] = 777
        storage.save_state(experiment.experiment_id, state)
        assert storage.get_experiment(experiment.experiment_id)[1]["cash"] == 777
        with pytest.raises(Exception):
            loaded.strategy_parameters["fast"] = 20


def test_runner_initializes_then_processes_each_closed_candle_once_and_tracks_benchmark():
    STRATEGIES["AlwaysBuy"] = AlwaysBuy
    try:
        with TemporaryDirectory() as directory:
            storage = PaperStorage(Path(directory) / "paper.sqlite3")
            experiment = storage.create_experiment(PaperExperiment("BTCUSDT", "1h", "AlwaysBuy", {}))
            feed = FakeMarketData([candle(0, 100)])
            runner = PaperRunner(storage, feed)
            assert "initialized" in runner.run_once()[0]
            assert storage.trades(experiment.experiment_id) == []
            feed.candles.append(candle(1, 110))
            assert "action=BUY" in runner.run_once()[0]
            assert len(storage.trades(experiment.experiment_id)) == 1
            assert runner.run_once() == []
            _, state = storage.get_experiment(experiment.experiment_id)
            assert state["benchmark_equity"] == pytest.approx(1100)
            feed.candles.append(candle(2, 120))
            runner.run_once()
            report = build_report(experiment.experiment_id, storage)
            assert report["equity"] > 1000
            assert report["unrealized_pnl"] > 0
    finally:
        STRATEGIES.pop("AlwaysBuy", None)


def test_multiple_experiments_have_independent_balances():
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        first = storage.create_experiment(PaperExperiment("BTCUSDT", "1h", "EMAStrategy", {"fast": 10, "slow": 50}))
        second = storage.create_experiment(PaperExperiment("ETHUSDT", "4h", "EMAStrategy", {"fast": 10, "slow": 50}))
        first_state = storage.get_experiment(first.experiment_id)[1]
        first_state["cash"] = 500
        storage.save_state(first.experiment_id, first_state)
        assert storage.get_experiment(second.experiment_id)[1]["cash"] == 1000


def test_public_market_data_discards_open_candle(monkeypatch):
    now = datetime(2024, 1, 1, 12, tzinfo=__import__("datetime").timezone.utc)
    closed = int((now - timedelta(hours=1)).timestamp() * 1000)
    open_time = int((now - timedelta(minutes=30)).timestamp() * 1000)
    class Response:
        def raise_for_status(self): pass
        def json(self): return [[closed - 3600000, "1", "2", "0", "1", "1", closed, "", "", "", "", ""], [open_time, "2", "3", "1", "2", "1", open_time + 3600000, "", "", "", "", ""]]
    monkeypatch.setattr("app.paper.market_data.requests.get", lambda *args, **kwargs: Response())
    assert len(PublicMarketData().closed_candles("BTCUSDT", "1h", now=now)) == 1
