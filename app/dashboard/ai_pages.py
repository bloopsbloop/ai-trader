import os
from app.ai.storage import read_dashboard, PORTFOLIO_LABELS
from app.ml.shadow import shadow_report


def scanner_page(st):
    data = read_dashboard(os.getenv("AI_SCANNER_DB"))
    st.subheader("AI Scanner — Batch 3 PAPER")
    st.caption("Experimental heuristic score, not a probability. Independent news and market-only portfolios.")
    st.write({"Runner": data["runtime"] or "Not started with the standalone runner"})
    scan = data["scan"]
    if not scan:
        st.info("No AI scans yet. Run python -m app.ai.run; add --paper for virtual fills.")
        return
    st.write({"Mode": scan["mode"], "Cutoff UTC": scan["cutoff"], "Decision UTC": scan["decision_at"], "Stale scan": scan["stale_scan"]})
    if scan["errors"]:
        st.warning("Some inputs or executions were unavailable; see diagnostics.")
        st.write(scan["errors"])
    for name, rows in scan["rankings"].items():
        st.subheader(PORTFOLIO_LABELS.get(name, name))
        st.dataframe([{key: value for key, value in row.items() if key != "features"} for row in rows], use_container_width=True)
        with st.expander("Market features 15m / 1h / 4h"):
            st.json(rows)
    st.subheader("Paper accounts")
    st.write({PORTFOLIO_LABELS.get(name, name): account for name, account in data["accounts"].items()})
    st.subheader("AI fills / rejections")
    st.dataframe(data["trades"], use_container_width=True)
    st.subheader("AI Telegram queue")
    st.write(data["queue"])


def news_page(st):
    data = read_dashboard(os.getenv("AI_SCANNER_DB"))
    st.subheader("AI News")
    st.caption("Publication time and first observation are stored separately. Default scorer: untrained English headline baseline.")
    if data["scan"]:
        st.write(data["scan"]["source_health"])
        st.dataframe(data["scan"]["news_scores"], use_container_width=True)
    st.dataframe(data["news"], use_container_width=True)


def ml_shadow_page(st):
    path = os.getenv("AI_SCANNER_DB")
    report = shadow_report(path) if path else shadow_report()
    st.subheader("ML Shadow — forward observation")
    st.warning("ML shadow predictions do not control paper trades.")
    st.write({"Model": report["model_version"], "Mode": report["mode"],
              "Latest prediction UTC": report["latest_prediction_time"]})
    if report["metrics"]["status"] == "Not enough data":
        st.info(f"Not enough data: {report['metrics']['resolved_count']} resolved; "
                f"at least {report['metrics']['minimum_resolved']} required.")
    st.write(report["metrics"])
    columns = ("candle_close_timestamp", "symbol", "probability_positive", "decision_threshold",
               "shadow_decision", "heuristic_decision", "resolved_at", "actual_net_return")
    st.dataframe([{key: row.get(key) for key in columns} for row in report["latest"]],
                 use_container_width=True)
