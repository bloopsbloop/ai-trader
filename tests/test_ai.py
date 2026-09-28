from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import sys
import subprocess

import pytest

from app.ai.market import INTERVALS, PublicScannerData, features, select_universe
from app.ai.news import Article, Assessment, BaselineScorer, freshness, parse_feed, resolve_symbols
from app.ai.runner import AIRunner
from app.ai.scoring import assess_news, rank
from app.ai.storage import AIStorage, read_dashboard
from app.models.candle import Candle
from app.paper.storage import PaperStorage

NOW = datetime(2026, 9, 28, 12, 0, 5, tzinfo=timezone.utc)


def article(title="Bitcoin partnership announced", url="https://example.com/story", **kwargs):
    return Article("test", title, url, kwargs.get("published", (NOW - timedelta(minutes=10)).isoformat()), kwargs.get("seen", NOW.isoformat()))


def bars(tf="15m", now=NOW):
    seconds = INTERVALS[tf]
    boundary = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, timezone.utc)
    return [Candle(boundary - timedelta(seconds=seconds * (50 - i)), 100 + i, 101 + i, 99 + i, 100.5 + i, 200 if i == 49 else 100) for i in range(50)]


class Market:
    def __init__(self):
        self.now = NOW
        self.bid, self.ask = 150, 150.1
        self.fail = False
        self.universe_rows = [{"symbol": "BTCUSDT", "quote_volume": 20_000_000}]

    def universe(self):
        return self.universe_rows

    def candles(self, symbol, tf, cutoff):
        if self.fail:
            raise ValueError("outage")
        return bars(tf, cutoff)

    def quote(self, symbol):
        return {"bid": self.bid, "ask": self.ask, "observed_at": self.now.isoformat()}


class Collector:
    def collect(self):
        return [article()], {"test": {"ok": True}}


def runner(tmp_path, **kwargs):
    market = Market()
    kwargs.setdefault("shadow", False)
    obj = AIRunner(AIStorage(tmp_path / "ai.sqlite3"), market, Collector(), paper=True, now=lambda: market.now, **kwargs)
    return obj, market


def test_import_paper_path_does_not_import_exchange():
    code = "import app.ai.run; import sys; assert 'app.core.exchange' not in sys.modules; assert 'binance.client' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_feed_rss_atom_and_bad_dates():
    rss = b'<rss><channel><item><title>Bitcoin upgrade</title><link>https://x.test/a?utm_source=rss</link><pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate></item><item><title>bad</title></item></channel></rss>'
    result = parse_feed(rss, "x", NOW)
    assert len(result) == 1 and result[0].url == "https://x.test/a"
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Solana upgrade</title><link href="https://x.test/b"/><published>2026-09-28T10:00:00Z</published></entry></feed>'
    assert len(parse_feed(atom, "x", NOW)) == 1
    assert not parse_feed(atom, "x", NOW - timedelta(days=1))


@pytest.mark.parametrize("payload", [b'<!DOCTYPE rss><rss/>', b'<!ENTITY a "xx"><rss/>', b'a' * 2_000_001], ids=["doctype", "entity", "oversized"])
def test_unsafe_feed_rejected(payload):
    with pytest.raises(ValueError):
        parse_feed(payload, "x", NOW)


def test_dedup_and_first_seen_is_immutable(tmp_path):
    storage = AIStorage(tmp_path / "ai.db")
    first = article()
    storage.ingest([first, replace(first, url=first.url + "?utm_source=x"), replace(first, url="https://other.test/copy")])
    storage.ingest([replace(first, first_seen_at=(NOW - timedelta(days=1)).isoformat())])
    assert len(storage.articles(NOW)) == 1
    assert not storage.articles(NOW - timedelta(seconds=1))


def test_no_future_news_or_late_arrival():
    future = article(published=(NOW + timedelta(seconds=1)).isoformat())
    late = article(seen=(NOW + timedelta(seconds=1)).isoformat())
    assert freshness(future, NOW) == freshness(late, NOW) == 0
    assert assess_news([future, late], {"BTCUSDT"}, NOW, BaselineScorer())[0] == []


def test_ambiguous_ticker_requires_explicit_mention():
    symbols = {"LINKUSDT", "BTCUSDT", "SOLUSDT"}
    assert resolve_symbols("LINK to the article", symbols) == []
    assert resolve_symbols("$LINK and Bitcoin partnership", symbols) == ["BTCUSDT", "LINKUSDT"]
    assert resolve_symbols("solution", symbols) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1.1, -0.1, True])
