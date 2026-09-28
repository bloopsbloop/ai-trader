"""Forward-only Batch 3; transactions cover decisions, fills, state and outbox."""
from dataclasses import asdict
from datetime import datetime, timezone
import json

from app.ai.market import INTERVALS, PublicScannerData, features, finite
from app.ai.news import BaselineScorer, NewsCollector, utc
from app.ai.scoring import assess_news, rank
from app.ai.storage import AIStorage, PORTFOLIO_LABELS
from app.core.portfolio import Portfolio
from app.models.position import Position
from app.models.order import OrderStatus
from app.paper.broker import PaperBroker


def clock():
    return datetime.now(timezone.utc)


class AIRunner:
    def __init__(self, storage=None, market=None, collector=None, scorer=None, paper=False, now=clock):
        self.storage = storage or AIStorage()
        self.market = market or PublicScannerData()
        self.collector = collector or NewsCollector()
        self.scorer = scorer or BaselineScorer()
        self.paper = paper
        self.now = now

    def run_once(self):
        current_slot = int(utc(self.now()).timestamp()) // 900
        if self.storage.has_slot(current_slot):
            return {"already_processed": True, "slot": current_slot}
        articles, health = self.collector.collect()
        self.storage.ingest(articles)
        errors = []
        try:
            universe = self.market.universe()
        except Exception as exc:
            universe = []
            errors.append({"stage": "universe", "error": type(exc).__name__})
        cutoff = utc(self.now())
        slot = int(cutoff.timestamp()) // 900
        if self.storage.has_slot(slot):
            return {"already_processed": True, "slot": slot}
        eligible = {row["symbol"] for row in universe}
        held = {account["position"]["symbol"] for account in self.storage.accounts().values() if account.get("position")}
        feature_rows = {}
        for symbol in sorted(eligible | held):
            if (utc(self.now()) - cutoff).total_seconds() > 180:
                errors.append({"stage": "features", "error": "Scan time budget exceeded; remaining symbols skipped"})
                break
            try:
                feature_rows[symbol] = {tf: features(self.market.candles(symbol, tf, cutoff), tf, cutoff) for tf in INTERVALS}
            except Exception as exc:
                errors.append({"symbol": symbol, "stage": "features", "error": type(exc).__name__})
        assessments, scoring_errors = assess_news(self.storage.articles(cutoff), eligible | held, cutoff, self.scorer)
        errors.extend(scoring_errors)
        rankings = {"batch_3_news": rank(feature_rows, assessments, eligible),
                    "batch_3_market": rank(feature_rows, assessments, eligible, use_news=False)}
        healthy_news = any(row.get("ok") for row in health.values()) and not scoring_errors
        if not healthy_news:
            for row in rankings["batch_3_news"]:
                if row["decision"] == "BUY":
                    row.update(decision="WATCH", reason="news coverage unavailable")
        decision_at = utc(self.now())
        stale_scan = (decision_at - cutoff).total_seconds() > 180
        scan = {"slot": slot, "mode": "PAPER" if self.paper else "SCAN_ONLY", "batch_id": "batch_3",
                "cutoff": cutoff.isoformat(), "decision_at": decision_at.isoformat(), "universe": universe,
                "rankings": rankings, "news_scores": assessments, "source_health": health, "errors": errors,
                "stale_scan": stale_scan, "portfolio_labels": PORTFOLIO_LABELS}
        with self.storage._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM ai_scans WHERE slot=?", (slot,)).fetchone():
                return {"already_processed": True, "slot": slot}
            # Never fill a new decision using a pre-decision candle close.
            if self.paper:
                quotes = {}
                for name, ranking in rankings.items():
                    self._execute(connection, name, ranking, scan, quotes)
            for item in assessments:
                if item["importance"] >= 0.8 and item["confidence"] >= 0.6 and item["freshness"] >= 0.5:
                    key = "major:" + item["article_id"]
                    if connection.execute("INSERT OR IGNORE INTO ai_alerts VALUES (?)", (key,)).rowcount:
                        self._queue(connection, f"PAPER AI major news: {', '.join(item['symbols'])}; {item['event_type']}; article {item['article_id'][:12]}", key, decision_at.isoformat())
            scan["accounts"] = {name: json.loads(payload) for name, payload in connection.execute("SELECT name,payload FROM ai_accounts")}
            scan["execution_policy"] = {"version": "paper-v1", "starting_balance": 1000,
                                        "position_size": 0.10, "max_position_fraction": 0.20,
                                        "stop_loss": 0.03, "take_profit": 0.06,
                                        "taker_commission": 0.0004, "slippage": 0.0005}
            connection.execute("INSERT INTO ai_scans VALUES (?, ?, ?)", (slot, cutoff.isoformat(), json.dumps(scan, allow_nan=False)))
        return scan

    def _execute(self, connection, name, ranking, scan, quotes):
        saved = connection.execute("SELECT payload FROM ai_accounts WHERE name=?", (name,)).fetchone()
        state = json.loads(saved[0]) if saved else {"cash": 1000.0, "realized_pnl": 0.0, "fees": 0.0, "position": None, "equity": 1000.0}
        portfolio = Portfolio(1000.0)
        portfolio.balance, portfolio.realized_pnl, portfolio.total_commissions = state["cash"], state["realized_pnl"], state["fees"]
        if state["position"]:
            portfolio.position = Position(**state["position"])
        broker = PaperBroker(position_size=0.10, max_position_fraction=0.20)
        row = next((row for row in ranking if portfolio.position and row["symbol"] == portfolio.position.symbol), None)
        if portfolio.position and row is None:
            # Missing history must not disable protective exits on a fresh quote.
            row = {"symbol": portfolio.position.symbol, "score": 0.5, "news_impact": 0}
            scan["errors"].append({"account": name, "stage": "features", "error": "Held symbol: quote-only protective exits"})
        if portfolio.position and scan["stale_scan"]:
            row = {"symbol": portfolio.position.symbol, "score": 0.5, "news_impact": 0}
        if not portfolio.position:
            row = None if scan["stale_scan"] else next((row for row in ranking if row["decision"] == "BUY"), None)
        order, quote = None, None
        if row:
            symbol = row["symbol"]
            try:
                if symbol not in quotes:
                    quotes[symbol] = self.market.quote(symbol)
                quote = quotes[symbol]
                bid, ask = finite(quote["bid"], True), finite(quote["ask"], True)
                observed, now = utc(quote["observed_at"]), utc(self.now())
                if not utc(scan["decision_at"]) <= observed <= now or (now - observed).total_seconds() > 30 or ask < bid or (ask / bid - 1) > 0.003:
                    raise ValueError("Stale, future or wide-spread quote")
                if portfolio.position:
                    move = bid / portfolio.position.entry - 1
                    if move <= -0.03 or move >= 0.06 or row["score"] < 0.45 or (name == "batch_3_news" and row["news_impact"] < -0.25):
                        order = broker.market_sell(portfolio, bid)
                    state["equity"] = portfolio.equity(bid)
                else:
                    order = broker.market_buy(portfolio, ask, symbol)
                    state["equity"] = portfolio.equity(bid)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                scan["errors"].append({"account": name, "stage": "execution", "error": type(exc).__name__})
        elif portfolio.position:
            scan["errors"].append({"account": name, "stage": "valuation", "error": "Held symbol has no valid features; position retained"})
        if order:
            record = {**asdict(order), "side": order.side.value, "status": order.status.value,
                      "created_at": utc(self.now()).isoformat(), "account": name, "slot": scan["slot"],
                      "decision_at": scan["decision_at"], "quote": quote}
            connection.execute("INSERT INTO ai_trades VALUES (?, ?, ?, ?)", (order.id, scan["slot"], name, json.dumps(record, allow_nan=False)))
            if order.status == OrderStatus.FILLED:
                self._queue(connection, f"PAPER AI {order.side.value} {order.symbol} | {PORTFOLIO_LABELS[name]} | qty={order.quantity:.8f} price={order.price:.8f}", "ai_trade:" + order.id, record["created_at"])
        state.update(cash=portfolio.balance, realized_pnl=portfolio.realized_pnl, fees=portfolio.total_commissions,
                     position=asdict(portfolio.position) if portfolio.position else None, last_scan=scan["slot"],
                     portfolio=PORTFOLIO_LABELS[name])
        connection.execute("INSERT OR REPLACE INTO ai_accounts VALUES (?, ?)", (name, json.dumps(state, allow_nan=False)))

    @staticmethod
    def _queue(connection, text, reason, created_at):
        connection.execute("INSERT INTO notification_queue (text, reason, status, created_at) VALUES (?, ?, 'PENDING', ?)", (text, reason, created_at))
