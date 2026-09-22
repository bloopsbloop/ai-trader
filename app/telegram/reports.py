from datetime import datetime, timedelta, timezone

from app.paper.report import build_report
from app.paper.storage import PaperStorage
from app.telegram.formatter import money
from app.telegram.notifier import TelegramNotifier
from app.telegram.chunking import chunk_message


def report_data(storage=None, since=None):
    storage = storage or PaperStorage()
    since = since or datetime.now(timezone.utc) - timedelta(days=1)
    rows, totals = [], {"trades": 0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "fees": 0.0, "cash": 0.0, "position_value": 0.0, "equity": 0.0, "open_positions": 0}
    for experiment, state in storage.all_experiments():
        report = build_report(experiment.experiment_id, storage)
        recent = [trade for trade in storage.trades(experiment.experiment_id) if datetime.fromisoformat(trade["timestamp"]) >= since]
        closed = [trade for trade in recent if trade["side"] == "SELL"]
        position_value = report["equity"] - state["cash"]
        row = {"name": f"{experiment.symbol} {experiment.timeframe} {experiment.strategy}", "batch_id": experiment.batch_id,
               "batch_name": experiment.batch_name, "trades": len(closed), "realized_pnl": sum(trade["realized_pnl"] for trade in closed), "unrealized_pnl": report["unrealized_pnl"], "fees": sum(trade["commission"] for trade in recent), "cash": state["cash"], "position_value": position_value, "equity": report["equity"], "open_position": bool(state["position"]), "max_drawdown": report["max_drawdown"]}
        rows.append(row)
        for key in ("trades", "realized_pnl", "unrealized_pnl", "fees", "cash", "position_value", "equity"):
            totals[key] += row[key]
        totals["open_positions"] += int(row["open_position"])
    return sorted(rows, key=lambda row: row["name"]), totals


def format_report(title, storage=None, since=None):
    rows, totals = report_data(storage, since)
    lines = [title, "", f"Date: {datetime.now(timezone.utc):%Y-%m-%d}", "Experiments: listed alphabetically", f"Open positions: {totals['open_positions']}", f"Closed trades: {totals['trades']}", f"Realized PnL: {money(totals['realized_pnl'])}", f"Unrealized PnL: {money(totals['unrealized_pnl'])}", f"Fees: {money(totals['fees'])}", f"Cash: {money(totals['cash'])}", f"Position value: {money(totals['position_value'])}", f"Total equity: {money(totals['equity'])}", ""]
    sections = ["\n".join(lines).rstrip()]
    for batch_id in ("batch_1", "batch_2"):
        batch_rows = [row for row in rows if row["batch_id"] == batch_id]
        if not batch_rows:
            continue
        blocks = [f"{row['name']}\nCash {money(row['cash'])} | Position value {money(row['position_value'])} | Equity {money(row['equity'])}\nTrades {row['trades']} | Realized {money(row['realized_pnl'])} | Unrealized {money(row['unrealized_pnl'])} | Drawdown {row['max_drawdown']:.2f}%" for row in batch_rows]
        sections.append(f"{batch_rows[0]['batch_name']}\n\n" + "\n\n".join(blocks))
    return "\n\n".join(sections)


def daily_report_chunks(storage=None):
    storage = storage or PaperStorage()
    text = format_report("📊 PAPER TRADING DAILY REPORT", storage, datetime.now(timezone.utc) - timedelta(days=1))
    return chunk_message(text, "Daily Report")


def weekly_report_chunks(storage=None):
    storage = storage or PaperStorage()
    text = format_report("📈 PAPER TRADING WEEKLY REPORT", storage, datetime.now(timezone.utc) - timedelta(days=7))
    return chunk_message(text, "Weekly Report")


def send_daily(storage=None, notifier=None):
    storage = storage or PaperStorage()
    return (notifier or TelegramNotifier(storage)).send_many(daily_report_chunks(storage), "daily_report")


def send_weekly(storage=None, notifier=None):
    storage = storage or PaperStorage()
    return (notifier or TelegramNotifier(storage)).send_many(weekly_report_chunks(storage), "weekly_report")
