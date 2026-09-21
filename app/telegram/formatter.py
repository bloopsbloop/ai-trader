from datetime import datetime, timezone


def money(value):
    return f"{value:,.2f} USDT"


def trade_message(experiment, order, state, equity, entry=None):
    title = "🟢 PAPER BUY" if order.side.value == "BUY" else "🔴 PAPER SELL"
    lines = [title, "", f"Experiment: {experiment.symbol} {experiment.timeframe} {experiment.strategy}"]
    if entry is not None:
        lines.append(f"Entry: {money(entry['price'])}")
    lines += [f"Price: {money(order.price)}", f"Position size: {money(order.price * order.quantity)}"]
    if order.side.value == "SELL":
        lines += [f"PnL: {money(order.realized_pnl)}", f"Fees: {money(order.commission)}"]
    lines += [f"Balance: {money(state['cash'])}", f"Equity: {money(equity)}", f"Time: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}"]
    return "\n".join(lines)


def report_message(title, date_label, rows, totals):
    lines = [title, "", f"Date: {date_label}", f"Active experiments: {len(rows)}", f"Trades: {totals['trades']}", f"Realized PnL: {money(totals['realized_pnl'])}", f"Fees: {money(totals['fees'])}", f"Total equity: {money(totals['equity'])}", "", "Experiments (listed, not ranked):"]
    for row in rows:
        lines.append(f"{row['name']} — PnL {money(row['realized_pnl'])}; trades {row['trades']}; drawdown {row['max_drawdown']:.2f}%")
    return "\n".join(lines)
