from __future__ import annotations

from app.dashboard.analytics import (alerts, calculate_drawdown, closed_trade_rows, load_research_rows,
                                     monthly_returns, optimizer_rows, parameter_heatmap, project_root)
from app.dashboard.exports import create_pdf_report, csv_bytes, html_report, json_bytes
from app.paper.report import build_report
from app.paper.storage import PaperStorage
from app.telegram.notifier import TelegramNotifier


def _dataframe(rows):
    import pandas as pd
    return pd.DataFrame(rows)


def _metrics(report):
    state = report["state"]
    return {
        "Current balance": round(state["cash"], 2), "Equity": round(report["equity"], 2),
        "Realized PnL": round(state["realized_pnl"], 2), "Open position": "Yes" if state["position"] else "No",
        "Trades": report["trades"], "Win rate": round(report["win_rate"], 2),
        "Profit factor": report["profit_factor"], "Sharpe": round(report["sharpe"], 2),
        "Max drawdown": round(report["max_drawdown"], 2), "Expectancy": round(report["expectancy"], 2),
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
    _metric_cards(st, {"Paper Experiments": len(storage.active_experiments()), "Backtests": len(load_research_rows()), "Optimizer Results": len(optimizer_rows()), "Research Reports": len(list((project_root() / "data" / "research").glob("*_*/report.json")))})
    st.caption("All dashboard data is read-only. It never places orders or reads exchange credentials.")


def telegram_page(st):
    status = TelegramNotifier(PaperStorage()).status()
    st.subheader("Telegram Status")
    st.metric("Configuration", "Configured" if status["configured"] else "Not configured")
    st.write({"Last successful notification": status["last_success"], "Last notification error": status["last_error"]})


def main():
    import streamlit as st
    st.set_page_config(page_title="AI Trader Research Dashboard", layout="wide")
    st.title("AI Trader — Research Dashboard")
    pages = {"Overview": overview_page, "Paper Trading": paper_page, "Optimizer": optimizer_page, "Research Reports": research_page, "Strategy Comparison": comparison_page, "Telegram Status": telegram_page}
    pages[st.sidebar.radio("Page", list(pages))](st)


if __name__ == "__main__":
    main()
