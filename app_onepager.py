"""
Streamlit UI — Task 2: One-Pager + Sidebar Chatbot + Surgical Updates.
========================================================================

Core flow:
  1. Upload a company financial document (PDF / CSV / TXT).
  2. DeepSeek extracts structured data into a one-pager (OnePagerData).
  3. InsightSentry enriches with external context (mock).
  4. A clean single-page HTML one-pager is rendered in the main area.
  5. Sidebar chatbot answers questions grounded in the one-pager only.
  6. Surgical update: paste new info → LLM proposes selective section changes
     with before/after/rationale/evidence → user accepts/rejects → audit trail.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from core.ingest import ingest
from core.schema_onepager import OnePagerData, CORE_ONEPAGER_FIELDS
from core.extractor_onepager import (
    extract_onepager,
    chat_with_context,
    DEFAULT_ONEPAGER_MODEL,
)
from core.charts import render_all as render_charts
from core.insight import fetch_company_context
from core.updater import propose_updates, build_audit_trail, SectionUpdate
from core.audit_db import (
    create_session,
    log_surgical_update,
    log_chat_interaction_batch,
    get_entries_for_session,
    get_stats_for_session,
    list_sessions,
    update_entry_accepted,
)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _blank(v: str) -> str:
    return v if (v and str(v).strip()) else "—"


_env.filters["blank"] = _blank

st.set_page_config(page_title="AI One-Pager Research", layout="wide")

# ── Session state init ──────────────────────────────────────────────────────
if "onepager" not in st.session_state:
    st.session_state.onepager = None
if "onepager_html" not in st.session_state:
    st.session_state.onepager_html = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "proposed_updates" not in st.session_state:
    st.session_state.proposed_updates: list[SectionUpdate] = []
if "update_mode" not in st.session_state:
    st.session_state.update_mode = False
if "model" not in st.session_state:
    st.session_state.model = DEFAULT_ONEPAGER_MODEL
if "audit_session_id" not in st.session_state:
    st.session_state.audit_session_id: str = ""

# ── Styling ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
#MainMenu, footer, header { visibility: hidden; }
html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }
.stApp { background: #F5F5F5; }

/* header */
.hdr { border-bottom: 3px solid #DC2626; padding: 6px 0 14px; margin-bottom: 22px; }
.hdr h1 { font-size: 24px; font-weight: 700; margin: 0; color: #111827; letter-spacing: -0.01em; }
.hdr p  { color: #6B7280; margin: 4px 0 0; font-size: 13px; }

/* labels */
label, .stTextInput label, .stFileUploader label,
[data-testid="stWidgetLabel"] p {
    color: #374151 !important; font-weight: 600 !important; font-size: 13px !important;
}

/* inputs */
.stTextInput input {
    background: #FFFFFF !important; color: #111827 !important;
    border: 1px solid #E2E8F0 !important; border-radius: 8px !important;
}
[data-testid="stFileUploaderDropzone"] {
    background: #FFFFFF !important; border: 1px dashed #CBD5E1 !important; border-radius: 8px !important;
}
[data-testid="stFileUploaderDropzone"] * { color: #475569 !important; }
.stTextArea textarea {
    background: #FFFFFF !important; color: #111827 !important;
    border: 1px solid #E2E8F0 !important; border-radius: 8px !important;
}

/* buttons */
.stButton button[kind="primary"] {
    background: #DC2626 !important; color: #fff !important; border: none !important;
    font-weight: 600 !important; border-radius: 8px !important; padding: 8px 22px !important;
}
.stButton button[kind="primary"]:hover { background: #B91C1C !important; }
.stButton button[kind="secondary"] {
    background: #F1F5F9 !important; color: #334155 !important; border: 1px solid #E2E8F0 !important;
    font-weight: 600 !important; border-radius: 8px !important; padding: 8px 22px !important;
}

/* chat in sidebar */
[data-testid="stSidebar"] {
    background: #FAFBFC !important;
    border-right: 1px solid #E2E8F0 !important;
}
[data-testid="stSidebar"] [data-testid="stChatMessage"] {
    background: transparent !important;
}

/* status */
[data-testid="stStatusWidget"], .stSpinner > div { color: #374151 !important; }

/* update cards */
.update-card {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 14px;
}
.update-card.accepted {
    border-color: #16a34a; background: #f0fdf4;
}
.update-card h4 { margin: 0 0 8px; font-size: 14px; color: #111827; }
.update-card .meta { font-size: 11px; color: #64748b; margin-bottom: 10px; }
.update-card .before { background: #FEF2F2; padding: 8px 12px; border-radius: 6px; font-size: 12px; color: #991B1B; margin-bottom: 8px; }
.update-card .after { background: #F0FDF4; padding: 8px 12px; border-radius: 6px; font-size: 12px; color: #166534; margin-bottom: 8px; }
.update-card .reason { font-size: 12px; color: #475569; }
.update-card .evidence { font-size: 11px; color: #64748b; font-style: italic; margin-top: 4px; }

.active-icon { color: #16a34a; font-weight: 700; font-size: 13px; }
</style>

<div class="hdr">
  <h1>AI Company One-Pager</h1>
  <p>Upload a financial document → get a structured one-page company summary. Chat with the sidebar bot. Paste new info for surgical updates.</p>
</div>
""", unsafe_allow_html=True)

