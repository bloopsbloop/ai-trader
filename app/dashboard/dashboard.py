from __future__ import annotations

from pathlib import Path

from app.dashboard.analytics import (alerts, calculate_drawdown, closed_trade_rows, load_research_rows,
                                     monthly_returns, optimizer_rows, parameter_heatmap, project_root)
from app.dashboard.exports import create_pdf_report, csv_bytes, html_report, json_bytes
from app.paper.report import build_report
