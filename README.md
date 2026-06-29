# AI Equity Research Report Generator

Bull · AI Software Engineer Assessment

Upload a company's financial document (earnings release, investor presentation,
or a CSV/TXT of financials) → an LLM extracts the key financials, metrics, and
narrative → download a **Geojit-style PDF research report** with tables, sections,
paragraphs, and charts. Also get an interactive **one-page summary** with a
sidebar chatbot and surgical-update workflow.

```
upload (PDF / CSV / TXT)  →  ingest  →  LLM extraction (structured)  →  charts  →  HTML template  →  PDF
                                                                                        →  one‑pager + chatbot + surgical updates
```

---

## App modes

There are three Streamlit apps, each launched with its own script:

| Script | App | What it does |
|---|---|---|
| `python start.py` | `app.py` | Task 1: PDF research report only |
| `python start_onepager.py` | `app_onepager.py` | Task 2: One-pager + sidebar chatbot + surgical updates |
| `python start_unified.py` | `app_unified.py` | Combined: one extraction → both the PDF report and the interactive one-pager (no duplicate LLM calls) |

The **unified app** (`start_unified.py`) is the recommended entry point — run one
LLM extraction and get everything.

---

## Tech used

| Layer | Library |
|---|---|
| UI | **Streamlit** |
| Document ingest | **pypdf** (PDF text), **pandas** (CSV), plain read (TXT) |
| AI extraction | **Any LLM** via **LiteLLM** using forced tool/function calling → validated **Pydantic** schema |
| Charts | **matplotlib** (rendered to base64 PNG) |
| Templates | **Jinja2** HTML + CSS (`report.html`, `onepager.html`) |
| PDF generation | **WeasyPrint** |
| Audit trail | **SQLite** (`data/audit.db`) |
| Market data | **InsightSentry** — live enrichment (market cap, CMP, P/E, 52W, etc.) |

---

## Where the template fields are defined

### Report fields (`core/schema.py`)
Everything the report can contain is defined once in `ReportData`. That model is:

* the **contract the LLM fills** (its JSON schema is handed to the model as a tool/function), and
* the **data the template renders** (`report.html` + `report.css`).

### One-pager fields (`core/schema_onepager.py`)
The one-pager is driven by `OnePagerData` — a separate, tighter schema for a
single-page view. It is either extracted directly by the one-pager app, or
**derived from `ReportData`** in the unified app (no second LLM call needed).

To add a new field or section, edit the relevant schema file and its matching
template — no other code changes.

---

## Setup

Requires Python 3.10+ and an API key for any supported LLM provider (Anthropic,
OpenAI, Google Gemini, Mistral, DeepSeek, and so on). You only need the key for
the model you choose.

```bash
cd research-report-generator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**macOS only:** WeasyPrint needs Pango/glib. Install once with `brew install pango`
(this also pulls in glib). The `start*.py` launchers add Homebrew's lib folder
to the loader path for you.

Add your key:

```bash
cp .env.example .env
# edit .env: set EXTRACTION_MODEL and the matching key, e.g.
#   EXTRACTION_MODEL=deepseek/deepseek-chat    + DEEPSEEK_API_KEY=sk-...
#   EXTRACTION_MODEL=anthropic/claude-sonnet-4-6  + ANTHROPIC_API_KEY=sk-ant-...
#   EXTRACTION_MODEL=gpt-4o                        + OPENAI_API_KEY=sk-...
#   EXTRACTION_MODEL=gemini/gemini-1.5-pro         + GEMINI_API_KEY=...
```

You can also change the model live in the app under **Model settings**.

---

## Run the app

```bash
# Task 1 only: research report
python start.py

# Task 2 only: one-pager + chatbot + surgical updates
python start_onepager.py

# Combined (recommended): report + one-pager from a single extraction
python start_unified.py
```

Opens at http://localhost:8501. The flow:
1. Enter a **company name**.
2. Upload a **PDF, CSV, or TXT**.
3. Click **Generate**.
4. Download the PDF report and/or HTML one-pager.

Press Ctrl+C in the terminal to stop.

---

## Features beyond the PDF report

### One-pager with sidebar chatbot
A clean single-page HTML summary with a sidebar chatbot. The bot answers
questions **grounded in the one-pager data only** — no hallucinated answers.
It can also propose edits: ask it to update a figure or rephrase a section,
and it returns before/after/rationale/evidence that you can accept or reject
inline.

### Surgical updates
Paste new information (filing update, press release, earnings call notes) and
the LLM compares it against the current one-pager, proposing **selective section
changes** — not a full regeneration. Each proposal has:
- `before` and `after` text
- a `rationale` for the change
- an `evidence` quote from the pasted text
- accept/reject buttons

Accepted changes are applied to the one-pager instantly and recorded in the
audit trail.

### InsightSentry enrichment
When a one-pager is generated, InsightSentry fetches live market data
(market cap, current price, P/E, P/B, 52W high/low, beta, dividend yield,
etc.) and fills any gaps. Time-sensitive fields like CMP are **always**
overwritten with live data, not stale document values.

### SQLite audit trail
Every chat interaction and surgical update is logged to `data/audit.db`.
The **Audit Trail** tab (unified app) or expander (one-pager app) lets you:
- Browse past sessions by company and document
- Filter by event type (chat Q&A or surgical updates)
- See full before/after, rationale, evidence, triggering prompts, and model reasoning

---

## Generate example PDFs from the CLI

```bash
# macOS: prefix with the lib path (see run.sh)
DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib" \
  python generate_examples.py            # runs the default ICICI, JSW, LTTS docs

