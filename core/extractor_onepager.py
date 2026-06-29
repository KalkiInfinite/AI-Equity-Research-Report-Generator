"""
One-pager extraction agent — context document -> structured OnePagerData via DeepSeek.
======================================================================================

Mirrors the pattern in extractor.py but targets the lighter OnePagerData schema.
Uses the same LiteLLM-backed, provider-agnostic approach with tool/function calling.

Also exports `chat_with_context` for the sidebar chatbot, which grounds answers
in the current one-pager content.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential

from core.ingest import IngestedDoc
from core.schema_onepager import OnePagerData

DEFAULT_ONEPAGER_MODEL = os.getenv("EXTRACTION_MODEL", "deepseek/deepseek-chat")
CHAT_MODEL = os.getenv("CHAT_MODEL", "deepseek/deepseek-v4-pro")
MAX_TEXT_CHARS = 60_000

litellm.drop_params = True

SYSTEM_PROMPT_ONEPAGER = """You are an equity research analyst's assistant. You read a company's \
financial document (earnings press release, investor presentation, results, or CSV/TXT of \
financials) and produce a structured one-page company summary for an investment research tool.

Rules:
- Extract ONLY what the document supports. NEVER invent numbers or facts. If a value is absent,
  leave the field blank ("" / empty list) — do not guess.
- Prefer the most recent reported period for financials and commentary.
- `snapshot`: populate every field the document supports (ticker, sector, market_cap, etc.).
  Use "NA" only when a field is genuinely unknown and the document gives no clue.
- `business_overview.summary`: 1-2 paragraphs describing what the company does, its core
  business model, and key competitive position if mentioned.
- `business_overview.business_segments`: list the main segments/divisions by name.
- `financial_highlights`: 4-8 key metrics (Revenue, EBITDA, PAT, EPS, margins, etc.)
  with their values, period, and YoY change where available.
- `growth_commentary`: 1-2 paragraphs on revenue/margin trends, growth drivers,
  and near-term outlook based on the document.
- `segment_insights`: 1-2 sentences per segment on performance and trajectory.
- `recent_developments`: any notable recent events, orders, regulatory changes, M&A, etc.
  mentioned in the document.
- `key_risks`: risks explicitly discussed or implied by the document.
- `analyst_takeaway`: 2-3 sentence summary of the investment case.
- `sources_used`: list the filename(s) this extraction was based on.
- Always provide at least one `charts` entry (a revenue-trend bar chart) when the numbers allow it.
  Use `kind` = 'bar' or 'line'. x-labels should match the periods in the document.

