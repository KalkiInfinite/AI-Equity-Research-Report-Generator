"""
Unified Streamlit app — Task 1 (PDF Report) + Task 2 (One-Pager & Chat).

Single extraction call populates ReportData, then OnePagerData is derived
from it (no second LLM call). Two tabs:
  Tab 1: 📄 Research Report — Geojit-style PDF download
  Tab 2: 📋 One-Pager & Chat — HTML one-pager + sidebar chatbot + surgical updates
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
from core.extractor import extract_report, missing_keys_for, DEFAULT_MODEL
from core.schema import ReportData, CORE_FIELDS
from core.schema_onepager import (
    OnePagerData, CORE_ONEPAGER_FIELDS, from_reportdata,
)
from core.extractor_onepager import (
    enrich_onepager,
    chat_with_context,
    DEFAULT_ONEPAGER_MODEL,
)
from core.charts import render_all as render_charts
from core.insight import fetch_company_context
from core.report import render_pdf, render_onepager_html, render_onepager_pdf
from core.updater import propose_updates, build_audit_trail, SectionUpdate
from core.audit_db import (
    create_session,
    log_surgical_update,
    log_chat_interaction_batch,
    get_entries_for_session,
    get_stats_for_session,
    list_sessions,
)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _blank(v: str) -> str:
    return v if (v and str(v).strip()) else "—"


st.set_page_config(page_title="AI Equity Research", layout="wide")

# ── Session state init ──────────────────────────────────────────────────────
if "report_data" not in st.session_state:
    st.session_state.report_data: ReportData | None = None
if "report_pdf" not in st.session_state:
    st.session_state.report_pdf: bytes = b""
if "onepager" not in st.session_state:
    st.session_state.onepager: OnePagerData | None = None
if "onepager_html" not in st.session_state:
    st.session_state.onepager_html = ""
if "onepager_pdf" not in st.session_state:
    st.session_state.onepager_pdf: bytes = b""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "proposed_updates" not in st.session_state:
    st.session_state.proposed_updates: list[SectionUpdate] = []
if "update_mode" not in st.session_state:
    st.session_state.update_mode = False
if "model" not in st.session_state:
    st.session_state.model = DEFAULT_MODEL
if "chat_model" not in st.session_state:
    from core.extractor_onepager import CHAT_MODEL as _CHAT_MODEL
    st.session_state.chat_model = _CHAT_MODEL
if "audit_session_id" not in st.session_state:
    st.session_state.audit_session_id: str = ""
if "generated_company" not in st.session_state:
    st.session_state.generated_company = ""
if "onepager_version" not in st.session_state:
    st.session_state.onepager_version = 0

# ── Helper ──────────────────────────────────────────────────────────────────
def _apply_update(op: OnePagerData, u: SectionUpdate) -> bool:
    """Returns True if applied, False if failed."""
    path = u.field_path

    if path in ("company_name", "report_date", "growth_commentary", "analyst_takeaway"):
        setattr(op, path, u.after)
        return True

    if path.startswith("snapshot."):
        attr = path.split(".")[1]
        if attr in op.snapshot.model_fields:
            setattr(op.snapshot, attr, u.after)
            return True
        return False

    if path.startswith("business_overview."):
        attr = path.split(".")[1]
        if attr in op.business_overview.model_fields:
            setattr(op.business_overview, attr, u.after)
            return True
        return False

    if path == "financial_highlights":
        parsed = None
        try:
            parsed = json.loads(u.after) if u.after.strip() else None
        except Exception:
            pass
        if not parsed:
            parsed = _parse_financial_highlights_text(u.after)
        if parsed:
            try:
                from core.schema_onepager import FinancialHighlight
                op.financial_highlights = [FinancialHighlight.model_validate(x) for x in parsed]
                return True
            except Exception:
                pass
        return False

    if path == "segment_insights":
        try:
            parsed = json.loads(u.after) if u.after.strip() else []
            from core.schema_onepager import SegmentInsight
            op.segment_insights = [SegmentInsight.model_validate(x) for x in parsed]
            return True
        except Exception:
            return False

    if path == "key_risks":
        try:
            parsed = json.loads(u.after) if u.after.strip() else []
            from core.schema_onepager import RiskItem
            op.key_risks = [RiskItem.model_validate(x) for x in parsed]
            return True
        except Exception:
            return False

    if path == "recent_developments":
        try:
            parsed = json.loads(u.after) if u.after.strip() else []
            from core.schema_onepager import RecentDevelopment
            op.recent_developments = [RecentDevelopment.model_validate(x) for x in parsed]
            return True
        except Exception:
            return False

    if path == "sources_used":
        try:
            parsed = json.loads(u.after) if u.after.strip() else []
            op.sources_used = parsed
            return True
        except Exception:
            return False

    return False


def _parse_financial_highlights_text(text: str) -> list[dict]:
    """Parse text-format financial highlights like '- Metric: value [period] (YoY: +X%)'."""
    import re
    items = []
    for line in text.strip().split("\n"):
        line = line.strip().lstrip("- ")
        if not line:
            continue
        # Pattern: Metric: value [period] (YoY: +X%)
        match = re.match(r"^(.+?):\s*(.+?)\s*\[([^\]]+)\]\s*(?:\(YoY:\s*([^)]+)\))?", line)
        if match:
            items.append({
                "metric": match.group(1).strip(),
                "value": match.group(2).strip(),
                "period": match.group(3).strip(),
                "yoy_change": (match.group(4) or "").strip(),
            })
    return items


# ── Styling ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
#MainMenu, footer, header { visibility: hidden; }
html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }
.stApp { background: #F8F9FA; }

.hdr { border-bottom: 3px solid #DC2626; padding: 6px 0 14px; margin-bottom: 22px; }
.hdr h1 { font-size: 24px; font-weight: 700; margin: 0; color: #111827; letter-spacing: -0.01em; }
.hdr p  { color: #6B7280; margin: 4px 0 0; font-size: 13px; }

label, .stTextInput label, .stFileUploader label,
[data-testid="stWidgetLabel"] p {
    color: #374151 !important; font-weight: 600 !important; font-size: 13px !important;
}
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
.stButton button[kind="primary"] {
    background: #DC2626 !important; color: #fff !important; border: none !important;
    font-weight: 600 !important; border-radius: 8px !important; padding: 8px 22px !important;
}
.stButton button[kind="primary"]:hover { background: #B91C1C !important; }
.stButton button[kind="secondary"] {
    background: #F1F5F9 !important; color: #334155 !important; border: 1px solid #E2E8F0 !important;
    font-weight: 600 !important; border-radius: 8px !important; padding: 8px 22px !important;
}
.stDownloadButton button {
    background: #DC2626 !important; color: #fff !important; font-weight: 600 !important;
    width: 100%; border: none !important; border-radius: 8px !important; padding: 10px !important;
}
.stDownloadButton button:hover { background: #B91C1C !important; }

[data-testid="stSidebar"] {
    background: #FAFBFC !important;
    border-right: 1px solid #E2E8F0 !important;
}
[data-testid="stStatusWidget"], .stSpinner > div { color: #374151 !important; }

.update-card {
    background: #FFFFFF; border: 1px solid #E2E8F0;
    border-radius: 10px; padding: 16px 20px; margin-bottom: 14px;
}
.update-card.accepted { border-color: #16a34a; background: #f0fdf4; }
.update-card h4 { margin: 0 0 8px; font-size: 14px; color: #111827; }
.update-card .meta { font-size: 11px; color: #64748b; margin-bottom: 10px; }
.update-card .before { background: #FEF2F2; padding: 8px 12px; border-radius: 6px; font-size: 12px; color: #991B1B; margin-bottom: 8px; }
.update-card .after { background: #F0FDF4; padding: 8px 12px; border-radius: 6px; font-size: 12px; color: #166534; margin-bottom: 8px; }
.update-card .reason { font-size: 12px; color: #475569; }
.update-card .evidence { font-size: 11px; color: #64748b; font-style: italic; margin-top: 4px; }
</style>

<div class="hdr">
  <h1>AI Equity Research Report Generator</h1>
  <p>Upload a financial document, get a Geojit-style PDF report AND an interactive one-pager with chatbot.</p>
</div>
""", unsafe_allow_html=True)

