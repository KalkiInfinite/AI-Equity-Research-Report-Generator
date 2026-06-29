"""
Surgical update module — propose selective section changes when new info arrives.
==================================================================================

Given an existing OnePagerData and a user-pasted chunk of new information (filing /
press release / news), the LLM compares the two and returns only the sections that
need updating, with before/after/rationale/evidence for each.

The user can accept/reject individual updates, and accepted changes produce an
audit trail.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import litellm
from pydantic import BaseModel, Field

from core.schema_onepager import OnePagerData, CompanySnapshot, BusinessOverview, FinancialHighlight

DEFAULT_UPDATER_MODEL = os.getenv("EXTRACTION_MODEL", "deepseek/deepseek-chat")
litellm.drop_params = True


class SectionUpdate(BaseModel):
    section_name: str = Field(description="Which section of the one-pager is being updated, e.g. 'Financial Highlights', 'Business Overview'")
    field_path: str = Field(description="Dot-separated path to the field, e.g. 'financial_highlights', 'growth_commentary', 'snapshot.market_cap'")
    before: str = Field(description="Previous value before the update")
    after: str = Field(description="New value proposed based on the fresh information")
    rationale: str = Field(description="Why this change is warranted")
    evidence: str = Field(description="Quote or reference from the new information supporting the change")
    model_reasoning: str = Field(default="", description="Step-by-step reasoning the model used to decide this update, for debugging")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    accepted: bool = Field(default=False, description="Whether the user accepted this update")


UPDATER_SYSTEM_PROMPT = """You are an equity research assistant that compares an existing company \
one-pager summary with freshly arrived information (filing, press release, news article, etc.).

Your job:
1. Read the existing one-pager and the new information carefully.
2. Identify which sections (if any) need to be updated based on the new information.
3. For each needed update, produce a SectionUpdate with:
   - section_name: human-readable section name (e.g. "Financial Highlights", "Growth Commentary")
   - field_path: dot-separated path (e.g. "financial_highlights", "growth_commentary", "snapshot.market_cap")
   - before: the EXACT current value in the one-pager (copy-paste, do not paraphrase)
   - after: the revised value incorporating the new information
   - rationale: why the change is needed (1-2 sentences)
   - evidence: quote or reference from the new information that supports the change
   - model_reasoning: a step-by-step breakdown of how you arrived at this decision.
     Include: (a) what specific data changed, (b) which numbers you compared,
     (c) why the old value is no longer accurate, (d) how you constructed the new value.
     This is used for debugging and audit, so be thorough.

Rules:
- ONLY propose changes that are directly supported by the new information.
- If the new information confirms the existing data, do NOT propose a change.
- If the new information has nothing new, return an empty list.
- Keep the 'before' text EXACT as it appears in the one-pager — this is critical
  for automated patching.
- The 'after' text should replace 'before' entirely for that field.
- For lists (e.g. financial_highlights, key_risks), the field_path should be
  the list field name and 'before'/'after' should be the JSON representation
  of the full list (old and new).

Call the `emit_updates` function exactly once with the list of SectionUpdate objects."""


def _tool_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "emit_updates",
            "description": "Return the list of proposed section updates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "array",
                        "items": SectionUpdate.model_json_schema(),
                        "description": "List of proposed section updates (empty if nothing to change).",
                    },
                },
                "required": ["updates"],
            },
        },
    }


def _one_pager_to_text(op: OnePagerData) -> str:
    """Compact text representation for the LLM to read. Replicates the format
    used in extractor_onepager._one_pager_to_text."""
    from core.extractor_onepager import _one_pager_to_text as to_text
    return to_text(op)


def _parse_updates(message) -> list[SectionUpdate]:
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        if call.function and call.function.name == "emit_updates":
            try:
                args = json.loads(call.function.arguments)
                raw_list = args.get("updates", [])
                return [SectionUpdate.model_validate(item) for item in raw_list]
            except Exception:
                continue

    # Fall back to JSON in content.
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            try:
                raw_list = json.loads(match.group())
                return [SectionUpdate.model_validate(item) for item in raw_list]
            except Exception:
                return []
    return []


def propose_updates(one_pager: OnePagerData, new_info: str, model: str = DEFAULT_UPDATER_MODEL) -> list[SectionUpdate]:
    """
    Compare the existing one_pager with new_info and return a list of
    SectionUpdate proposals.
    """
    existing_text = _one_pager_to_text(one_pager)
    user_msg = (
        "=== EXISTING ONE-PAGER ===\n\n"
        f"{existing_text}\n\n"
        "=== NEW INFORMATION ===\n\n"
        f"{new_info}\n\n"
        "Compare the two and call `emit_updates` with any necessary section changes."
    )

    messages = [
        {"role": "system", "content": UPDATER_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    kwargs = dict(model=model, messages=messages, max_tokens=8192, temperature=0)
    try:
        resp = litellm.completion(
            tools=[_tool_spec()],
            tool_choice={"type": "function", "function": {"name": "emit_updates"}},
            **kwargs,
        )
    except Exception:
        resp = litellm.completion(tools=[_tool_spec()], tool_choice="auto", **kwargs)

    return _parse_updates(resp.choices[0].message)


def build_audit_trail(updates: list[SectionUpdate], only_accepted: bool = True) -> str:
    """Generate a human-readable audit trail from a list of SectionUpdate objects."""
    items = [u for u in updates if (not only_accepted or u.accepted)]
    if not items:
        return "No updates to show."

    lines = ["## Update Audit Trail\n"]
    for i, u in enumerate(items, 1):
        status = "ACCEPTED" if u.accepted else "PENDING"
        lines.append(f"### {i}. {u.section_name} [{status}]")
        lines.append(f"- **Field:** `{u.field_path}`")
        lines.append(f"- **Timestamp:** {u.timestamp}")
        lines.append(f"- **Rationale:** {u.rationale}")
        lines.append(f"- **Evidence:** {u.evidence}")
        lines.append(f"- **Before:** {u.before}")
        lines.append(f"- **After:** {u.after}")
        if u.model_reasoning:
            lines.append(f"- **Model reasoning steps:** {u.model_reasoning}")
        lines.append("")
    return "\n".join(lines)