Call the `emit_onepager` function exactly once with the structured data."""


def _tool_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "emit_onepager",
            "description": "Return the structured one-pager data extracted from the document.",
            "parameters": OnePagerData.model_json_schema(),
        },
    }


def _user_content(doc: IngestedDoc, company_name: str):
    instruction = (
        f"Company name (as provided by the user): {company_name or 'unknown, infer from the document'}.\n\n"
        "Extract the one-pager summary data from the document below and call `emit_onepager`."
    )

    if doc.fmt == "pdf" and not doc.has_usable_text and doc.pdf_bytes:
        import base64 as b64
        encoded = b64.standard_b64encode(doc.pdf_bytes).decode("utf-8")
        return [
            {"type": "text", "text": instruction},
            {"type": "file", "file": {"file_data": f"data:application/pdf;base64,{encoded}"}},
        ]

    text = doc.text[:MAX_TEXT_CHARS]
    truncated = "\n\n[... document truncated ...]" if len(doc.text) > MAX_TEXT_CHARS else ""
    return f"{instruction}\n\n=== DOCUMENT ({doc.filename}) ===\n{text}{truncated}"


def _coerce(raw: dict) -> OnePagerData:
    try:
        return OnePagerData.model_validate(raw)
    except Exception:
        clean: dict[str, Any] = {k: raw[k] for k in OnePagerData.model_fields if k in raw}
        try:
            return OnePagerData.model_validate(clean)
        except Exception:
            return OnePagerData(company_name=str(raw.get("company_name", "")))


def _parse_message(message) -> OnePagerData | None:
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        if call.function and call.function.name == "emit_onepager":
            try:
                return _coerce(json.loads(call.function.arguments))
            except Exception:
                continue

    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return _coerce(json.loads(match.group()))
            except Exception:
                return None
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=12))
def extract_onepager(doc: IngestedDoc, company_name: str, model: str = DEFAULT_ONEPAGER_MODEL) -> OnePagerData:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_ONEPAGER},
        {"role": "user", "content": _user_content(doc, company_name)},
    ]

    kwargs = dict(model=model, messages=messages, max_tokens=8192, temperature=0)
    try:
        resp = litellm.completion(
            tools=[_tool_spec()],
            tool_choice={"type": "function", "function": {"name": "emit_onepager"}},
            **kwargs,
        )
    except Exception:
        resp = litellm.completion(tools=[_tool_spec()], tool_choice="auto", **kwargs)

    data = _parse_message(resp.choices[0].message)
    if data is None:
        data = OnePagerData(company_name=company_name)
    if company_name and not data.company_name:
        data.company_name = company_name
    return data


def enrich_onepager(onepager: OnePagerData, report_data, model: str | None = None) -> OnePagerData:
    if model is None:
        model = DEFAULT_ONEPAGER_MODEL
    """Lightweight LLM call to fill one-pager gaps from an already-extracted ReportData.

    This is much faster/cheaper than re-extracting from the raw document because
    the LLM works with already-structured data instead of raw text. Only the
    sections that ReportData doesn't directly map to are filled:
      - business_segments
      - segment_insights
      - recent_developments
      - key_risks (if not already populated from key_highlights parsing)
      - analyst_takeaway (if thesis was missing)

    Returns the enriched OnePagerData (mutates and returns the same object).
    """
    import json as _json

    # Build a concise summary of the ReportData for the LLM
    rd_text_parts = []
    rd_text_parts.append(f"Company: {report_data.company_name}")
    rd_text_parts.append(f"Sector: {report_data.sector}")
    rd_text_parts.append(f"Rating: {report_data.rating}, Target: {report_data.target_price}, CMP: {report_data.current_price}")
    rd_text_parts.append(f"Thesis: {report_data.thesis}")
    rd_text_parts.append(f"\nDescription: {report_data.description}")

    if report_data.highlights:
        rd_text_parts.append("\nHighlights:")
        for h in report_data.highlights:
            rd_text_parts.append(f"  - {h}")

    if report_data.key_highlights:
        rd_text_parts.append("\nKey Highlights (outlook/drivers/risks):")
        for h in report_data.key_highlights:
            rd_text_parts.append(f"  - {h}")

    if report_data.company_data:
        rd_text_parts.append("\nCompany Data:")
        for kv in report_data.company_data:
            rd_text_parts.append(f"  {kv.label}: {kv.value}")

    if report_data.financials:
        for tbl in report_data.financials:
            rd_text_parts.append(f"\n--- {tbl.title} ---")
            if tbl.columns:
                rd_text_parts.append("  Columns: " + ", ".join(tbl.columns))
            for row in tbl.rows[:20]:  # limit rows
                cells_str = " | ".join(row.cells) if row.cells else ""
                rd_text_parts.append(f"  {row.label}: {cells_str}")

    rd_text = "\n".join(rd_text_parts)

    messages = [
        {"role": "system", "content": ENRICH_SYSTEM_PROMPT},
        {"role": "user", "content": f"=== REPORT DATA ===\n\n{rd_text}\n\nCall `emit_enrichment` with the derived one-pager sections."},
    ]

    kwargs = dict(model=model, messages=messages, max_tokens=4096, temperature=0)
    try:
        resp = litellm.completion(
            tools=[_enrich_tool_spec()],
            tool_choice={"type": "function", "function": {"name": "emit_enrichment"}},
            **kwargs,
        )
    except Exception:
        resp = litellm.completion(tools=[_enrich_tool_spec()], tool_choice="auto", **kwargs)

    enrichment = _parse_enrichment_message(resp.choices[0].message)

    if enrichment.get("business_segments") and not onepager.business_overview.business_segments:
        onepager.business_overview.business_segments = enrichment["business_segments"]

    if enrichment.get("segment_insights") and not onepager.segment_insights:
        from core.schema_onepager import SegmentInsight
        onepager.segment_insights = [SegmentInsight.model_validate(s) for s in enrichment["segment_insights"]]

    if enrichment.get("recent_developments") and not onepager.recent_developments:
        from core.schema_onepager import RecentDevelopment
        onepager.recent_developments = [RecentDevelopment.model_validate(r) for r in enrichment["recent_developments"]]

    if enrichment.get("key_risks"):
        from core.schema_onepager import RiskItem
        new_risks = [RiskItem.model_validate(r) for r in enrichment["key_risks"]]
        if not onepager.key_risks or all(not r.description for r in onepager.key_risks):
            onepager.key_risks = new_risks

    if enrichment.get("analyst_takeaway") and not onepager.analyst_takeaway:
        onepager.analyst_takeaway = enrichment["analyst_takeaway"]

    return onepager


def _parse_enrichment_message(message) -> dict:
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        if call.function and call.function.name == "emit_enrichment":
            try:
                import json as _json
                return _json.loads(call.function.arguments)
            except Exception:
                continue
    return {}


ENRICH_SYSTEM_PROMPT = """You are an equity research analyst's assistant. You are given the \
structured data from a Geojit-style research report (ReportData) for a company. Your job is to \
fill in the missing sections of a one-pager summary by analysing what's already extracted.