# ── Common inputs ───────────────────────────────────────────────────────────
col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    choice = st.selectbox(
        "Company name",
        ["ICICI Bank", "JSW Energy", "LTTS", "POCL", "Eternal (Zomato)", "Other…"],
        index=0,
        key="company_select",
    )
    if choice == "Other…":
        company_name = st.text_input("Enter company name", placeholder="e.g. Tata Motors")
    else:
        company_name = choice

with col2:
    uploaded = st.file_uploader(
        "Financial document", type=["pdf", "csv", "txt"],
        help="Earnings release, investor presentation, or financials as PDF / CSV / TXT.",
        key="file_upload",
    )

with col3:
    st.caption("---")
    with st.expander("Model"):
        extraction_model = st.text_input(
            "Extraction model", value=st.session_state.model,
            help="Fast model for doc extraction. deepseek-chat recommended.",
            key="ext_model_input",
        )
        chat_model = st.text_input(
            "Chat model", value=st.session_state.chat_model,
            help="Smart model for chatbot reasoning. deepseek-v4-pro recommended.",
            key="chat_model_input",
        )
        st.session_state.model = extraction_model
        st.session_state.chat_model = chat_model
        missing = missing_keys_for(extraction_model)
        if missing:
            st.warning(f"Missing API key(s): {', '.join(missing)}")
        key_ok = not missing

