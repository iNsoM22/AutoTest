"""Professional HTML and PDF report generator for processed recordings."""

from __future__ import annotations

import html
from pathlib import Path

from src.export.recording_bundle import ProcessedRecordingBundle
from src.processing.models import ProcessedEvent
from src.utils import target_label


def render_recording_report_html(bundle: ProcessedRecordingBundle) -> str:
    """Render a clean, readable HTML document suitable for PDF printing."""
    title = html.escape(bundle.session_name or "Processed Recording Report")
    start_url = html.escape(bundle.start_url or "(none)")
    exported_str = html.escape(bundle.exported_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    events_by_id = {ev.id: ev for ev in bundle.processed_events}

    scenario_summary_rows: list[str] = []
    scenario_sections: list[str] = []

    if bundle.scenarios:
        for sc in bundle.scenarios:
            status_label = "Included" if sc.included else "Excluded"
            sc_events = [events_by_id[eid] for eid in sc.processed_event_ids if eid in events_by_id]
            scenario_summary_rows.append(
                "<tr>"
                f"<td>{sc.order_index}</td>"
                f"<td><strong>{html.escape(sc.name)}</strong></td>"
                f"<td>{len(sc_events)}</td>"
                f"<td>{status_label}</td>"
                "</tr>"
            )
            scenario_sections.append(
                _render_scenario_section(
                    f"Scenario {sc.order_index}: {sc.name}",
                    sc.description,
                    sc_events,
                )
            )
    else:
        scenario_sections.append(
            _render_scenario_section(
                "Recorded Steps",
                "All processed user interactions in chronological order.",
                bundle.processed_events,
            )
        )

    summary_table = ""
    if scenario_summary_rows:
        summary_table = (
            "<h2>Detected Scenarios</h2>"
            "<table><thead><tr>"
            "<th>#</th><th>Scenario</th><th>Steps</th><th>Status</th>"
            "</tr></thead><tbody>" + "".join(scenario_summary_rows) + "</tbody></table>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<style>
body {{
    font-family: "Segoe UI", Arial, Helvetica, sans-serif;
    color: #1f2937;
    font-size: 10pt;
    line-height: 1.45;
    margin: 0;
}}
.header {{
    border-bottom: 2px solid #2563eb;
    padding-bottom: 10px;
    margin-bottom: 16px;
}}
h1 {{
    margin: 0 0 6px 0;
    font-size: 18pt;
    color: #111827;
}}
.meta {{
    color: #4b5563;
    font-size: 9pt;
}}
h2 {{
    margin: 18px 0 8px 0;
    font-size: 13pt;
    color: #1d4ed8;
    border-bottom: 1px solid #e5e7eb;
    padding-bottom: 4px;
}}
h3 {{
    margin: 14px 0 4px 0;
    font-size: 11pt;
    color: #111827;
}}
.desc {{
    color: #4b5563;
    margin: 0 0 8px 0;
    font-size: 9.5pt;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 14px;
    font-size: 9pt;
}}
th, td {{
    border: 1px solid #d1d5db;
    padding: 6px 8px;
    text-align: left;
    vertical-align: top;
}}
th {{
    background-color: #f3f4f6;
    font-weight: 600;
    color: #111827;
}}
.badge {{
    display: inline-block;
    padding: 2px 6px;
    border-radius: 3px;
    background-color: #dbeafe;
    color: #1e40af;
    font-weight: 600;
    font-size: 8pt;
    text-transform: uppercase;
}}
.muted {{
    color: #6b7280;
    font-size: 8.5pt;
}}
</style>
</head>
<body>
<div class="header">
    <h1>{title}</h1>
    <div class="meta">
        <strong>Start URL:</strong> {start_url} &nbsp;|&nbsp;
        <strong>Viewport:</strong> {bundle.viewport_width}x{bundle.viewport_height} &nbsp;|&nbsp;
        <strong>Steps:</strong> {len(bundle.processed_events)} &nbsp;|&nbsp;
        <strong>Scenarios:</strong> {len(bundle.scenarios)}<br/>
        <strong>Exported:</strong> {exported_str}
    </div>
</div>
{summary_table}
{"".join(scenario_sections)}
</body>
</html>
"""


def _render_scenario_section(
    heading: str,
    description: str,
    events: list[ProcessedEvent],
) -> str:
    rows: list[str] = []
    for ev in events:
        url_str = str(ev.payload.get("url", "") or "")
        target = html.escape(target_label(ev.target) or url_str or "-")
        raw_val = ev.payload.get("value")
        if raw_val is None:
            raw_val = ev.payload.get("key")
        value = html.escape(str(raw_val)) if raw_val not in (None, "") else "-"
        outcomes = "; ".join(o.summary for o in ev.outcomes if o.summary)
        outcomes_html = html.escape(outcomes) if outcomes else '<span class="muted">None</span>'
        rows.append(
            "<tr>"
            f"<td>{ev.order_index}</td>"
            f'<td><span class="badge">{html.escape(ev.action_type.value)}</span></td>'
            f"<td>{html.escape(ev.summary)}<br/><span class='muted'>{target}</span></td>"
            f"<td>{value}</td>"
            f"<td>{outcomes_html}</td>"
            "</tr>"
        )
    desc_html = f'<p class="desc">{html.escape(description)}</p>' if description else ""
    return (
        f"<h3>{html.escape(heading)}</h3>"
        f"{desc_html}"
        "<table><thead><tr>"
        "<th>#</th><th>Action</th><th>Step &amp; Target</th>"
        "<th>Value</th><th>Observed Outcomes</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def export_recording_pdf(bundle: ProcessedRecordingBundle, output_path: Path) -> Path:
    """Render `bundle` to a styled A4 PDF file using PySide6's `QPrinter`."""
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
    from PySide6.QtPrintSupport import QPrinter

    output_path.parent.mkdir(parents=True, exist_ok=True)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(output_path))
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageMargins(
        QMarginsF(14.0, 14.0, 14.0, 14.0),
        QPageLayout.Unit.Millimeter,
    )

    doc = QTextDocument()
    doc.setHtml(render_recording_report_html(bundle))
    doc.print_(printer)
    return output_path