def test_structured_scores_validate(value):
    with pytest.raises(ValueError):
        Assessment(0, value, 1, 0.5, "other", "test")


def test_model_freshness_not_trusted():
    class Scorer:
        def score(self, article, now):
            return dict(sentiment=1, importance=1, confidence=1, freshness=1, event_type="listing", scorer_version="test")
    result, errors = assess_news([article()], {"BTCUSDT"}, NOW, Scorer())
    assert not errors and 0 < result[0]["freshness"] < 1


def test_bad_model_fails_closed():
    class Scorer:
        def score(self, article, now):
            return {"sentiment": "BUY"}
    result, errors = assess_news([article()], {"BTCUSDT"}, NOW, Scorer())
    assert not result and errors


def test_rumor_neutralized():
    score = BaselineScorer().score(article("Bitcoin listing rumor"), NOW)
    assert score.sentiment == 0 and score.confidence < 0.5


def test_only_closed_candles_and_contiguity():
    closed = bars()
    future = Candle(NOW, 100, 1_000_000, 1, 900_000, 10_000)
    assert features(closed + [future], "15m", NOW) == features(closed, "15m", NOW)
    with pytest.raises(ValueError):
        features(closed[:-1], "15m", NOW)
    with pytest.raises(ValueError):
        features(closed[:-2] + [closed[-1], closed[-1]], "15m", NOW)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 0])
def test_bad_market_values(bad):
    rows = bars()
    rows[-1] = replace(rows[-1], close=bad)
    with pytest.raises(ValueError):
        features(rows, "15m", NOW)


def test_universe_filters():
    symbols = [dict(symbol=s, quoteAsset="USDT", baseAsset=s[:-4], status="TRADING", isSpotTradingAllowed=True) for s in ("BTCUSDT", "USDCUSDT", "ETHUSDT", "SOLUSDT")]
    symbols[-1]["status"] = "BREAK"
    volumes = [dict(symbol=s, quoteVolume=v) for s, v in (("BTCUSDT", "12000000"), ("USDCUSDT", "90000000"), ("ETHUSDT", "100"), ("SOLUSDT", "15000000"))]
    assert select_universe({"symbols": symbols}, volumes) == [{"symbol": "BTCUSDT", "quote_volume": 12000000}]


def test_news_cannot_buy_without_market_confirmation():
    frames = {tf: features(bars(tf), tf, NOW) for tf in INTERVALS}
    frames["4h"]["trend"] = -0.01
    news = [{"symbols": ["BTCUSDT"], "importance": 1, "confidence": 1, "freshness": 1, "sentiment": 1, "article_id": "x"}]
    assert rank({"BTCUSDT": frames}, news, {"BTCUSDT"})[0]["decision"] != "BUY"


def test_database_isolation(tmp_path):
    path = tmp_path / "paper.db"
    PaperStorage(path)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="non-AI"):
        AIStorage(path)
    assert path.read_bytes() == original


def test_restart_idempotence_and_atomic_outbox(tmp_path):
    obj, market = runner(tmp_path)
    scan = obj.run_once()
    data = read_dashboard(obj.storage.path)
    assert scan["mode"] == "PAPER"
    assert len(data["accounts"]) == len(data["trades"]) == 2
    assert len(obj.storage.pending_notifications()) == 2
    assert all(trade["price"] > trade["quote"]["ask"] for trade in data["trades"])
    assert all(trade["quote"]["observed_at"] >= trade["decision_at"] for trade in data["trades"])
    restarted = AIRunner(AIStorage(obj.storage.path), market, Collector(), paper=True, now=lambda: NOW)
    assert restarted.run_once()["already_processed"]
    assert len(read_dashboard(obj.storage.path)["trades"]) == 2


def test_transaction_rollback(tmp_path, monkeypatch):
    obj, _ = runner(tmp_path)
    def fail(*args):
        raise RuntimeError("disk failure")
    monkeypatch.setattr(obj, "_queue", fail)
    with pytest.raises(RuntimeError):
        obj.run_once()
    data = read_dashboard(obj.storage.path)
    assert not data["accounts"] and not data["trades"] and not data["scan"]