gen_col1, gen_col2 = st.columns([1, 5])
with gen_col1:
    go = st.button("Generate", type="primary", disabled=not (uploaded and key_ok))

st.caption("ℹ️ One extraction runs both the report and the one-pager. No duplicate LLM calls.")

# ── Generation ──────────────────────────────────────────────────────────────
if go and uploaded and key_ok:
    try:
        progress = st.progress(0, text="Starting")
        with st.status("Generating report & one-pager", expanded=True) as status:
            st.write("📄 Reading the document.")
            progress.progress(10, text="Reading document")
            doc = ingest(uploaded.name, uploaded.getvalue())
            if doc.has_usable_text:
                st.write(f"Read as {doc.fmt.upper()}.")
            else:
                st.write(f"Read as {doc.fmt.upper()} (no text layer, sending file to model).")

            st.write(f"🤖 Extracting data with {st.session_state.model}. This may take 20–60 seconds.")
            progress.progress(30, text="Extracting with AI, please wait")
            report_data = extract_report(doc, company_name, model=st.session_state.model)
            st.write(f"Extraction returned {len(report_data.highlights)} highlights, "
                     f"{len(report_data.financials)} tables, {len(report_data.charts)} charts.")

            # Enrich with InsightSentry — OVERWRITE time-sensitive fields with live data
            st.write("📡 Fetching live market data from InsightSentry.")
            progress.progress(60, text="Fetching market data")
            ctx = fetch_company_context(company_name)

            # ReportData: always use live data for price-sensitive fields
            if ctx.get("sector") and not report_data.sector:
                report_data.sector = ctx["sector"]
            if ctx.get("current_price"):
                report_data.current_price = ctx["current_price"]  # overwrite stale doc price
                if not report_data.report_date:
                    from datetime import datetime as _dt
                    report_data.report_date = _dt.now().strftime("%B %d, %Y")

            # Append missing company_data items (always use live values)
            existing_labels = {kv.label.lower() for kv in report_data.company_data}
            api_fields = {
                "Market Cap": ctx.get("market_cap"),
                "52W High": ctx.get("52w_high"),
                "52W Low": ctx.get("52w_low"),
                "PE Ratio": ctx.get("pe_ratio"),
                "Dividend Yield": ctx.get("dividend_yield"),
                "Beta": ctx.get("beta"),
            }
            from core.schema import KeyValue
            for label, value in api_fields.items():
                if value:
                    # Remove old entry if exists
                    report_data.company_data = [kv for kv in report_data.company_data if kv.label.lower() != label.lower()]
                    report_data.company_data.append(KeyValue(label=label, value=value))

            st.write("🖨️ Rendering PDF report.")
            progress.progress(70, text="Rendering report PDF")
            pdf_bytes = render_pdf(report_data)

            # ── Derive and enrich one-pager ──────────────────────────────
            st.write("🔄 Deriving one-pager from report data (no second LLM call).")
            progress.progress(80, text="Building one-pager")
            onepager = from_reportdata(report_data)

            # Enrich one-pager snapshot — overwrite all time-sensitive fields with live data
            if ctx.get("ticker") and not onepager.snapshot.ticker:
                onepager.snapshot.ticker = ctx["ticker"]
            if ctx.get("sector") and not onepager.snapshot.sector:
                onepager.snapshot.sector = ctx["sector"]
            # Always overwrite these: they change daily
            if ctx.get("market_cap"):
                onepager.snapshot.market_cap = ctx["market_cap"]
            if ctx.get("current_price"):
                onepager.snapshot.current_price = ctx["current_price"]
            if ctx.get("pe_ratio"):
                onepager.snapshot.pe_ratio = ctx["pe_ratio"]
            if ctx.get("pb_ratio"):
                onepager.snapshot.pb_ratio = ctx["pb_ratio"]
            if ctx.get("dividend_yield"):
                onepager.snapshot.dividend_yield = ctx["dividend_yield"]
            if ctx.get("52w_high"):
                onepager.snapshot.week52_high = ctx["52w_high"]
            if ctx.get("52w_low"):
                onepager.snapshot.week52_low = ctx["52w_low"]
            if ctx.get("beta"):
                onepager.snapshot.beta = ctx["beta"]
            if ctx.get("revenue_ttm") and not onepager.snapshot.revenue_ttm:
                onepager.snapshot.revenue_ttm = ctx["revenue_ttm"]
            if ctx.get("net_income_ttm") and not onepager.snapshot.net_income_ttm:
                onepager.snapshot.net_income_ttm = ctx["net_income_ttm"]

            # Lightweight enrichment: fill segment insights, risks, developments
            st.write("🧠 Enriching one-pager with segment insights, risks, and developments.")
            progress.progress(90, text="Enriching one-pager")
            onepager = enrich_onepager(onepager, report_data, model=st.session_state.model)
            st.session_state.enrichment_done = True

            # Render one-pager HTML
            charts = render_charts(onepager.charts)
            html = render_onepager_html(onepager, charts)
            onepager_pdf = render_onepager_pdf(onepager, charts)

            progress.progress(100, text="Done")
            status.update(label="Report & one-pager ready.", state="complete", expanded=False)

        st.session_state.report_data = report_data
        st.session_state.report_pdf = pdf_bytes
        st.session_state.onepager = onepager
        st.session_state.onepager_html = html
        st.session_state.onepager_pdf = onepager_pdf
        st.session_state.proposed_updates = []
        st.session_state.update_mode = False
        st.session_state.generated_company = onepager.company_name or company_name

        # Create SQLite audit session
        st.session_state.audit_session_id = create_session(
            company_name=onepager.company_name or company_name,
            model_name=st.session_state.model,
            doc_filename=uploaded.name,
        )

        # Short welcome — no long auto-analysis
        st.session_state.chat_history = [{
            "role": "assistant",
            "content": f"**{onepager.company_name or company_name}** ready. "
                       f"Live price: {onepager.snapshot.current_price or 'N/A'} | "
                       f"Market Cap: {onepager.snapshot.market_cap or 'N/A'}. "
                       f"Ask me anything, request changes, or send new info."
        }]

        missing_r = [f for f in CORE_FIELDS if not getattr(report_data, f)]
        missing_op = [f for f in CORE_ONEPAGER_FIELDS if not getattr(onepager, f)]
        if missing_r or missing_op:
            st.info("Some sections came back empty and are shown blank.")

        st.rerun()

    except Exception as exc:
        st.error(f"Generation failed: {exc}")
        st.exception(exc)