The ReportData already contains:
- company_name, sector, rating, target_price, current_price
- description (business overview)
- highlights (key quarterly result bullets)
- key_highlights (analytical outlook/driver/risk bullets)
- company_data (market data key-value pairs)
- financials (detailed P&L, Balance Sheet, Cash Flow, Ratios tables)
- charts

Produce these one-pager sections that are NOT directly in ReportData:

1. business_segments: list of 2-5 business segment names inferred from the description,
   financial tables, or highlights. Just the segment names, not descriptions.

2. segment_insights: for each segment, provide:
   - segment_name: the segment name
   - revenue: best-guess revenue contribution from the financial tables if broken out
   - growth: growth trend if mentioned
   - commentary: 1-sentence performance note

3. recent_developments: 2-4 notable recent events mentioned in highlights/key_highlights:
   - date: approximate date/quarter mentioned
   - headline: brief headline
   - impact: 1-sentence business impact

4. key_risks: 3-5 concrete risks derived from the key_highlights and financial data:
   - category: Regulatory / Macro / Competition / Operational / Financial
   - description: specific risk
   - severity: High / Medium / Low

5. analyst_takeaway: if thesis is blank, write 2-3 concise sentences summarising
   the investment case from all available data.

Rules:
- Only use information present in the ReportData. Never invent.
- If no segment breakdown exists, skip segment_insights (return empty list).
- If no recent events are mentioned, return empty list.
- Be specific, not generic. Use numbers where available.

Call the `emit_enrichment` function exactly once with the requested fields."""


def _enrich_tool_spec() -> dict:
    from core.schema_onepager import BusinessOverview, SegmentInsight, RecentDevelopment, RiskItem
    return {
        "type": "function",
        "function": {
            "name": "emit_enrichment",
            "description": "Return the enriched one-pager sections derived from the report data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "business_segments": {
                        "type": "array", "items": {"type": "string"},
                        "description": "List of business segment names.",
                    },
                    "segment_insights": {
                        "type": "array", "items": SegmentInsight.model_json_schema(),
                        "description": "Per-segment performance insights.",
                    },
                    "recent_developments": {
                        "type": "array", "items": RecentDevelopment.model_json_schema(),
                        "description": "Notable recent events.",
                    },
                    "key_risks": {
                        "type": "array", "items": RiskItem.model_json_schema(),
                        "description": "Key risks facing the company.",
                    },
                    "analyst_takeaway": {
                        "type": "string",
                        "description": "2-3 sentence investment takeaway.",
                    },
                },
                "required": ["business_segments", "segment_insights", "recent_developments", "key_risks", "analyst_takeaway"],
            },
        },
    }


CHAT_SYSTEM_PROMPT = """You are an equity research analyst with access to a company one-pager,
live market data, chat history, and web search. Be concise and proactive.

STYLE RULES:
- Keep answers SHORT. 2-4 sentences unless the user asks for detail. No essays.
- Never use em dashes (—) or special unicode punctuation. Use plain hyphens or commas.
- Flag data inconsistencies: if two numbers in the one-pager contradict each other
  (e.g. a YoY growth figure does not match the absolute values), call it out.
- When you apply a change, say what changed in one line.

CRITICAL: NEVER INVENT NUMBERS.
- Calculate projections ONLY from real data in the one-pager or external market data.
- If data is insufficient, say what is missing. Do not guess.
- Show your math when projecting: "H1 income / 2 = quarterly run-rate of X."

MODES:
1. INFORMATIONAL: answer from all sources. Cite where data comes from.
2. DIRECTIVE: confirm the change, call emit_updates. 1-2 lines.
3. ANALYTICAL: use all context. Propose changes via emit_updates.
4. PROJECTION: calculate from available data. If insufficient, explain what is needed.

ONE-PAGER CONTENT:
{context}

EXTERNAL MARKET DATA (from InsightSentry API):
{external_data}

