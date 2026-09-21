from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any, Iterable

def csv_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    rows = list(rows)
    if not rows:
        return b""
    fields = list(dict.fromkeys(key for row in rows for key in row))
    output = __import__("io").StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, default=str, allow_nan=False).encode("utf-8")


def html_report(title: str, metrics: dict[str, Any], trades: Iterable[dict[str, Any]]) -> bytes:
    metrics_html = "".join(f"<li><b>{html.escape(str(key))}</b>: {html.escape(str(value))}</li>" for key, value in metrics.items())
    trade_rows = list(trades)
    headers = list(trade_rows[0]) if trade_rows else []
    table = "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(header, '')))}</td>" for header in headers) + "</tr>" for row in trade_rows)
    header_html = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    return f"<!doctype html><html><body><h1>{html.escape(title)}</h1><h2>Performance</h2><ul>{metrics_html}</ul><h2>Trades</h2><table border='1'><tr>{header_html}</tr>{table}</table></body></html>".encode("utf-8")


def create_pdf_report(path: Path, title: str, metrics: dict[str, Any], trades: Iterable[dict[str, Any]], benchmark: dict[str, Any] | None = None) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    flow = [Paragraph(title, styles["Title"]), Spacer(1, 0.35 * cm), Paragraph("Performance", styles["Heading2"])]
    metric_rows = [[str(key).replace("_", " ").title(), str(value)] for key, value in metrics.items()]
    if benchmark:
        metric_rows.extend([[f"Buy & Hold {key.replace('_', ' ').title()}", str(value)] for key, value in benchmark.items()])
    table = Table(metric_rows, colWidths=[8 * cm, 8 * cm])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke)]))
    flow.extend([table, Spacer(1, 0.35 * cm), Paragraph("Closed Trades", styles["Heading2"])])
    trade_rows = list(trades)
    if trade_rows:
        fields = ["Entry", "Exit", "PnL", "Fees", "Duration", "Signal"]
        data = [fields] + [[str(row.get(field, "")) for field in fields] for row in trade_rows]
        trades_table = Table(data, repeatRows=1, colWidths=[3.1 * cm, 3.1 * cm, 2 * cm, 2 * cm, 3.1 * cm, 3.1 * cm])
        trades_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke), ("FONTSIZE", (0, 0), (-1, -1), 7)]))
        flow.append(trades_table)
    else:
        flow.append(Paragraph("No closed trades yet.", styles["BodyText"]))
    SimpleDocTemplate(str(path), pagesize=A4, rightMargin=1.2 * cm, leftMargin=1.2 * cm).build(flow)
    return path