# ── Helper: regenerate one-pager renders ────────────────────────────────────
def _regenerate_onepager():
    op = st.session_state.onepager
    charts = render_charts(op.charts)
    st.session_state.onepager_html = render_onepager_html(op, charts)
    st.session_state.onepager_pdf = render_onepager_pdf(op, charts)
    st.session_state.onepager_version += 1


# ── Chat sidebar ─────────────────────────────────────────────────────────────
if st.session_state.onepager is not None:
    with st.sidebar:
        st.markdown("### 💬 Research Chat")
        st.caption("Ask questions, request changes, or provide new info.")

        chat_container = st.container(height=380)
        with chat_container:
            for i, msg in enumerate(st.session_state.chat_history):
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])

                    # Show proposed updates inline
                    updates = msg.get("updates")
                    if updates and not msg.get("resolved"):
                        for j, u in enumerate(updates):
                            st.markdown(f"""
                            <div style="background:#FFF7ED;border:1px solid #F59E0B;border-radius:6px;padding:8px;margin:6px 0;font-size:11px;">
                            <strong style="color:#D97706;">🔄 {u.section_name}</strong>
                            <code style="font-size:10px;">{u.field_path}</code>
                            <div style="background:#FEF2F2;padding:4px 6px;border-radius:3px;margin:4px 0;color:#991B1B;">
                            BEFORE: {u.before[:80]}{'...' if len(u.before) > 80 else ''}
                            </div>
                            <div style="background:#F0FDF4;padding:4px 6px;border-radius:3px;margin:4px 0;color:#166534;">
                            AFTER: {u.after[:80]}{'...' if len(u.after) > 80 else ''}
                            </div>
                            <div style="color:#475569;margin:4px 0;">{u.rationale[:100]}</div>
                            </div>
                            """, unsafe_allow_html=True)

                            cc1, cc2 = st.columns(2)
                            with cc1:
                                if not u.accepted and st.button("✅ Accept", key=f"chat_acc_{i}_{j}", use_container_width=True):
                                    ok = _apply_update(st.session_state.onepager, u)
                                    _regenerate_onepager()
                                    msg["updates"][j].accepted = True
                                    msg["resolved"] = True
                                    status_msg = f"✅ Applied: {u.section_name} updated." if ok else f"❌ Failed to apply update to {u.section_name}. The data format was not recognized."
                                    if st.session_state.audit_session_id:
                                        log_surgical_update(
                                            session_id=st.session_state.audit_session_id,
                                            section_name=u.section_name,
                                            field_path=u.field_path,
                                            before_text=u.before,
                                            after_text=u.after,
                                            rationale=u.rationale,
                                            evidence=u.evidence,
                                            accepted=ok,
                                            user_prompt=st.session_state.chat_history[i - 1]["content"] if i > 0 else "",
                    model_name=st.session_state.chat_model,
                                            model_reasoning=u.model_reasoning,
                                        )
                                    st.session_state.chat_history.append({
                                        "role": "assistant",
                                        "content": status_msg,
                                    })
                                    st.rerun()
                            with cc2:
                                if not u.accepted and st.button("↩ Reject", key=f"chat_rej_{i}_{j}", use_container_width=True):
                                    msg["updates"][j].accepted = False
                                    msg["resolved"] = True
                                    st.rerun()

        if user_msg := st.chat_input("Ask, edit, or provide new info...", key="sidebar_chat"):
            st.session_state.chat_history.append({"role": "user", "content": user_msg})
            with st.spinner("Thinking..."):
                result = chat_with_context(
                    user_msg,
                    st.session_state.onepager,
                    model=st.session_state.chat_model,
                    chat_history=st.session_state.chat_history,
                )

            chat_entry = {"role": "assistant", "content": result["text"]}
            if result["updates"]:
                chat_entry["updates"] = result["updates"]
                chat_entry["resolved"] = False
            st.session_state.chat_history.append(chat_entry)

            if st.session_state.audit_session_id:
                log_chat_interaction_batch(
                    session_id=st.session_state.audit_session_id,
                    user_msg=user_msg,
                    assistant_msg=result["text"],
                    context_onepager=st.session_state.onepager,
                    model_name=st.session_state.chat_model,
                )
            st.rerun()

        if st.session_state.chat_history and st.button("Clear chat", use_container_width=True):
            st.session_state.chat_history = []
            st.rerun()