# or pass your own "Name=path" pairs:
python generate_examples.py "Acme=path/to/file.pdf" "Beta=data.csv"
```

Generated PDFs land in `result/`.

---

## Included examples (`result/`)

| File | Source doc | Input format |
|---|---|---|
| `ICICI_Bank_research_report.pdf` | ICICI Q2FY26 | **PDF** |
| `JSW_Energy_research_report.pdf` | JSW Energy Q2FY26 | **PDF** |
| `LTTS_research_report.pdf` | LTTS Q2FY26 | **PDF** |
| `POCL_(from_TXT)_research_report.pdf` | POCL Q2FY26 | **TXT** |
| `Sample_Co_(from_CSV)_research_report.pdf` | sample_financials.csv | **CSV** |

---

## How the acceptance criteria are met

* **Template matches the sample** — `report.html`/`report.css` recreate the Geojit
  layout and section order: header + rating block, description, result highlights,
  company-data side box, shareholding & price-performance tables, charts, estimate
  revisions, key highlights, detailed financial statements, recommendation history,
  disclaimer.
* **Required fields populated** — financial tables, metric box, narrative sections,
  and ≥1 chart (revenue trend + margin charts by default).
* **≥2 input formats** — PDF, CSV, and TXT (see examples above).
* **Missing fields handled gracefully** — every field is optional; absent values
  render as `—`. Empty analyst rating/target on raw company filings is normal and
  shown blank rather than invented (the extractor is instructed never to guess).
* **One-click PDF download** — the download button outputs a ready-to-use PDF.

### Nice-to-haves
* **Multiple chart types** — grouped bars (revenue/segment) and line charts (margins),
  driven by declarative `ChartSpec`s from the model.
* **Modular** — add fields via `schema.py`; tables are fully generic.
* **One-pager + chatbot** — single-page summary with an LLM chatbot grounded in the data.
* **Surgical updates** — paste new info to get selective, auditable section changes.
* **Live market enrichment** — InsightSentry fills current price, market cap, P/E, etc.
* **SQLite audit trail** — every interaction logged with full before/after/evidence.

---

## Project structure

```
research-report-generator/
├── app.py                       # Streamlit UI — Task 1: PDF report only
├── app_onepager.py              # Streamlit UI — Task 2: one-pager + chatbot + updates
├── app_unified.py               # Streamlit UI — combined: report + one-pager (recommended)
├── start.py                     # Launcher for PDF report app (sets WeasyPrint lib path)
├── start_onepager.py            # Launcher for one-pager app
├── start_unified.py             # Launcher for unified app
├── run.sh                       # Shell-script macOS launch wrapper
├── generate_examples.py         # CLI: batch-generate example PDFs
├── core/
│   ├── schema.py                # ReportData — source of truth for report fields
│   ├── schema_onepager.py       # OnePagerData — source of truth for one-pager fields
│   ├── ingest.py                # PDF / CSV / TXT → normalised text (+ native PDF bytes)
│   ├── extractor.py             # LLM tool-use (via LiteLLM) → validated ReportData
│   ├── extractor_onepager.py    # LLM extraction → OnePagerData + chat + enrichment
│   ├── insight.py               # InsightSentry — live market data enrichment
│   ├── updater.py               # Surgical update — LLM proposals with before/after/evidence
│   ├── audit_db.py              # SQLite audit trail — chat + update logging
│   ├── charts.py                # ChartSpec → base64 PNG (matplotlib)
│   └── report.py                # Jinja2 render + WeasyPrint → PDF bytes
├── templates/
│   ├── report.html              # Report layout (Geojit-style)
│   ├── report.css               # Report styling
│   └── onepager.html            # One-pager layout
├── data/
│   └── audit.db                 # SQLite audit trail database
├── docs/                        # Assessment docs and source files
├── test_data/                   # Sample CSV + extracted TXT inputs
└── result/                      # Generated example PDFs
```

## Notes & limitations

* Raw company filings (investor presentations) carry no analyst rating/target/CMP,
  so those header fields render blank — by design. Feed a broker note to populate them.
* The extractor sends up to ~60k characters of document text; for very long decks the
  tail is truncated (scanned PDFs are sent to the model as a file for vision-capable models).
* Figures are AI-extracted — verify against the source before any real use.
* The chatbot in the one-pager is grounded in the one-pager data only. It cannot
  answer questions about external information not in the document.
* Surgical updates use a separate LLM call for comparison. The model proposes
  changes that you review before applying — it does not auto-modify the one-pager.
* InsightSentry enrichment is currently mock-based (returns hardcoded values for
  known companies). Swap in a real market-data API in `core/insight.py`.