{news_section}
{web_section}"""


def _chat_tool_spec() -> dict:
    from core.updater import SectionUpdate
    return {
        "type": "function",
        "function": {
            "name": "emit_updates",
            "description": "Propose one-pager section updates when the user requests changes or provides new information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "array",
                        "items": SectionUpdate.model_json_schema(),
                        "description": "List of proposed section updates (empty if no changes needed).",
                    },
                },
                "required": ["updates"],
            },
        },
    }


def chat_with_context(
    user_message: str,
    one_pager: OnePagerData,
    model: str | None = None,
    chat_history: list[dict] | None = None,
) -> dict:
    if model is None:
        model = CHAT_MODEL
    """Answer a user question + optionally propose one-pager updates.

    Includes chat history so the bot remembers previous turns.
    Also injects InsightSentry live data, news, and web search results.

    Returns: {"text": str, "updates": list[SectionUpdate] | None}
    """
    import json as _json
    from core.updater import SectionUpdate

    context = _one_pager_to_text(one_pager)

    # Fetch live market data
    company = one_pager.company_name or one_pager.snapshot.ticker or ""
    external_data = _fetch_external_context(company)

    # Fetch recent news if user mentions external events
    news_section = ""
    msg_lower = user_message.lower()
    news_triggers = ("fraud", "news", "scandal", "investigation", "article", "happened", "event", "case", "filing", "rbi", "sebi", "report")
    if any(w in msg_lower for w in news_triggers):
        news_section = _fetch_news_context(company)
        if news_section:
            news_section = f"RECENT NEWS ARTICLES:\n{news_section}"

    # Web search — always run for enriched responses
    web_section = ""
    try:
        web_section = _web_search(f"{company} {user_message[:100]}")
    except Exception:
        pass
    if web_section:
        web_section = f"WEB SEARCH RESULTS (use if relevant):\n{web_section}"

    system = CHAT_SYSTEM_PROMPT.format(
        context=context,
        external_data=external_data,
        news_section=news_section or "",
        web_section=web_section or "",
    )

    # Build messages with chat history so the bot remembers context
    messages = [{"role": "system", "content": system}]
    if chat_history:
        # Include last 10 messages as conversation context
        for msg in chat_history[-10:]:
            if msg.get("updates"):
                # For messages with updates, just include the text part
                messages.append({"role": msg["role"], "content": msg["content"]})
            else:
                messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    try:
        resp = litellm.completion(
            model=model,
            messages=messages,
            max_tokens=2048,
            temperature=0,
            tools=[_chat_tool_spec()],
            tool_choice="auto",
        )
    except Exception:
        # Fallback: plain completion without tools
        try:
            resp = litellm.completion(model=model, messages=messages, max_tokens=1024, temperature=0)
        except Exception as exc:
            return {"text": f"Error: {exc}", "updates": None}

    message = resp.choices[0].message
    text = (message.content or "").strip()

    # Parse tool calls for updates
    updates: list[SectionUpdate] = []
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        if call.function and call.function.name == "emit_updates":
            try:
                args = _json.loads(call.function.arguments)
                raw_list = args.get("updates", [])
                updates = [SectionUpdate.model_validate(item) for item in raw_list]
            except Exception:
                pass

    return {"text": text or "I analyzed your request.", "updates": updates if updates else None}


def _web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo and return formatted results."""
    try:
        from ddgs import DDGS
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return ""
        parts = []
        for r in results[:max_results]:
            title = r.get("title", "")
            body = (r.get("body") or "")[:300]
            href = r.get("href", "")
            parts.append(f"- {title}")
            parts.append(f"  {body}")
            parts.append(f"  URL: {href}")
        return "\n".join(parts)
    except ImportError:
        return ""
    except Exception:
        return ""