def test_stops_work_when_held_symbol_leaves_universe_and_history_fails(tmp_path):
    obj, market = runner(tmp_path)
    obj.run_once()
    market.now += timedelta(minutes=15)
    market.bid, market.ask = 130, 130.1
    market.universe_rows, market.fail = [], True
    obj.run_once()
    data = read_dashboard(obj.storage.path)
    assert len(data["trades"]) == 4
    assert all(account["position"] is None for account in data["accounts"].values())
    assert all(account["realized_pnl"] < 0 for account in data["accounts"].values())


def test_scanner_only_has_no_accounts(tmp_path):
    obj, _ = runner(tmp_path)
    obj.paper = False
    obj.run_once()
    assert not obj.storage.accounts()


def test_wide_spread_blocks_fills(tmp_path):
    obj, market = runner(tmp_path)
    market.ask = 170
    scan = obj.run_once()
    assert scan["errors"] and not read_dashboard(obj.storage.path)["trades"]


def test_feed_outage_blocks_news_buy_but_not_control(tmp_path):
    obj, _ = runner(tmp_path)
    obj.collector.collect = lambda: ([], {"test": {"ok": False}})
    scan = obj.run_once()
    assert scan["rankings"]["batch_3_news"][0]["decision"] == "WATCH"
    assert obj.storage.accounts()["batch_3_news"]["position"] is None
    assert obj.storage.accounts()["batch_3_market"]["position"]


def test_important_news_alert_dedup(tmp_path):
    obj, market = runner(tmp_path)
    obj.paper = False
    obj.collector.collect = lambda: ([article("Bitcoin exchange hacked")], {"test": {"ok": True}})
    obj.run_once()
    market.now += timedelta(minutes=15)
    obj.run_once()
    assert len(obj.storage.pending_notifications()) == 1


def test_dashboard_no_database_creation(tmp_path):
    path = tmp_path / "missing.db"
    assert read_dashboard(path)["scan"] is None
    assert not path.exists()


def test_telegram_failure_retains_outbox(tmp_path):
    from app.telegram.notifier import TelegramNotifier
    from app.telegram.settings import TelegramSettings
    obj, _ = runner(tmp_path)
    obj.run_once()
    class Client:
        def send_message(self, text):
            return False, "network unavailable"
    settings = TelegramSettings(token="test", chat_id="test", retry_count=1)
    notifier = TelegramNotifier(obj.storage, settings=settings, client=Client())
    notifier.flush_queue()
    assert len(obj.storage.pending_notifications()) == 2
    assert obj.storage.pending_notifications()[0]["retries"] == 1
    assert len(read_dashboard(obj.storage.path)["trades"]) == 2


def test_no_trading_endpoints():
    with pytest.raises(ValueError):
        PublicScannerData()._get("order")


def test_risk_rejection_is_not_a_fill_or_notification(tmp_path):
    obj, _ = runner(tmp_path)
    with obj.storage._connection() as connection:
        for name in ("batch_3_news", "batch_3_market"):
            state = {"cash": 900, "realized_pnl": -100, "fees": 0, "position": None, "equity": 900}
            connection.execute("INSERT INTO ai_accounts VALUES (?,?)", (name, json.dumps(state)))
    obj.run_once()
    data = read_dashboard(obj.storage.path)
    assert all(row["status"] == "REJECTED" for row in data["trades"])
    assert all(account["position"] is None for account in data["accounts"].values())
    assert not obj.storage.pending_notifications()


def test_concurrent_runners_commit_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    obj, market = runner(tmp_path)
    other = AIRunner(AIStorage(obj.storage.path), market, Collector(), paper=True, now=lambda: NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda x: x.run_once(), (obj, other)))
    assert sum(bool(r.get("already_processed")) for r in results) == 1
    assert len(read_dashboard(obj.storage.path)["trades"]) == 2


def test_streamlit_pages_render_and_do_not_write(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    obj, _ = runner(tmp_path)
    obj.run_once()
    before = obj.storage.path.read_bytes()
    monkeypatch.setenv("AI_SCANNER_DB", str(obj.storage.path))
    app = AppTest.from_string("import streamlit as st\nfrom app.dashboard.ai_pages import scanner_page, news_page\nscanner_page(st)\nnews_page(st)")
    app.run(timeout=30)
    assert not app.exception
    assert len(app.dataframe) >= 5
    assert obj.storage.path.read_bytes() == before