# ── Sidebar: chat ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 💬 Research Chat")
    st.caption("Ask questions about the current one-pager.")

    # Chat history display
    chat_container = st.container(height=420)
    with chat_container:
        if not st.session_state.chat_history:
            st.caption("Upload a document and generate a one-pager to start chatting.")
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

    # Chat input
    if st.session_state.onepager is not None:
        if user_msg := st.chat_input("Ask about the company...", key="chat_input"):
            st.session_state.chat_history.append({"role": "user", "content": user_msg})
            with st.spinner("Thinking..."):
                answer = chat_with_context(
                    user_msg,
                    st.session_state.onepager,
                    model=st.session_state.model,
                )
            st.session_state.chat_history.append({"role": "assistant", "content": answer})

            # Log to SQLite audit trail
            if st.session_state.audit_session_id:
                log_chat_interaction_batch(
                    session_id=st.session_state.audit_session_id,
                    user_msg=user_msg,
                    assistant_msg=answer,
                    context_onepager=st.session_state.onepager,
                    model_name=st.session_state.model,
                )
            st.rerun()
    else:
        st.chat_input("Ask about the company...", disabled=True)

    if st.session_state.chat_history and st.button("Clear chat", use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()

# ── Main area: inputs ───────────────────────────────────────────────────────
col1, col2 = st.columns([2, 1])
with col1:
    choice = st.selectbox(
        "Company name",
        ["ICICI Bank", "JSW Energy", "LTTS", "POCL", "Eternal (Zomato)", "Other…"],
        index=0,
    )
    if choice == "Other…":
        company_name = st.text_input("Enter company name", placeholder="e.g. Tata Motors")
    else:
        company_name = choice

with col2:
    st.caption("---")
    with st.expander("Model settings"):
        model = st.text_input(
            "LLM model",
            value=st.session_state.model,
            key="model_input",
            help="Any LiteLLM model id.",
        )
        st.session_state.model = model

uploaded = st.file_uploader(
    "Financial document", type=["pdf", "csv", "txt"],
    help="Earnings release, investor presentation, or financials as PDF / CSV / TXT.",
)

# ── Generate button ─────────────────────────────────────────────────────────
gen_col1, gen_col2 = st.columns([1, 4])
with gen_col1:
    go = st.button("Generate One-Pager", type="primary", disabled=(uploaded is None))

if go and uploaded:
    try:
        progress = st.progress(0, text="Starting")
        with st.status("Generating your one-pager", expanded=True) as status:
            st.write("📄 Reading the document.")
            progress.progress(15, text="Reading document")
            doc = ingest(uploaded.name, uploaded.getvalue())
            st.write(f"Read as {doc.fmt.upper()}.")

            st.write(f"🤖 Extracting data with {st.session_state.model}. This may take 20–60 seconds.")
            progress.progress(40, text="Extracting with AI, please wait")
            onepager = extract_onepager(doc, company_name, model=st.session_state.model)
            st.write(f"Extraction returned {len(onepager.financial_highlights)} financial highlights, "
                     f"{len(onepager.segment_insights)} segments, {len(onepager.key_risks)} risks.")

            progress.progress(70, text="Enriching with external data")
            ctx = fetch_company_context(company_name)
            if ctx.get("sector") and not onepager.snapshot.sector:
                onepager.snapshot.sector = ctx["sector"]
            if ctx.get("market_cap") and not onepager.snapshot.market_cap:
                onepager.snapshot.market_cap = ctx["market_cap"]
            if ctx.get("current_price") and not onepager.snapshot.current_price:
                onepager.snapshot.current_price = ctx["current_price"]
            if ctx.get("ticker") and not onepager.snapshot.ticker:
                onepager.snapshot.ticker = ctx["ticker"]
            st.write("Enrichment applied.")

            progress.progress(85, text="Rendering")
            charts = render_charts(onepager.charts)
            template = _env.get_template("onepager.html")
            html = template.render(d=onepager, charts=charts)

            progress.progress(100, text="Done")
            status.update(label="One-pager ready.", state="complete", expanded=False)

        st.session_state.onepager = onepager
        st.session_state.onepager_html = html
        st.session_state.proposed_updates = []
        st.session_state.update_mode = False

        # Create SQLite audit session
        st.session_state.audit_session_id = create_session(
            company_name=onepager.company_name or company_name,
            model_name=st.session_state.model,
            doc_filename=uploaded.name,
        )

        # Auto-send a welcome message in chat
        st.session_state.chat_history = [{
            "role": "assistant",
            "content": f"I've analyzed the document for **{onepager.company_name or company_name}**. "
                       f"You can ask me questions about its financials, business segments, risks, "
                       f"or any other section of the one-pager below."
                       f"\n\nUse the **Surgical Update** section below to paste new information "
                       f"and see what sections need updating."
        }]

        missing = [f for f in CORE_ONEPAGER_FIELDS if not getattr(onepager, f)]
        if missing:
            st.info("Some sections came back empty: " + ", ".join(missing))

        st.rerun()

    except Exception as exc:
        st.error(f"Generation failed: {exc}")
        st.exception(exc)

# ── Display one-pager ───────────────────────────────────────────────────────
if st.session_state.onepager_html:
    st.divider()
    st.markdown("### 📋 Company One-Pager")

    # Toggle between view modes
    view_mode = st.radio("View mode", ["Rendered", "Source HTML"], horizontal=True, label_visibility="collapsed")

    if view_mode == "Rendered":
        components.html(st.session_state.onepager_html, height=1200, scrolling=True)
    else:
        st.code(st.session_state.onepager_html, language="html")

    # Download button
    safe_name = (st.session_state.onepager.company_name or "report").replace(" ", "_")
    st.download_button(
        "⬇ Download HTML",
        data=st.session_state.onepager_html,
        file_name=f"{safe_name}_onepager.html",
        mime="text/html",
    )

    # ── Surgical Update section ──────────────────────────────────────────
    st.divider()
    st.markdown("### 🔁 Surgical Update")
    st.caption("Paste new information (filing update, press release, earnings call notes) to see which sections of the one-pager should change.")

    new_info = st.text_area(
        "New information",
        placeholder="Paste a press release excerpt, updated financials, or any new company information here...",
        height=120,
        key="new_info_area",
    )

    update_col1, update_col2 = st.columns([1, 4])
    with update_col1:
        analyze = st.button("Analyze for Updates", type="secondary", disabled=not new_info.strip())

    if analyze and new_info.strip():
        with st.spinner("Comparing with existing one-pager..."):
            updates = propose_updates(
                st.session_state.onepager,
                new_info.strip(),
                model=st.session_state.model,
            )
            st.session_state.proposed_updates = updates
            st.session_state.update_mode = True

            # Log each proposed update to SQLite
            if st.session_state.audit_session_id:
                for u in updates:
                    log_surgical_update(
                        session_id=st.session_state.audit_session_id,
                        section_name=u.section_name,
                        field_path=u.field_path,
                        before_text=u.before,
                        after_text=u.after,
                        rationale=u.rationale,
                        evidence=u.evidence,
                        accepted=u.accepted,
                        user_prompt=new_info.strip(),
                        model_name=st.session_state.model,
                        model_reasoning=u.model_reasoning,
                    )
            st.rerun()

    # Display proposed updates
    if st.session_state.update_mode and st.session_state.proposed_updates:
        st.markdown(f"**{len(st.session_state.proposed_updates)} proposed update(s).** Review and accept/reject below.")
        updates = st.session_state.proposed_updates

        all_accepted = all(u.accepted for u in updates)

        for i, u in enumerate(updates):
            status_badge = "✅ Accepted" if u.accepted else "⏳ Pending"
            card_class = "accepted" if u.accepted else ""
            with st.container():
                st.markdown(f"""<div class="update-card {card_class}">
                <h4>{i+1}. {u.section_name} <span style="font-size:11px;color:#64748b;">[{status_badge}]</span></h4>
                <div class="meta">Field: <code>{u.field_path}</code> &nbsp;|&nbsp; {u.timestamp[:19]}</div>
                <div class="before"><strong>Before:</strong> {u.before or '—'}</div>
                <div class="after"><strong>After:</strong> {u.after or '—'}</div>
                <div class="reason"><strong>Rationale:</strong> {u.rationale}</div>
                <div class="evidence"><strong>Evidence:</strong> "{u.evidence}"</div>
                </div>""", unsafe_allow_html=True)

                c1, c2, _ = st.columns([1, 1, 6])
                with c1:
                    if not u.accepted and st.button("✅ Accept", key=f"accept_{i}"):
                        st.session_state.proposed_updates[i].accepted = True
                        # Track the time when accepted
                        st.session_state.proposed_updates[i].timestamp = datetime.now(timezone.utc).isoformat()
                        st.rerun()
                with c2:
                    if u.accepted and st.button("↩ Reject", key=f"reject_{i}"):
                        st.session_state.proposed_updates[i].accepted = False
                        st.rerun()

        st.divider()

        # Apply accepted updates
        if any(u.accepted for u in updates):
            if st.button("⚡ Apply Accepted Changes to One-Pager", type="primary"):
                op = st.session_state.onepager
                for u in updates:
                    if not u.accepted:
                        continue
                    _apply_update(op, u)

                # Re-render
                charts = render_charts(op.charts)
                template = _env.get_template("onepager.html")
                st.session_state.onepager_html = template.render(d=op, charts=charts)
                st.session_state.proposed_updates = []
                st.session_state.update_mode = False

                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": f"Applied {sum(1 for u in updates if u.accepted)} accepted update(s) to the one-pager. The changes are now reflected in the view and chat context."
                })
                st.rerun()

        # Audit trail
        if updates:
            with st.expander("📜 Audit Trail"):
                trail = build_audit_trail(updates, only_accepted=False)
                st.markdown(trail)

    elif st.session_state.update_mode and not st.session_state.proposed_updates:
        st.info("No updates needed. The new information does not contain any material changes.")

