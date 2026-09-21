from datetime import datetime, timedelta, timezone

from app.paper.report import build_report
from app.paper.storage import PaperStorage
from app.telegram.formatter import report_message
from app.telegram.notifier import TelegramNotifier


def report_data(storage=None, since=None):
    storage = storage or PaperStorage()
    since = since or datetime.now(timezone.utc) - timedelta(days=1)
    rows, totals = [], {"trades": 0, "realized_pnl": 0.0, "fees": 0.0, "equity": 0.0}
    for experiment, state in storage.all_experiments():
        report = build_report(experiment.experiment_id, storage)
        trades = [trade for trade in storage.trades(experiment.experiment_id) if datetime.fromisoformat(trade["timestamp"]) >= since]
        closed = [trade for trade in trades if trade["side"] == "SELL"]
        realized = sum(trade["realized_pnl"] for trade in closed)
        fees = sum(trade["commission"] for trade in trades)
        rows.append({"name": f"{experiment.symbol} {experiment.timeframe} {experiment.strategy}", "trades": len(closed), "realized_pnl": realized, "fees": fees, "max_drawdown": report["max_drawdown"], "equity": report["equity"], "benchmark_equity": report["benchmark_equity"], "win_rate": report["win_rate"], "profit_factor": report["profit_factor"], "sharpe": report["sharpe"]})
        for key, value in (("trades", len(closed)), ("realized_pnl", realized), ("fees", fees), ("equity", report["equity"])):
            totals[key] += value
    return rows, totals


def send_daily(storage=None, notifier=None):
    storage = storage or PaperStorage()
    rows, totals = report_data(storage, datetime.now(timezone.utc) - timedelta(days=1))
    return (notifier or TelegramNotifier(storage)).send(report_message("📊 PAPER TRADING DAILY REPORT", datetime.now(timezone.utc).date().isoformat(), rows, totals))


def send_weekly(storage=None, notifier=None):
    storage = storage or PaperStorage()
    rows, totals = report_data(storage, datetime.now(timezone.utc) - timedelta(days=7))
    return (notifier or TelegramNotifier(storage)).send(report_message("📈 PAPER TRADING WEEKLY REPORT", datetime.now(timezone.utc).date().isoformat(), rows, totals))
