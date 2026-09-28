from __future__ import annotations

from app.dashboard.analytics import (alerts, calculate_drawdown, closed_trade_rows, load_research_rows,
                                     monthly_returns, optimizer_rows, parameter_heatmap, project_root)
from app.dashboard.exports import create_pdf_report, csv_bytes, html_report, json_bytes
from app.paper.report import build_report
from app.paper.storage import PaperStorage
from app.telegram.notifier import TelegramNotifier
from app.dashboard.ai_pages import ml_shadow_page, news_page, scanner_page


def _dataframe(rows):
    import pandas as pd
    return pd.DataFrame(rows)


def _metrics(report):
    state = report["state"]
    insufficient = "Not enough data" if report["trades"] < 2 else None
    return {
        "Cash": round(state["cash"], 2), "Position Value": round(report["equity"] - state["cash"], 2), "Equity": round(report["equity"], 2),
        "Realized PnL": round(state["realized_pnl"], 2), "Open position": "Yes" if state["position"] else "No",
        "Trades": report["trades"], "Win rate": insufficient or round(report["win_rate"], 2),
        "Profit factor": insufficient or report["profit_factor"], "Sharpe": insufficient or round(report["sharpe"], 2),
        "Max drawdown": round(report["max_drawdown"], 2), "Expectancy": insufficient or round(report["expectancy"], 2),
    }


def _metric_cards(st, metrics):
    cards = st.columns(4)
    for index, (name, value) in enumerate(metrics.items()):
        cards[index % 4].metric(name, value)


def exports(st, title, metrics, trades, benchmark, identity):
    st.subheader("Export")
    st.download_button("Export CSV", csv_bytes(trades), file_name=f"{identity}_trades.csv", mime="text/csv")
    st.download_button("Export JSON", json_bytes({"metrics": metrics, "trades": trades, "benchmark": benchmark}), file_name=f"{identity}_report.json", mime="application/json")
    st.download_button("Export HTML Report", html_report(title, metrics, trades), file_name=f"{identity}_report.html", mime="text/html")
    pdf_path = project_root() / "output" / "pdf" / f"{identity}_report.pdf"
    create_pdf_report(pdf_path, title, metrics, trades, benchmark)
    st.download_button("Export PDF Report", pdf_path.read_bytes(), file_name=pdf_path.name, mime="application/pdf")


def paper_page(st):
    storage = PaperStorage()
    experiments = storage.active_experiments()
    if not experiments:
        st.info("No paper experiments exist yet.")
        return
    options = {f"{experiment.symbol} {experiment.timeframe} — {experiment.strategy} ({experiment.experiment_id[:8]})": experiment.experiment_id for experiment, _ in experiments}
    experiment_id = options[st.selectbox("Experiment", list(options))]
    report = build_report(experiment_id, storage)
    _metric_cards(st, _metrics(report))
    curve = report["state"].get("equity_curve", [])
    closed = closed_trade_rows(storage.trades(experiment_id))
    if curve:
        chart = _dataframe(curve).set_index("time")
        st.subheader("Equity & Balance Curve")
        st.line_chart(chart[[column for column in ("equity", "cash") if column in chart.columns]])
        st.subheader("Drawdown Curve")
        st.line_chart(_dataframe(calculate_drawdown(curve)).set_index("time"))
        st.subheader("Monthly Returns")
        st.bar_chart(_dataframe(monthly_returns(curve)).set_index("month"))
    else:
        st.info("Charts appear after the first forward candle.")
    st.subheader("Trade List")
    st.dataframe(_dataframe(closed), use_container_width=True)
    active_alerts = alerts({"trades": report["trades"], "max_drawdown": report["max_drawdown"], "expectancy": report["expectancy"]})
    if active_alerts:
        st.warning("Alerts: " + ", ".join(active_alerts))
    exports(st, f"Paper report: {experiment_id}", _metrics(report), closed, {"equity": report["benchmark_equity"]}, experiment_id)