elif not st.session_state.onepager_html:
    st.info("Upload a financial document and click **Generate One-Pager** to get started.")


# ── SQLite Audit Trail Viewer ────────────────────────────────────────────────
st.divider()
with st.expander("📋 Full Audit Trail (SQLite) | session history & debug logs", expanded=False):
    sessions = list_sessions(limit=20)
    if not sessions:
        st.caption("No audit sessions yet. Generate a one-pager to start logging.")
    else:
        st.markdown("### Past Sessions")
        session_options = {
            f"{s['company_name']} | {s['created_at'][:19]} ({s['doc_filename']})": s["id"]
            for s in sessions
        }
        selected_label = st.selectbox(
            "Select a session to inspect",
            list(session_options.keys()),
            key="audit_session_select",
        )
        selected_id = session_options[selected_label]

        # Session stats
        stats = get_stats_for_session(selected_id)
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Surgical Updates", stats["surgical_updates"])
        col_b.metric("Chat Interactions", stats["chat_interactions"])
        col_c.metric("Accepted Updates", stats["accepted_updates"])

        st.divider()

        # Filter by event type
        filter_type = st.radio(
            "Filter by event type",
            ["All", "Surgical Updates", "Chat Interactions"],
            horizontal=True,
            key="audit_filter",
        )
        event_filter = None
        if filter_type == "Surgical Updates":
            event_filter = "surgical_update"
        elif filter_type == "Chat Interactions":
            event_filter = "chat_interaction"

        entries = get_entries_for_session(selected_id, event_type=event_filter)
        if not entries:
            st.caption("No entries for this filter.")
        else:
            for e in entries:
                etype = e["event_type"]
                if etype == "chat_interaction":
                    with st.container():
                        st.markdown(f"""
                        <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:12px;margin-bottom:10px;">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                            <span style="font-weight:700;font-size:13px;color:#2563EB;">💬 Chat Q&A</span>
                            <span style="font-size:10px;color:#94A3B8;">{e['timestamp'][:19]}</span>
                        </div>
                        <div style="margin-bottom:6px;">
                            <span style="font-size:10px;font-weight:600;color:#64748B;">PROMPT</span>
                            <p style="font-size:12px;color:#334155;margin:2px 0;background:#fff;padding:8px;border-radius:4px;">{e['user_prompt']}</p>
                        </div>
                        <div>
                            <span style="font-size:10px;font-weight:600;color:#16A34A;">RESPONSE</span>
                            <p style="font-size:12px;color:#334155;margin:2px 0;background:#fff;padding:8px;border-radius:4px;">{e['model_response']}</p>
                        </div>
                        </div>
                        """, unsafe_allow_html=True)

                        if e.get("context_snapshot"):
                            with st.expander("📸 One-pager context snapshot at time of chat"):
                                st.code(e["context_snapshot"][:5000], language="json")

                elif etype == "surgical_update":
                    accepted_badge = "✅ Accepted" if e["accepted"] else "⏳ Pending"
                    card_color = "#F0FDF4" if e["accepted"] else "#FFF7ED"
                    border_color = "#16A34A" if e["accepted"] else "#F59E0B"
                    with st.container():
                        st.markdown(f"""
                        <div style="background:{card_color};border:1px solid {border_color};border-radius:8px;padding:14px;margin-bottom:10px;">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                            <span style="font-weight:700;font-size:13px;color:#111827;">🔁 {e['section_name']}</span>
                            <span style="font-size:10px;color:#64748B;">{accepted_badge} &nbsp;|&nbsp; {e['timestamp'][:19]}</span>
                        </div>
                        <div style="font-size:11px;color:#64748B;margin-bottom:6px;">Field: <code>{e['field_path']}</code> &nbsp;|&nbsp; Model: {e.get('model_name', '-')}</div>
                        <div style="background:#FEF2F2;padding:6px 10px;border-radius:4px;margin-bottom:4px;font-size:11px;">
                            <strong style="color:#991B1B;">BEFORE:</strong> <span style="color:#B91C1C;">{e['before_text'] or '—'}</span>
                        </div>
                        <div style="background:#F0FDF4;padding:6px 10px;border-radius:4px;margin-bottom:4px;font-size:11px;">
                            <strong style="color:#166534;">AFTER:</strong> <span style="color:#15803D;">{e['after_text'] or '—'}</span>
                        </div>
                        <div style="font-size:11px;color:#475569;margin:4px 0;">📝 <strong>Rationale:</strong> {e['rationale']}</div>
                        <div style="font-size:10px;color:#94A3B8;font-style:italic;">📎 <strong>Evidence:</strong> "{e['evidence']}"</div>
                        </div>
                        """, unsafe_allow_html=True)

                        if e.get("user_prompt"):
                            with st.expander("📥 Triggering prompt (new info pasted)"):
                                st.text(e["user_prompt"][:3000])

                        if e.get("model_reasoning"):
                            with st.expander("🧠 Model reasoning steps (debug)"):
                                st.text(e["model_reasoning"][:5000])


