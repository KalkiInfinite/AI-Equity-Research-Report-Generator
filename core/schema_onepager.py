"""
One-pager schema — lightweight summary structure for Task 2.
================================================================

A simplified schema with nine sections, used as the extraction target
for the one-pager view (instead of the heavy ReportData for PDFs).

Sections:
  Company Snapshot, Business Overview, Financial Highlights,
  Growth & Margin Commentary, Segment Insights, Recent Developments,
  Key Risks, Analyst Takeaway, Sources Used.

Charts are reused from schema.py (ChartSpec) for consistency.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field

from core.schema import ChartSpec


class CompanySnapshot(BaseModel):
    ticker: str = Field(default="", description="Stock ticker symbol")
    sector: str = Field(default="", description="Industry / sector classification")
    market_cap: str = Field(default="", description="Market capitalisation, e.g. 'Rs. 295,735 cr'")
    current_price: str = Field(default="", description="Current market price with currency")
    pe_ratio: str = Field(default="", description="Price-to-earnings ratio")
    pb_ratio: str = Field(default="", description="Price-to-book ratio")
    dividend_yield: str = Field(default="", description="Dividend yield %")
    week52_high: str = Field(default="", description="52-week high")
    week52_low: str = Field(default="", description="52-week low")
    beta: str = Field(default="", description="Stock beta")
    revenue_ttm: str = Field(default="", description="Trailing-twelve-month revenue")
    net_income_ttm: str = Field(default="", description="Trailing-twelve-month net income / PAT")
    employees: str = Field(default="", description="Approximate employee count")
    founded: str = Field(default="", description="Year founded or incorporated")
    headquarters: str = Field(default="", description="Headquarters location")


class BusinessOverview(BaseModel):
    summary: str = Field(default="", description="1-2 paragraph description of the business and what it does")
    business_segments: List[str] = Field(default_factory=list, description="Key business segments or product lines")


class FinancialHighlight(BaseModel):
    metric: str = Field(description="Metric name, e.g. 'Revenue', 'EBITDA', 'PAT', 'EPS'")
    value: str = Field(description="Value with units, e.g. 'Rs. 12,500 cr'")
    period: str = Field(description="Period this value refers to, e.g. 'Q3 FY26', 'FY25'")
    yoy_change: str = Field(default="", description="Year-over-year change, e.g. '+12.3%' or '-5.1%'")


class SegmentInsight(BaseModel):
    segment_name: str = Field(description="Name of the segment / division")
    revenue: str = Field(default="", description="Segment revenue with units")
    growth: str = Field(default="", description="Growth rate / trend")
    commentary: str = Field(default="", description="1-2 sentence commentary on segment performance")


class RecentDevelopment(BaseModel):
    date: str = Field(default="", description="Date or quarter of the development")
    headline: str = Field(description="Brief headline of the event")
    impact: str = Field(default="", description="Impact on the business or stock, if known")


class RiskItem(BaseModel):
    category: str = Field(default="", description="Category of risk, e.g. Regulatory, Macro, Competition")
    description: str = Field(description="Description of the risk")
    severity: str = Field(default="Medium", description="Severity level: High / Medium / Low")


class OnePagerData(BaseModel):
    """The complete data backing one one-pager summary."""

    company_name: str = Field(default="", description="Full company name")
    report_date: str = Field(default="", description="Report date as a string, e.g. 'June 2026'")

    # ── Sections ────────────────────────────────────────────────────────────
    snapshot: CompanySnapshot = Field(default_factory=CompanySnapshot)
    business_overview: BusinessOverview = Field(default_factory=BusinessOverview)
    financial_highlights: List[FinancialHighlight] = Field(default_factory=list)
    growth_commentary: str = Field(default="", description="1-2 paragraphs on growth trajectory, margin trends, and outlook")
    segment_insights: List[SegmentInsight] = Field(default_factory=list)
    recent_developments: List[RecentDevelopment] = Field(default_factory=list)
    key_risks: List[RiskItem] = Field(default_factory=list)
    analyst_takeaway: str = Field(default="", description="Concise 2-3 sentence investment takeaway or thesis")
    sources_used: List[str] = Field(default_factory=list, description="List of source documents referenced")

    # ── Charts ───────────────────────────────────────────────────────────────
    charts: List[ChartSpec] = Field(default_factory=list, description="At least one chart (revenue trend recommended)")


# Fields the UI can use to warn when extraction comes back thin.
CORE_ONEPAGER_FIELDS = [
    "snapshot", "business_overview", "financial_highlights",
    "growth_commentary", "analyst_takeaway",
]


def from_reportdata(rd) -> OnePagerData:
    """Derive a OnePagerData from an existing ReportData (Task 1 output).

    No LLM call — pure structural mapping. Gaps (segment insights, risks,
    recent developments) are left empty and can be filled later via the
    optional enrichment call in extractor_onepager.enrich_onepager().

    Charts are carried over directly.
    """
    from core.schema import ReportData

    # ── Snapshot: pull from company_data key-value pairs ─────────────────
    cd = {kv.label.lower(): kv.value for kv in rd.company_data} if hasattr(rd, 'company_data') else {}

    snapshot = CompanySnapshot(
        sector=getattr(rd, "sector", ""),
        market_cap=cd.get("market cap", cd.get("mcap", "")),
        current_price=getattr(rd, "current_price", ""),
        pe_ratio=cd.get("pe ratio", cd.get("p/e", "")),
        pb_ratio=cd.get("pb ratio", cd.get("p/b", "")),
        dividend_yield=cd.get("dividend yield", cd.get("dividend ytd", "")),
        week52_high=cd.get("52w high", cd.get("52-week high", "")),
        week52_low=cd.get("52w low", cd.get("52-week low", "")),
        beta=cd.get("beta", ""),
        revenue_ttm=_extract_financial_item(rd, "revenue"),
        net_income_ttm=_extract_financial_item(rd, "pat", "net profit", "profit after tax"),
        employees=cd.get("outstanding shares", cd.get("shares", "")),
    )

    # ── Business overview ────────────────────────────────────────────────
    overview = BusinessOverview(
        summary=getattr(rd, "description", ""),
    )

    # ── Financial highlights from P&L table ──────────────────────────────
    highlights = _extract_financial_highlights(rd)

    # ── Growth commentary: merge highlights + key_highlights ─────────────
    growth_parts = []
    if hasattr(rd, "highlights") and rd.highlights:
        growth_parts.append("Key Results:\n" + "\n".join(f"• {h}" for h in rd.highlights))
    if hasattr(rd, "key_highlights") and rd.key_highlights:
        growth_parts.append("Outlook & Drivers:\n" + "\n".join(f"• {h}" for h in rd.key_highlights))
    growth_commentary = "\n\n".join(growth_parts)

    # ── Analyst takeaway ─────────────────────────────────────────────────
    takeaway = getattr(rd, "thesis", "")
    if not takeaway:
        # Fall back to rating + target if thesis is blank
        rating = getattr(rd, "rating", "")
        target = getattr(rd, "target_price", "")
        if rating or target:
            takeaway = f"Rating: {rating}, Target: {target}".strip(", ")

    # ── Risk extraction from key_highlights ──────────────────────────────
    risks: list[RiskItem] = []
    if hasattr(rd, "key_highlights"):
        for h in rd.key_highlights:
            lower = h.lower()
            if any(w in lower for w in ("risk", "concern", "headwind", "challenge", "threat", "volatile")):
                risks.append(RiskItem(description=h))

    return OnePagerData(
        company_name=getattr(rd, "company_name", ""),
        report_date=getattr(rd, "report_date", ""),
        snapshot=snapshot,
        business_overview=overview,
        financial_highlights=highlights,
        growth_commentary=growth_commentary.strip(),
        analyst_takeaway=takeaway,
        key_risks=risks,
        charts=list(getattr(rd, "charts", [])),
    )


def _extract_financial_item(rd, *keywords) -> str:
    """Search financial tables for a row matching any of the keywords and
    return the most recent column value."""
    financials = getattr(rd, "financials", []) or []
    for tbl in financials:
        for row in tbl.rows:
            label_lower = row.label.lower()
            if any(kw in label_lower for kw in keywords):
                if row.cells:
                    return row.cells[-1]  # most recent column
    return ""


def _extract_financial_highlights(rd) -> list[FinancialHighlight]:
    """Pull key metrics from P&L table rows into financial highlights."""
    financials = getattr(rd, "financials", []) or []
    key_metrics = [
        "revenue", "total income", "ebitda", "ebit",
        "profit before tax", "pbt", "profit after tax", "pat", "net profit",
        "eps", "earnings per share",
    ]
    found: list[FinancialHighlight] = []
    seen: set[str] = set()

    for tbl in financials:
        columns = list(tbl.columns) if tbl.columns else []
        for row in tbl.rows:
            label_lower = row.label.lower().strip()
            for km in key_metrics:
                if km in label_lower and km not in seen:
                    seen.add(km)
                    value = row.cells[-1] if row.cells else ""
                    period = columns[-1] if columns else ""
                    # Try to compute YoY from second-last cell
                    yoy = ""
                    if len(row.cells) >= 2 and row.cells[-2]:
                        try:
                            curr = float(row.cells[-1].replace(",", "").replace("Rs.", "").replace("cr", "").strip())
                            prev = float(row.cells[-2].replace(",", "").replace("Rs.", "").replace("cr", "").strip())
                            if prev and prev != 0:
                                pct = ((curr - prev) / abs(prev)) * 100
                                yoy = f"{pct:+.1f}%"
                        except (ValueError, ZeroDivisionError):
                            pass
                    found.append(FinancialHighlight(
                        metric=row.label.strip(),
                        value=value,
                        period=period,
                        yoy_change=yoy,
                    ))
                    break
    return found