# ── Tabs ────────────────────────────────────────────────────────────────────
if st.session_state.report_data is not None:
    tab1, tab2, tab3 = st.tabs(["📄 Research Report", "📋 One-Pager & Chat", "📜 Audit Trail"])

    # ── Tab 1: Research Report ───────────────────────────────────────────
    with tab1:
        st.markdown(f"### Geojit-style Research Report: {st.session_state.generated_company}")
        st.caption("Full multi-page PDF with detailed financial statements, charts, and analysis.")

        safe = st.session_state.generated_company.replace(" ", "_")
        st.download_button(
            "⬇ Download PDF Report",
            data=st.session_state.report_pdf,
            file_name=f"{safe}_research_report.pdf",
            mime="application/pdf",
        )

        with st.expander("Extracted data (preview)"):
            rd = st.session_state.report_data
            st.write({
                "company_name": rd.company_name,
                "sector": rd.sector,
                "rating": rd.rating,
                "target_price": rd.target_price,
                "current_price": rd.current_price,
                "highlights": len(rd.highlights),
                "financial_tables": [t.title for t in rd.financials],
                "charts": [c.title for c in rd.charts],
            })

    # ── Tab 2: One-Pager & Chat ──────────────────────────────────────────
    with tab2:
        if st.session_state.onepager_html:
            st.markdown(f"### Company One-Pager: {st.session_state.generated_company}")
            st.caption("Live single-page summary. Editable via surgical updates below.")

            view_mode = st.radio(
                "View", ["Rendered", "Source HTML"],
                horizontal=True, label_visibility="collapsed",
                key="onepager_view",
            )
            if view_mode == "Rendered":
                # Toggle between two identical calls — forces Streamlit to create a new iframe
                # each time the version changes, bypassing browser cache
                v = st.session_state.onepager_version
                if v % 2 == 0:
                    components.html(st.session_state.onepager_html, height=1200, scrolling=True)
                else:
                    components.html(st.session_state.onepager_html, height=1200, scrolling=True)
            else:
                st.code(st.session_state.onepager_html, language="html")

            safe = st.session_state.generated_company.replace(" ", "_")

            c1, c2 = st.columns(2)
            with c1:
                st.download_button(
                    "⬇ Download One-Pager HTML",
                    data=st.session_state.onepager_html,
                    file_name=f"{safe}_onepager.html",
                    mime="text/html",
                )
            with c2:
                st.download_button(
                    "⬇ Download One-Pager PDF",
                    data=st.session_state.onepager_pdf,
                    file_name=f"{safe}_onepager.pdf",
                    mime="application/pdf",
                )

            st.caption("Use the **sidebar chat** to ask questions, request edits, or provide new info. Changes apply instantly.")

    # ── Tab 3: SQLite Audit Trail ────────────────────────────────────────
    with tab3:
        st.markdown("### 📋 Full Audit Trail (SQLite)")
        st.caption("All surgical updates and chat interactions logged across sessions.")

        sessions = list_sessions(limit=20)
        if not sessions:
            st.caption("No sessions yet. Generate a report to start logging.")
        else:
            session_options = {
                f"{s['company_name']} | {s['created_at'][:19]} ({s['doc_filename']})": s["id"]
                for s in sessions
            }
            selected_label = st.selectbox(
                "Select session", list(session_options.keys()), key="audit_session",
            )
            selected_id = session_options[selected_label]

            stats = get_stats_for_session(selected_id)
            ca, cb, cc = st.columns(3)
            ca.metric("Surgical Updates", stats["surgical_updates"])
            cb.metric("Chat Interactions", stats["chat_interactions"])
            cc.metric("Accepted", stats["accepted_updates"])

            st.divider()
            filter_type = st.radio(
                "Filter", ["All", "Surgical Updates", "Chat Interactions"],
                horizontal=True, key="audit_filter",
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
                    if e["event_type"] == "chat_interaction":
                        with st.container():
                            st.markdown(f"""
                            <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:12px;margin-bottom:10px;">
                            <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
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
                                with st.expander("📸 One-pager context snapshot"):
                                    st.code(e["context_snapshot"][:5000], language="json")

                    elif e["event_type"] == "surgical_update":
                        badge = "✅ Accepted" if e["accepted"] else "⏳ Pending"
                        ccolor = "#F0FDF4" if e["accepted"] else "#FFF7ED"
                        bcolor = "#16A34A" if e["accepted"] else "#F59E0B"
                        st.markdown(f"""
                        <div style="background:{ccolor};border:1px solid {bcolor};border-radius:8px;padding:14px;margin-bottom:10px;">
                        <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                            <span style="font-weight:700;font-size:13px;color:#111827;">🔁 {e['section_name']}</span>
                            <span style="font-size:10px;color:#64748B;">{badge} | {e['timestamp'][:19]}</span>
                        </div>
                        <div style="font-size:11px;color:#64748B;margin-bottom:6px;">Field: <code>{e['field_path']}</code> | Model: {e.get('model_name','-')}</div>
                        <div style="background:#FEF2F2;padding:6px 10px;border-radius:4px;margin-bottom:4px;font-size:11px;">
                            <strong style="color:#991B1B;">BEFORE:</strong> {e['before_text'] or '—'}
                        </div>
                        <div style="background:#F0FDF4;padding:6px 10px;border-radius:4px;margin-bottom:4px;font-size:11px;">
                            <strong style="color:#166534;">AFTER:</strong> {e['after_text'] or '—'}
                        </div>
                        <div style="font-size:11px;color:#475569;margin:4px 0;">📝 {e['rationale']}</div>
                        <div style="font-size:10px;color:#94A3B8;font-style:italic;">📎 "{e['evidence']}"</div>
                        </div>
                        """, unsafe_allow_html=True)
                        if e.get("user_prompt"):
                            with st.expander("📥 Triggering prompt"):
                                st.text(e["user_prompt"][:3000])
                        if e.get("model_reasoning"):
                            with st.expander("🧠 Model reasoning steps"):
                                st.text(e["model_reasoning"][:5000])

else:
    st.info("Upload a financial document and click **Generate** to create the report and one-pager.")



