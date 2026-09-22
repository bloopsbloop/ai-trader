from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from app.indicators.adx import ADX
from app.indicators.bollinger import BollingerBands
from app.indicators.donchian import DonchianChannel
from app.indicators.supertrend import Supertrend
from app.models.candle import Candle
from app.paper.experiments import batch_2_experiments, create_batch_2_experiments
from app.paper.models import PaperExperiment
from app.paper.runner import PaperRunner
from app.paper.storage import PaperStorage
from app.strategies.base import MarketContext
from app.strategies.donchian_breakout import DonchianBreakoutStrategy
from app.telegram.formatter import trade_message
from app.research.batch2 import data_quality, run_batch2_research


def candles(count=80):
    return [Candle(datetime(2024, 1, 1) + timedelta(minutes=15 * index), 100 + index, 102 + index, 99 + index, 101 + index, 1) for index in range(count)]


def test_batch2_indicators_have_warmup_and_no_future_dependency():
    data = candles()
    assert BollingerBands.calculate(data[:10], 20) is None
    assert DonchianChannel.calculate(data[:-1], 20).high == data[-2].high
    assert ADX.calculate(data, 14) is not None
    assert Supertrend.calculate(data, 10, 3) is not None
    first = BollingerBands.calculate(data[:40], 20)
    changed_future = data[:40] + [Candle(datetime(2030, 1, 1), 1, 9999, 0, 9999, 1)]
    assert BollingerBands.calculate(changed_future[:40], 20) == first


def test_donchian_strategy_uses_previous_channel_only():
    data = candles(22)
    data[-1].close = data[-2].high + 1
    assert DonchianBreakoutStrategy(20, 10).analyze_context(MarketContext(tuple(data))).value == "BUY"


def test_batch2_initialization_is_idempotent_and_preserves_batch1():
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        baseline = storage.create_experiment(PaperExperiment("BTCUSDT", "1h", "EMAStrategy", {"fast": 10, "slow": 50}, experiment_id="control"))
        before = storage.get_experiment("control")
        first = create_batch_2_experiments(storage, Path(directory) / "batch2.json")
        second = create_batch_2_experiments(storage, Path(directory) / "batch2.json")
        assert len(first) == len(second)
        assert len(storage.all_experiments()) == len(first) + 1
        assert storage.get_experiment("control") == before
        assert baseline.batch_id == "batch_1"


def test_runner_shares_market_request_for_same_symbol_timeframe():
    class Feed:
        calls = 0
        def closed_candles(self, symbol, timeframe, limit=500):
            self.calls += 1
            return candles(2)
    with TemporaryDirectory() as directory:
        storage = PaperStorage(Path(directory) / "paper.sqlite3")
        for suffix in ("a", "b"):
            storage.create_experiment(PaperExperiment("BTCUSDT", "15m", "EMAStrategy", {"fast": 1, "slow": 2}, experiment_id=suffix))
        feed = Feed()
        PaperRunner(storage, feed).run_once()
        assert feed.calls == 1


def test_batch2_costs_and_telegram_label():
    experiments = batch_2_experiments()
    doge = next(item for item in experiments if item.symbol == "DOGEUSDT")
    btc = next(item for item in experiments if item.symbol == "BTCUSDT")
    assert doge.slippage > btc.slippage
    order = type("Order", (), {"side": type("Side", (), {"value": "BUY"})(), "price": 100.0, "quantity": 1.0, "commission": 0.1, "realized_pnl": 0.0})()
    assert "Batch: Multi-Asset Strategy Lab" in trade_message(doge, order, {"cash": 900}, 1000)


def test_data_quality_and_research_exports():
    class Provider:
        def get_history(self, symbol, timeframe, start, end):
            return candles(120)
    with TemporaryDirectory() as directory:
        rows = run_batch2_research(Provider(), directory)
        assert len(rows) == 30
        assert data_quality(candles(10), "15m")["duplicates"] == 0
        assert (Path(directory) / "summary.csv").exists()
        assert (Path(directory) / "data_quality.json").exists()