def optimizer_page(st):
    rows = optimizer_rows()
    if not rows:
        st.info("No optimizer exports found.")
        return
    st.subheader("Top 20 Parameter Combinations")
    st.dataframe(_dataframe(rows[:20]), use_container_width=True)
    heatmap = parameter_heatmap(rows)
    if heatmap:
        try:
            import matplotlib
        except ImportError:
            st.warning("Heatmap unavailable. Install matplotlib.")
            return
        frame = _dataframe([{"parameter_a": key[0], "parameter_b": key[1], "score": value} for key, value in heatmap.items()])
        st.subheader("Parameter Heatmap")
        st.dataframe(frame.pivot(index="parameter_a", columns="parameter_b", values="score").style.background_gradient(cmap="viridis"), use_container_width=True)
    research = load_research_rows()
    st.subheader("Parameter Stability & Walk-Forward")
    st.dataframe(_dataframe([{"symbol": row["symbol"], "timeframe": row["timeframe"], "strategy": row["strategy"], "parameter_stability": row["parameter_stability"], "walk_forward_returns": row["walk_forward_returns"]} for row in research]), use_container_width=True)


def research_page(st):
    rows = load_research_rows()
    if not rows:
        st.info("No research reports found.")
        return
    st.subheader("Out-of-Sample Research")
    st.dataframe(_dataframe([{key: value for key, value in row.items() if key not in {"parameters", "walk_forward_returns"}} for row in rows]), use_container_width=True)


def comparison_page(st):
    rows = load_research_rows()
    if not rows:
        st.info("No comparison data found.")
        return
    columns = ["symbol", "timeframe", "strategy", "test_return", "test_max_drawdown", "test_sharpe", "test_profit_factor", "test_expectancy", "trades", "buy_hold_return", "buy_hold_max_drawdown", "buy_hold_sharpe"]
    st.subheader("Strategy vs Buy & Hold")
    st.dataframe(_dataframe([{key: row[key] for key in columns} for row in rows]), use_container_width=True)


def overview_page(st):
    storage = PaperStorage()
    reports = [build_report(experiment.experiment_id, storage) for experiment, _ in storage.active_experiments()]
    states = [report["state"] for report in reports]
    _metric_cards(st, {"Total Equity": round(sum(report["equity"] for report in reports), 2), "Total Cash": round(sum(state["cash"] for state in states), 2), "Position Value": round(sum(report["equity"] - state["cash"] for report, state in zip(reports, states)), 2), "Unrealized PnL": round(sum(report["unrealized_pnl"] for report in reports), 2), "Realized PnL": round(sum(state["realized_pnl"] for state in states), 2), "Active Positions": sum(bool(state["position"]) for state in states), "Trades Today": sum(report["trades"] for report in reports), "Queued Notifications": len(storage.pending_notifications())})
    last_candle = max((state.get("last_processed_candle") or "" for state in states), default="—")
    telegram = TelegramNotifier(storage).status()
    st.subheader("System Status")
    _metric_cards(st, {"Telegram": telegram["health"], "Runner": "Experiments active" if states else "No active experiments", "SQLite": "Connected", "Last candle": last_candle})
    st.caption("All dashboard data is read-only. It never places orders or reads exchange credentials.")


def telegram_page(st):
    status = TelegramNotifier(PaperStorage()).status()
    st.subheader("Telegram Status")
    st.metric("Health", status["health"])
    st.write({"Last successful notification": status["last_success"], "Last notification error": status["last_error"]})


def telegram_queue_page(st):
    st.subheader("Telegram Queue")
    st.dataframe(_dataframe(PaperStorage().notification_queue()), use_container_width=True)


def recovery_page(st):
    st.subheader("Recovery History")
    st.dataframe(_dataframe(PaperStorage().recovery_history()), use_container_width=True)


