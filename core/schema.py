"""
Report schema — the single source of truth for every template field.
============================================================================

This module defines, in one place, the entire structure of the research
report. The LLM extractor fills an instance of `ReportData`; the Jinja2
template renders one. To add a new field or section to the report you only
touch this file and `templates/report.html` — nothing else.

Design notes
------------
* Tables are modelled generically as (title, columns, rows) so the same
  rendering code handles the P&L, balance sheet, shareholding, estimates,
  etc. Adding a new table for a new company requires no code changes.
* Everything is optional. Missing data is the normal case, not an error —
  the template renders blanks / "NA" for anything absent (graceful handling).
* Charts are described declaratively (ChartSpec) so the LLM can request a
  revenue-trend bar chart, a margin line chart, etc., and `charts.py`
  renders whatever it is given.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class KeyValue(BaseModel):
    """A single label/value pair, e.g. 'Market Cap (Rs.cr)' -> '295,735'."""
    label: str = Field(description="The metric name / label")
    value: str = Field(description="The metric value as shown, including units. Use 'NA' if unknown.")


class TableRow(BaseModel):
    """One row of a generic table: a label plus one cell per column."""
    label: str = Field(description="Row label, e.g. 'Revenue', 'EBITDA', 'Promoters'")
    cells: List[str] = Field(
        default_factory=list,
        description="One value per column, same order/length as the table's `columns`. Use '' for a missing cell.",
    )


class Table(BaseModel):
    """A generic titled table with column headers and labelled rows."""
    title: str = Field(description="Table heading, e.g. 'Profit & Loss (Rs. cr)'")
    columns: List[str] = Field(
        default_factory=list,
        description="Column headers AFTER the row-label column, e.g. ['FY23A','FY24A','FY25A','FY26E','FY27E']",
    )
    rows: List[TableRow] = Field(default_factory=list)


class ChartSeries(BaseModel):
    name: str = Field(description="Series name shown in the legend, e.g. 'Revenue' or 'EBITDA margin %'")
    y: List[float] = Field(description="Numeric y-values aligned with the chart's x labels")


class ChartSpec(BaseModel):
    """Declarative chart description rendered by charts.py."""
    title: str
    kind: str = Field(default="bar", description="'bar' or 'line'")
    x: List[str] = Field(description="X-axis category labels, e.g. ['FY23','FY24','FY25','FY26E','FY27E']")
    series: List[ChartSeries] = Field(default_factory=list)
    y_label: str = Field(default="", description="Optional y-axis label")


class RatingRow(BaseModel):
    date: str = ""
    rating: str = ""
    target: str = ""


class ReportData(BaseModel):
    """The complete data backing one research report."""

    # ── Header / cover block ────────────────────────────────────────────────
    company_name: str = Field(default="", description="Full company name")
    sector: str = Field(default="", description="Industry / sector classification")
    report_date: str = Field(default="", description="Report date as a string")
    rating: str = Field(default="", description="Investment rating, e.g. BUY / HOLD / ACCUMULATE / SELL")
    target_price: str = Field(default="", description="Target price with currency, e.g. 'Rs. 337'")
    current_price: str = Field(default="", description="Current market price (CMP) with currency")
    upside: str = Field(default="", description="Upside/downside to target, e.g. '+12%'")
    thesis: str = Field(default="", description="One-line investment thesis headline")

    # ── Narrative ───────────────────────────────────────────────────────────
    description: str = Field(default="", description="2-4 sentence company description / business overview")
    highlights: List[str] = Field(
        default_factory=list,
        description="Front-page bullet highlights (quarterly results, growth, margins, key events)",
    )
    key_highlights: List[str] = Field(
        default_factory=list,
        description="Deeper analytical narrative bullets (outlook, drivers, risks)",
    )

    # ── Side / data tables ──────────────────────────────────────────────────
    company_data: List[KeyValue] = Field(
        default_factory=list,
        description="Company data box: Market Cap, 52-Week High/Low, Enterprise Value, Outstanding Shares, "
                    "Free Float %, Dividend Yield, Avg Volume, Beta, Face Value, etc.",
    )
    shareholding: Optional[Table] = Field(
        default=None, description="Shareholding pattern table (Promoters/FII/MF/Public) across recent quarters"
    )
    price_performance: Optional[Table] = Field(
        default=None, description="Absolute/relative returns over 3M / 6M / 1Y"
    )
    estimates: Optional[Table] = Field(
        default=None, description="Old vs New estimates table (Revenue/EBITDA/Margins/PAT/EPS, with change %)"
    )

    # ── Detailed financials (one Table each: P&L, Balance Sheet, Cash Flow, Ratios) ──
    financials: List[Table] = Field(
        default_factory=list,
        description="Detailed financial statements, one Table per statement (P&L, Balance Sheet, Cash Flow, Ratios)",
    )

    # ── Charts ──────────────────────────────────────────────────────────────
    charts: List[ChartSpec] = Field(
        default_factory=list,
        description="At least a revenue-trend chart and a margin chart when the data is available",
    )

    # ── Back matter ─────────────────────────────────────────────────────────
    recommendation_history: List[RatingRow] = Field(
        default_factory=list, description="Past rating/target history rows"
    )
    analyst_name: str = Field(default="", description="Author / analyst name if present")
    disclaimer: str = Field(
        default="",
        description="Short disclaimer text. Leave blank to use the default boilerplate.",
    )


# Fields the acceptance criteria call "required" — used by the UI to warn (not
# fail) when extraction comes back thin.
CORE_FIELDS = ["company_name", "description", "highlights", "company_data", "financials"]