def _fetch_external_context(company_name: str) -> str:
    """Fetch InsightSentry live market data and format it as context text."""
    try:
        from core.insight import fetch_company_context, _api_get, _KNOWN_TICKERS, _normalise

        ctx = fetch_company_context(company_name)
        if not ctx or not any(ctx.values()):
            return "No external market data available."

        parts = ["Live market data for this company:"]
        if ctx.get("current_price"):
            parts.append(f"  Current Price: {ctx['current_price']}")
        if ctx.get("market_cap"):
            parts.append(f"  Market Cap: {ctx['market_cap']}")
        if ctx.get("revenue_ttm"):
            parts.append(f"  Revenue (TTM): {ctx['revenue_ttm']}")
        if ctx.get("net_income_ttm"):
            parts.append(f"  Net Income (TTM): {ctx['net_income_ttm']}")
        if ctx.get("pe_ratio"):
            parts.append(f"  PE Ratio: {ctx['pe_ratio']}")
        if ctx.get("sector"):
            parts.append(f"  Sector: {ctx['sector']}")
        if ctx.get("52w_high"):
            parts.append(f"  52W High: {ctx['52w_high']}")
        if ctx.get("52w_low"):
            parts.append(f"  52W Low: {ctx['52w_low']}")
        if ctx.get("dividend_yield"):
            parts.append(f"  Dividend Yield: {ctx['dividend_yield']}")
        if ctx.get("beta"):
            parts.append(f"  Beta: {ctx['beta']}")
        if ctx.get("employees"):
            parts.append(f"  Employees/Shares: {ctx['employees']}")
        if ctx.get("founded"):
            parts.append(f"  Founded: {ctx['founded']}")

        # Fetch recent OHLCV data for growth trend calculation
        key = _normalise(company_name)
        ticker_code = _KNOWN_TICKERS.get(key, "")
        if ticker_code:
            series = _api_get(f"/v3/symbols/{ticker_code}/series", {
                "bar_type": "day", "bar_interval": 1, "data_points": 30,
            })
            if series and series.get("data"):
                closes = [bar.get("close", 0) for bar in series["data"][-10:] if bar.get("close")]
                if len(closes) >= 2:
                    pct_changes = [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(1, len(closes))]
                    avg_change = sum(pct_changes) / len(pct_changes)
                    parts.append(f"  Recent price trend (avg daily change): {avg_change:+.2f}%")

        return "\n".join(parts)
    except Exception:
        return "External market data temporarily unavailable."


def _fetch_news_context(company_name: str) -> str:
    """Search InsightSentry newsfeed for recent articles about the company."""
    try:
        from core.insight import _api_get
        result = _api_get("/v3/newsfeed", {
            "keywords": company_name,
            "limit": 5,
        })
        if not result or not result.get("items"):
            return ""

        parts = []
        for item in result["items"][:5]:
            title = item.get("title", "Untitled")
            source = item.get("source", "")
            published = item.get("published", "")[:19]
            content = (item.get("content") or "")[:300]
            parts.append(f"- [{published}] {title} ({source})")
            if content:
                parts.append(f"  {content}")
        return "\n".join(parts)
    except Exception:
        return ""


def _one_pager_to_text(op: OnePagerData) -> str:
    """Serialize the one-pager to a compact text block for chatbot context."""
    lines = [f"Company: {op.company_name}", f"Report Date: {op.report_date}", ""]

    s = op.snapshot
    lines.append("## Company Snapshot")
    lines.append(f"Ticker: {s.ticker} | Sector: {s.sector} | Market Cap: {s.market_cap}")
    lines.append(f"Price: {s.current_price} | Revenue TTM: {s.revenue_ttm} | Net Income TTM: {s.net_income_ttm}")
    lines.append(f"Employees: {s.employees} | Founded: {s.founded} | HQ: {s.headquarters}")
    lines.append("")

    lines.append("## Business Overview")
    lines.append(op.business_overview.summary)
    if op.business_overview.business_segments:
        lines.append("Segments: " + ", ".join(op.business_overview.business_segments))
    lines.append("")

    if op.financial_highlights:
        lines.append("## Financial Highlights")
        for h in op.financial_highlights:
            yoy = f" (YoY: {h.yoy_change})" if h.yoy_change else ""
            lines.append(f"- {h.metric}: {h.value} [{h.period}]{yoy}")
        lines.append("")

    if op.growth_commentary:
        lines.append("## Growth & Margin Commentary")
        lines.append(op.growth_commentary)
        lines.append("")

    if op.segment_insights:
        lines.append("## Segment Insights")
        for si in op.segment_insights:
            lines.append(f"- {si.segment_name}: Revenue {si.revenue}, Growth {si.growth}. {si.commentary}")
        lines.append("")

    if op.recent_developments:
        lines.append("## Recent Developments")
        for rd in op.recent_developments:
            lines.append(f"- [{rd.date}] {rd.headline} | Impact: {rd.impact}")
        lines.append("")

    if op.key_risks:
        lines.append("## Key Risks")
        for r in op.key_risks:
            lines.append(f"- [{r.severity}] {r.category}: {r.description}")
        lines.append("")

    if op.analyst_takeaway:
        lines.append("## Analyst Takeaway")
        lines.append(op.analyst_takeaway)
        lines.append("")

    if op.sources_used:
        lines.append("## Sources")
        for src in op.sources_used:
            lines.append(f"- {src}")

    return "\n".join(lines)