def _experiment_rows(storage):
    rows = []
    for experiment, state in storage.all_experiments():
        report = build_report(experiment.experiment_id, storage)
        rows.append({"Batch": experiment.batch_name, "Batch ID": experiment.batch_id, "Symbol": experiment.symbol,
                     "Timeframe": experiment.timeframe, "Strategy": experiment.strategy, "Equity": report["equity"],
                     "Return %": (report["equity"] / experiment.starting_balance - 1) * 100,
                     "Realized PnL": state["realized_pnl"], "Unrealized PnL": report["unrealized_pnl"],
                     "Trades": report["trades"], "Max DD": report["max_drawdown"], "Status": experiment.status.value,
                     "Started": experiment.created_at, "Open Position": bool(state["position"]), "Fees": state["fees"]})
    return rows


def _filtered_rows(st, rows):
    filters = (("Batch", "All batches"), ("Symbol", "All symbols"), ("Strategy", "All strategies"),
               ("Timeframe", "All timeframes"), ("Status", "All statuses"))
    for column, all_label in filters:
        selected = st.sidebar.selectbox(column, [all_label] + sorted({row[column] for row in rows}))
        if selected != all_label:
            rows = [row for row in rows if row[column] == selected]
    return rows


def leaderboard_page(st):
    st.subheader("Experiment Leaderboard")
    st.caption("Descriptive sorting only. Independent virtual experiment balances.")
    st.dataframe(_dataframe(_filtered_rows(st, _experiment_rows(PaperStorage()))), use_container_width=True)


def batch_comparison_page(st):
    rows = _experiment_rows(PaperStorage())
    result = []
    for batch in sorted({row["Batch"] for row in rows}):
        selected = [row for row in rows if row["Batch"] == batch]
        result.append({"Batch": batch, "Experiments": len(selected), "Active positions": sum(row["Open Position"] for row in selected),
                       "Closed trades": sum(row["Trades"] for row in selected), "Total equity": sum(row["Equity"] for row in selected),
                       "Realized PnL": sum(row["Realized PnL"] for row in selected), "Unrealized PnL": sum(row["Unrealized PnL"] for row in selected),
                       "Fees": sum(row["Fees"] for row in selected), "Average drawdown": sum(row["Max DD"] for row in selected) / len(selected),
                       "Profitable": sum(row["Return %"] > 0 for row in selected), "Losing": sum(row["Return %"] < 0 for row in selected)})
    st.subheader("Batch Comparison")
    st.caption("Independent virtual experiment balances; totals are research summaries, not one real portfolio.")
    st.dataframe(_dataframe(result), use_container_width=True)


def strategy_matrix_page(st):
    rows = _experiment_rows(PaperStorage())
    metric = st.selectbox("Metric", ["Return %", "Trades", "Max DD", "Status"])
    frame = _dataframe(rows)
    frame["Market"] = frame["Symbol"] + " " + frame["Timeframe"]
    st.dataframe(frame.pivot_table(index="Strategy", columns="Market", values=metric, aggfunc="first"), use_container_width=True)


def activity_page(st):
    st.subheader("Recent Signals")
    st.dataframe(_dataframe(PaperStorage().recent_signal_events(100)), use_container_width=True)


def main():
    import streamlit as st
    st.set_page_config(page_title="AI Trader Research Dashboard", layout="wide")
    st.title("AI Trader — Research Dashboard")
    pages = {"Overview": overview_page, "Paper Trading": paper_page, "Batch Comparison": batch_comparison_page,
             "AI Scanner": scanner_page, "AI News": news_page, "ML Shadow": ml_shadow_page,
             "Experiment Leaderboard": leaderboard_page, "Strategy Matrix": strategy_matrix_page, "Recent Signals": activity_page,
             "Optimizer": optimizer_page, "Research Reports": research_page, "Strategy Comparison": comparison_page,
             "Telegram Status": telegram_page, "Telegram Queue": telegram_queue_page, "Recovery History": recovery_page}
    pages[st.sidebar.radio("Page", list(pages))](st)


if __name__ == "__main__":
    main()

