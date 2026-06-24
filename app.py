"""
Streamlit UI — upload a company financial document, get a downloadable PDF report.
==================================================================================

Inputs : company name + file upload (PDF / CSV / TXT)
Output : one-click download of the generated research-report PDF.
"""

import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from core.ingest import ingest
from core.extractor import extract_report, missing_keys_for, DEFAULT_MODEL
from core.report import render_pdf
from core.schema import CORE_FIELDS

st.set_page_config(page_title="AI Equity Research", layout="centered")

# Palette borrowed from the nova-trade-pipeline UI: light background, white
# surfaces, red (#DC2626) accent, Inter font, dark slate text.
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

#MainMenu, footer, header { visibility: hidden; }
html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }
.stApp { background: #F8F9FA; }

/* header */
.hdr { border-bottom: 3px solid #DC2626; padding: 6px 0 14px; margin-bottom: 22px; }
.hdr h1 { font-size: 24px; font-weight: 700; margin: 0; color: #111827; letter-spacing: -0.01em; }
.hdr p  { color: #6B7280; margin: 4px 0 0; font-size: 13px; }

/* make widget labels clearly visible (the dark-on-white bug) */
label, .stTextInput label, .stFileUploader label,
[data-testid="stWidgetLabel"] p {
    color: #374151 !important; font-weight: 600 !important; font-size: 13px !important;
}

/* text input + uploader: white surface, light border, dark text */
.stTextInput input {
    background: #FFFFFF !important; color: #111827 !important;
    border: 1px solid #E2E8F0 !important; border-radius: 8px !important;
}
[data-testid="stFileUploaderDropzone"] {
    background: #FFFFFF !important; border: 1px dashed #CBD5E1 !important; border-radius: 8px !important;
}
[data-testid="stFileUploaderDropzone"] * { color: #475569 !important; }

/* primary (Generate) button — red accent */
.stButton button[kind="primary"] {
    background: #DC2626 !important; color: #fff !important; border: none !important;
    font-weight: 600 !important; border-radius: 8px !important; padding: 8px 22px !important;
}
.stButton button[kind="primary"]:hover { background: #B91C1C !important; }

/* download button */
.stDownloadButton button {
    background: #DC2626 !important; color: #fff !important; font-weight: 600 !important;
    width: 100%; border: none !important; border-radius: 8px !important; padding: 10px !important;
}
.stDownloadButton button:hover { background: #B91C1C !important; }

/* status / progress text stays readable */
[data-testid="stStatusWidget"], .stSpinner > div { color: #374151 !important; }
</style>
<div class="hdr">
  <h1>AI Equity Research Report Generator</h1>
  <p>Upload a company financial document so the AI can extract the data and build a Geojit-style PDF report for you to download.</p>
</div>
""", unsafe_allow_html=True)

# Any LLM the user has: pick a model, supply that provider's key in .env.
with st.expander("Model settings"):
    model = st.text_input(
        "LLM model", value=DEFAULT_MODEL,
        help="Any LiteLLM model id, e.g. anthropic/claude-sonnet-4-6, gpt-4o, "
             "gemini/gemini-1.5-pro, mistral/mistral-large-latest, ollama/llama3.",
    )
    st.caption("Set the matching API key in your .env file "
               "(ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, and so on).")

missing = missing_keys_for(model)
if missing:
    st.warning(f"This model needs an API key that is not set: {', '.join(missing)}. "
               "Add it to your .env file to enable extraction.")
key_ok = not missing

# Known test companies from the assessment, plus an "Other" escape hatch so
# the app still works for any document you upload.
COMPANY_OPTIONS = ["ICICI Bank", "JSW Energy", "LTTS", "POCL", "Eternal (Zomato)", "Other…"]

choice = st.selectbox("Company name", COMPANY_OPTIONS, index=0)
if choice == "Other…":
    company_name = st.text_input("Enter company name", placeholder="e.g. Tata Motors")
else:
    company_name = choice

st.caption("ℹ️ Pick the **same company as the PDF/file you upload below** "
           "(e.g. choose *JSW Energy* if you upload the JSW Energy document).")

uploaded = st.file_uploader("Context document", type=["pdf", "csv", "txt"],
                            help="Earnings release, investor presentation, or financials as PDF / CSV / TXT.")

go = st.button("Generate report", type="primary", disabled=not (uploaded and key_ok))

if go and uploaded and key_ok:
    try:
        progress = st.progress(0, text="Starting")
        with st.status("Generating your report", expanded=True) as status:
            st.write("📄 Reading the document.")
            progress.progress(15, text="Reading document")
            doc = ingest(uploaded.name, uploaded.getvalue())
            if doc.has_usable_text:
                st.write(f"This file was read as {doc.fmt.upper()} and its text was "
                         "extracted successfully.")
            else:
                st.write(f"This file was read as {doc.fmt.upper()} but it has no text layer, "
                         "so the file is being sent to the model to read directly instead.")

            st.write(f"🤖 Now extracting the financials with {model}. This can take "
                     "anywhere from twenty to sixty seconds for large documents, so please wait.")
            progress.progress(45, text="Extracting with AI, please wait")
            data = extract_report(doc, company_name, model)
            st.write(f"Extraction finished and returned {len(data.highlights)} highlights, "
                     f"{len(data.financials)} financial tables, and {len(data.charts)} charts.")

            st.write("🖨️ Rendering the PDF.")
            progress.progress(80, text="Rendering PDF")
            pdf_bytes = render_pdf(data)

            progress.progress(100, text="Done")
            status.update(label="Your report is ready.", state="complete", expanded=False)

        missing = [f for f in CORE_FIELDS if not getattr(data, f)]
        if missing:
            st.info("Some sections came back empty (rendered blank in the PDF): "
                    + ", ".join(missing))

        st.success(f"Report ready for **{data.company_name or company_name}**.")

        # quick on-screen preview of what was extracted
        with st.expander("Extracted data (preview)"):
            st.write({
                "rating": data.rating, "target_price": data.target_price,
                "highlights": len(data.highlights), "company_data": len(data.company_data),
                "financial_tables": [t.title for t in data.financials],
                "charts": [c.title for c in data.charts],
            })

        safe = (data.company_name or company_name or "report").replace(" ", "_")
        st.download_button("⬇ Download PDF report", data=pdf_bytes,
                           file_name=f"{safe}_research_report.pdf", mime="application/pdf")
    except Exception as exc:
        st.error(f"Generation failed: {exc}")
        st.exception(exc)