# ── Helper: apply a single SectionUpdate to OnePagerData ────────────────────
def _apply_update(op: OnePagerData, u: SectionUpdate) -> None:
    """Naively apply a section update by setting the field from 'after' text.
    Handles top-level string fields and list fields where 'after' is JSON."""
    path = u.field_path

    # Top-level string / single-value fields
    if path in ("company_name", "report_date", "growth_commentary", "analyst_takeaway"):
        setattr(op, path, u.after)
        return

    # Snapshot sub-fields: snapshot.xxx
    if path.startswith("snapshot."):
        attr = path.split(".")[1]
        if attr in op.snapshot.model_fields:
            setattr(op.snapshot, attr, u.after)
        return

    # Business overview sub-fields
    if path.startswith("business_overview."):
        attr = path.split(".")[1]
        if attr in op.business_overview.model_fields:
            setattr(op.business_overview, attr, u.after)
        return

    # List fields: try to parse 'after' as JSON list and replace
    list_fields = {
        "financial_highlights": "FinancialHighlight",
        "segment_insights": "SegmentInsight",
        "recent_developments": "RecentDevelopment",
        "key_risks": "RiskItem",
        "sources_used": None,
    }

    if path in list_fields:
        try:
            parsed = json.loads(u.after) if u.after.strip() else []
            if path == "sources_used":
                setattr(op, path, parsed)
            elif path == "financial_highlights":
                from core.schema_onepager import FinancialHighlight
                setattr(op, path, [FinancialHighlight.model_validate(item) for item in parsed])
            elif path == "segment_insights":
                from core.schema_onepager import SegmentInsight
                setattr(op, path, [SegmentInsight.model_validate(item) for item in parsed])
            elif path == "recent_developments":
                from core.schema_onepager import RecentDevelopment
                setattr(op, path, [RecentDevelopment.model_validate(item) for item in parsed])
            elif path == "key_risks":
                from core.schema_onepager import RiskItem
                setattr(op, path, [RiskItem.model_validate(item) for item in parsed])
        except Exception:
            pass
