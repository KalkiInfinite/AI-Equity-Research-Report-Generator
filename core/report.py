"""
Report renderer — ReportData -> HTML (Jinja2) -> PDF (WeasyPrint).
=================================================================

The template/CSS live in ../templates and recreate the Geojit sample layout.
This module wires the data + rendered charts into the template and produces
the final PDF bytes for one-click download.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.charts import render_all
from core.schema import ReportData

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_DEFAULT_DISCLAIMER = (
    "This report is generated automatically from a company-provided financial document for "
    "demonstration purposes. It is not investment advice. Figures are extracted by an AI model "
    "and may contain errors; verify against the source document before relying on them."
)

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _value_or_blank(v: str) -> str:
    return v if (v and str(v).strip()) else "—"


_env.filters["blank"] = _value_or_blank


def render_html(data: ReportData) -> str:
    charts = render_all(data.charts)
    template = _env.get_template("report.html")
    return template.render(
        d=data,
        charts=charts,
        disclaimer=data.disclaimer.strip() or _DEFAULT_DISCLAIMER,
    )


def render_pdf(data: ReportData) -> bytes:
    """Render the report to PDF bytes."""
    from weasyprint import HTML  # imported lazily so the rest works without system libs

    html = render_html(data)
    return HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf()
