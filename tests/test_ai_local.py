from datetime import timedelta
from pathlib import Path
import requests
import pytest

from app.ai.http import PublicSession, RateLimited
from app.ai.news import NewsCollector
from app.ai.run import run_cycles
from app.ai.storage import read_dashboard
from tests.test_ai import runner


def test_rate_limit_does_not_repeat_requests(monkeypatch):
    calls = []
    def get(self, url, **kwargs):
        calls.append(url)
        result = requests.Response()
        result.status_code = 429
        result.headers["Retry-After"] = "120"
        result._content = b"limited"
        result._content_consumed = True
        return result
    monkeypatch.setattr(requests.Session, "get", get)
    session = PublicSession()
    for _ in range(3):
        with pytest.raises(RateLimited):
            session.get("https://example.test")
    assert len(calls) == 1


def test_retry_policy_is_bounded_and_get_only():
    policy = PublicSession().get_adapter("https://").max_retries
    assert policy.total == 2
    assert policy.allowed_methods == frozenset({"GET"})
    assert 503 in policy.status_forcelist and 429 not in policy.status_forcelist


def test_feed_failures_are_isolated(monkeypatch):
    collector = NewsCollector(["https://bad.test", "https://good.test"])
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size):
            yield b'<rss><channel><item><title>Bitcoin upgrade</title><link>https://good.test/news</link><pubDate>Mon, 28 Sep 2020 10:00:00 GMT</pubDate></item></channel></rss>'
    def fail(*args, **kwargs):
        raise RateLimited("cooldown")
    monkeypatch.setattr(collector.sessions["https://bad.test"], "get", fail)
    monkeypatch.setattr(collector.sessions["https://good.test"], "get", lambda *a, **k: Response())
    articles, health = collector.collect()
    assert len(articles) == 1
    assert not health["https://bad.test"]["ok"] and health["https://good.test"]["ok"]


def test_continuous_runner_persists_and_skips_same_slot(tmp_path):
    obj, _ = runner(tmp_path)
    sleeps = []
    run_cycles(obj, interval=60, cycles=2, sleep=sleeps.append)
    data = read_dashboard(obj.storage.path)
    assert data["runtime"]["status"] == "STOPPED"
    assert data["runtime"]["cycles"] == 2 and not data["runtime"]["failures"]
    assert len(data["trades"]) == 2
    assert sleeps == [60]
    assert {a["portfolio"] for a in data["accounts"].values()} == {"with_news", "without_news"}


def test_runner_recovers_after_failed_cycle(tmp_path, monkeypatch):
    obj, _ = runner(tmp_path)
    original = obj.run_once
    calls = []
    def once():
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("network unavailable")
        return original()
    monkeypatch.setattr(obj, "run_once", once)
    run_cycles(obj, cycles=2, sleep=lambda _: None)
    data = read_dashboard(obj.storage.path)
    assert data["runtime"]["failures"] == 1
    assert len(data["trades"]) == 2


def test_runner_stops_cleanly_on_interrupt(tmp_path):
    obj, _ = runner(tmp_path)
    def stop(_):
        raise KeyboardInterrupt
    run_cycles(obj, cycles=None, sleep=stop)
    assert read_dashboard(obj.storage.path)["runtime"]["reason"] == "keyboard interrupt"


def test_duplicate_slot_makes_no_network_calls(tmp_path, monkeypatch):
    obj, _ = runner(tmp_path)
    obj.run_once()
    def fail():
        pytest.fail("Duplicate slot must not collect or scan again")
    monkeypatch.setattr(obj.collector, "collect", fail)
    monkeypatch.setattr(obj.market, "universe", fail)
    assert obj.run_once()["already_processed"]


def test_stale_scan_still_allows_quote_based_stop(tmp_path, monkeypatch):
    obj, market = runner(tmp_path)
    obj.run_once()
    market.now += timedelta(minutes=15)
    original = market.candles
    def slow(symbol, tf, cutoff):
        market.now += timedelta(seconds=70)
        return original(symbol, tf, cutoff)
    monkeypatch.setattr(market, "candles", slow)
    market.bid, market.ask = 130, 130.1
    scan = obj.run_once()
    assert scan["stale_scan"]
    assert all(a["position"] is None for a in obj.storage.accounts().values())
    assert len(read_dashboard(obj.storage.path)["trades"]) == 4


def test_stale_scan_does_not_open_positions(tmp_path, monkeypatch):
    obj, market = runner(tmp_path)
    original = market.candles
    def slow(symbol, tf, cutoff):
        market.now += timedelta(seconds=70)
        return original(symbol, tf, cutoff)
    monkeypatch.setattr(market, "candles", slow)
    assert obj.run_once()["stale_scan"]
    assert all(a["position"] is None for a in obj.storage.accounts().values())


def test_standalone_dashboard_pages(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    obj, _ = runner(tmp_path)
    run_cycles(obj)
    monkeypatch.setenv("AI_SCANNER_DB", str(obj.storage.path))
    original = obj.storage.path.read_bytes()
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app/ai/dashboard.py")).run(timeout=30)
    assert not page.exception
    assert any(s.value == "with_news" for s in page.subheader)
    page.sidebar.radio[0].set_value("News").run(timeout=30)
    assert not page.exception
    assert original == obj.storage.path.read_bytes()


def test_continuous_runner_advances_to_next_slot(tmp_path):
    obj, market = runner(tmp_path)
    def advance(_):
        market.now += timedelta(minutes=15)
    run_cycles(obj, cycles=2, sleep=advance)
    with obj.storage._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_scans").fetchone()[0] == 2
    assert len(read_dashboard(obj.storage.path)["trades"]) == 2


def test_ai_telegram_delivery_is_persisted_and_not_repeated(tmp_path):
    from app.telegram.notifier import TelegramNotifier
    from app.telegram.settings import TelegramSettings
    obj, _ = runner(tmp_path)
    obj.run_once()
    class Client:
        last_message_id = "local-test"
        messages = []
        def send_message(self, text):
            self.messages.append(text)
            return True, None
    client = Client()
    notifier = TelegramNotifier(obj.storage, TelegramSettings(token="test", chat_id="test", retry_count=1), client)
    notifier.flush_queue()
    notifier.flush_queue()
    assert len(client.messages) == 2
    assert all("PAPER AI BUY" in text for text in client.messages)
    assert not obj.storage.pending_notifications()
    assert read_dashboard(obj.storage.path)["queue"] == [{"status": "DELIVERED", "count": 2}]


def test_public_transport_retries_transient_failures():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(1)
            self.send_response(503 if len(calls) < 3 else 200)
            self.end_headers()
            self.wfile.write(b"response")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with PublicSession() as session:
            # Exercise the production HTTPS adapter using a local HTTP fixture.
            session.mount("http://", session.get_adapter("https://"))
            response = session.get(f"http://127.0.0.1:{server.server_port}", timeout=2)
            assert response.status_code == 200 and len(calls) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
