"""
Extraction agent — context document -> structured ReportData, using ANY LLM.
============================================================================

This module is provider-agnostic. It talks to the model through LiteLLM, which
routes a single OpenAI-style call to whichever provider the chosen model belongs
to (Anthropic, OpenAI, Google Gemini, Mistral, Groq, Ollama, Azure, and many
more). You pick the model with the EXTRACTION_MODEL setting and supply that
provider's standard API key in the environment; no code changes are needed to
switch providers.

Examples of EXTRACTION_MODEL values:
    anthropic/claude-sonnet-4-6      (needs ANTHROPIC_API_KEY)
    gpt-4o                           (needs OPENAI_API_KEY)
    gemini/gemini-1.5-pro            (needs GEMINI_API_KEY)
    mistral/mistral-large-latest     (needs MISTRAL_API_KEY)
    ollama/llama3                    (local, no key)

Strategy:
  1. If the document yielded usable text (text PDF / CSV / TXT), send the text.
  2. Otherwise (scanned / image-only PDF), attach the PDF as a file so vision
     capable models can read the rendered pages. Models without file support
     will surface a clear error.

The model is asked to return data through a single function/tool call whose
parameters are the ReportData JSON schema, so we get a validated object back
instead of parsing prose. A plain-JSON fallback is kept for models that reply
with text instead of a tool call.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential

from core.ingest import IngestedDoc
from core.schema import ReportData

# Default model. Override via the EXTRACTION_MODEL environment variable to use
# any provider/model you have a key for.
DEFAULT_MODEL = os.getenv("EXTRACTION_MODEL", "deepseek/deepseek-chat")

# How much document text we send. Long decks are trimmed to a safe size.
MAX_TEXT_CHARS = 60_000

litellm.drop_params = True  # silently drop params a given provider doesn't support

SYSTEM_PROMPT = """You are an equity research analyst's assistant. You read a company's \
financial document (earnings press release, investor presentation, results, or a CSV/TXT \
of financials) and produce the structured data for a Geojit-style equity research report.

Rules:
- Extract ONLY what the document supports. NEVER invent numbers. If a value is absent,
  leave the field blank ("" / empty list / null) — do not guess.
- Prefer the most recent reported quarter/year and include historical + estimate columns
  when the document provides them.
- For tables, keep every row's `cells` aligned to `columns` (same length and order);
  use "" for a cell with no value.
- Write `description` as 2-4 neutral sentences. Write `highlights` as crisp result bullets
  (revenue, growth %, margins, PAT, segment notes). Write `key_highlights` as analytical
  outlook/driver/risk bullets.
- Populate `company_data` with whatever market data exists (Market Cap, 52W High/Low,
  Enterprise Value, Shares Outstanding, Free Float %, Dividend Yield, Beta, Face Value...).
- Build `financials` as one table per statement you find (Profit & Loss, Balance Sheet,
  Cash Flow, Key Ratios). Use the document's own year columns.
- Always provide at least two `charts` when the numbers allow it: a revenue-trend chart
  ('bar') and a margin or profit chart ('line'), with x labels matching the financial years.
- `rating`, `target_price`, `current_price`, `upside` only if the document states them.

Call the `emit_report` function exactly once with the structured data."""


def _tool_spec() -> dict:
    """OpenAI-style function spec; LiteLLM translates it to each provider."""
    return {
        "type": "function",
        "function": {
            "name": "emit_report",
            "description": "Return the structured research-report data extracted from the document.",
            "parameters": ReportData.model_json_schema(),
        },
    }


def _user_content(doc: IngestedDoc, company_name: str):
    instruction = (
        f"Company name (as provided by the user): {company_name or 'unknown — infer from the document'}.\n\n"
        "Extract the research-report data from the document below and call `emit_report`."
    )

    # Scanned/image PDF with no usable text -> attach the file for vision models.
    if doc.fmt == "pdf" and not doc.has_usable_text and doc.pdf_bytes:
        b64 = base64.standard_b64encode(doc.pdf_bytes).decode("utf-8")
        return [
            {"type": "text", "text": instruction},
            {"type": "file", "file": {"file_data": f"data:application/pdf;base64,{b64}"}},
        ]

    text = doc.text[:MAX_TEXT_CHARS]
    truncated = "\n\n[... document truncated ...]" if len(doc.text) > MAX_TEXT_CHARS else ""
    return f"{instruction}\n\n=== DOCUMENT ({doc.filename}) ===\n{text}{truncated}"


def _coerce(raw: dict) -> ReportData:
    """Validate/normalise the tool arguments into a ReportData (lenient)."""
    try:
        return ReportData.model_validate(raw)
    except Exception:
        clean: dict[str, Any] = {k: raw[k] for k in ReportData.model_fields if k in raw}
        try:
            return ReportData.model_validate(clean)
        except Exception:
            return ReportData(company_name=str(raw.get("company_name", "")))


def _parse_message(message) -> ReportData | None:
    """Pull ReportData out of a tool call, or fall back to JSON in the text."""
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        if call.function and call.function.name == "emit_report":
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
def extract_report(doc: IngestedDoc, company_name: str, model: str = DEFAULT_MODEL) -> ReportData:
    """Run extraction with the configured model and return a ReportData."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_content(doc, company_name)},
    ]

    kwargs = dict(model=model, messages=messages, max_tokens=8192, temperature=0)
    # Try to force the function call; some providers ignore tool_choice, so fall
    # back to letting the model choose, then to plain-JSON parsing.
    try:
        resp = litellm.completion(
            tools=[_tool_spec()],
            tool_choice={"type": "function", "function": {"name": "emit_report"}},
            **kwargs,
        )
    except Exception:
        resp = litellm.completion(tools=[_tool_spec()], tool_choice="auto", **kwargs)

    data = _parse_message(resp.choices[0].message)
    if data is None:
        data = ReportData(company_name=company_name)
    if company_name and not data.company_name:
        data.company_name = company_name
    return data


def missing_keys_for(model: str = DEFAULT_MODEL) -> list[str]:
    """Return the API-key env vars the chosen model needs but that are unset."""
    try:
        return litellm.validate_environment(model).get("missing_keys", [])
    except Exception:
        return []
