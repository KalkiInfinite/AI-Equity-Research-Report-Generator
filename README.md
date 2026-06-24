# AI Equity Research Report Generator

Bull · AI Software Engineer Assessment

Upload a company's financial document (earnings release, investor presentation,
or a CSV/TXT of financials) → an LLM extracts the key financials, metrics, and
narrative → download a **Geojit-style PDF research report** with tables, sections,
paragraphs, and charts.

```
upload (PDF / CSV / TXT)  →  ingest  →  LLM extraction (structured)  →  charts  →  HTML template  →  PDF
```

---

## Tech used

| Layer | Library |
|---|---|
| UI | **Streamlit** |
| Document ingest | **pypdf** (PDF text), **pandas** (CSV), plain read (TXT) |
| AI extraction | **Any LLM** via **LiteLLM** (default `anthropic/claude-sonnet-4-6`; also OpenAI, Gemini, Mistral, Ollama, etc.) using forced tool/function calling → validated **Pydantic** schema |
| Charts | **matplotlib** (rendered to base64 PNG) |
| Template | **Jinja2** HTML + CSS |
| PDF generation | **WeasyPrint** |

---

## Where the template fields are defined

Everything the report can contain is defined **once** in
[`core/schema.py`](core/schema.py) as the `ReportData` Pydantic model. That model
is simultaneously:

* the **contract the LLM fills** (its JSON schema is handed to the model as a tool/function), and
* the **data the template renders** ([`templates/report.html`](templates/report.html) + [`templates/report.css`](templates/report.css)).

To add a new field or section, edit `core/schema.py` and `templates/report.html` —
no other code changes. Financial statements, shareholding, estimates, etc. are all
modelled as a generic `Table` (title + columns + rows), so a new table for a new
company needs **zero** code changes.

---

## Setup

Requires Python 3.10+ and an API key for any supported LLM provider (Anthropic,
OpenAI, Google Gemini, Mistral, and so on). You only need the key for the model
you choose.

```bash
cd research-report-generator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**macOS only:** WeasyPrint needs Pango/glib. Install once with `brew install pango`
(this also pulls in glib). `run.sh` and `generate_examples` wrappers add Homebrew's
lib folder to the loader path for you.

Add your key:

```bash
cp .env.example .env
# edit .env: set EXTRACTION_MODEL and the matching key, e.g.
#   EXTRACTION_MODEL=anthropic/claude-sonnet-4-6  + ANTHROPIC_API_KEY=sk-ant-...
#   EXTRACTION_MODEL=gpt-4o                        + OPENAI_API_KEY=sk-...
#   EXTRACTION_MODEL=gemini/gemini-1.5-pro         + GEMINI_API_KEY=...
```

You can also change the model live in the app under **Model settings**.

---

## Run the app

```bash
python start.py          # recommended: sets the WeasyPrint lib path, then launches
# or:
./run.sh                 # shell-script equivalent of start.py
# or, if WeasyPrint's system libs are already on your loader path:
streamlit run app.py
```

Opens at http://localhost:8501. Then enter a company name, upload a PDF / CSV / TXT,
click **Generate report**, and click **Download PDF report**. Press Ctrl+C in the
terminal to stop.

1. Enter a **company name**.
2. Upload a **PDF, CSV, or TXT**.
3. Click **Generate report**.
4. Click **⬇ Download PDF report**.

---

## Generate example PDFs from the CLI

```bash
# macOS: prefix with the lib path (see run.sh)
DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib" \
  python generate_examples.py            # runs the default ICICI + LTTS docs

# or pass your own "Name=path" pairs:
python generate_examples.py "Acme=path/to/file.pdf" "Beta=data.csv"
```

Generated PDFs land in [`result/`](result/).

---

## Included examples (`result/`)

| File | Source doc | Input format |
|---|---|---|
| `ICICI_Bank_research_report.pdf` | ICICI Q2FY26 | **PDF** |
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
  and **≥1 chart** (revenue trend + margin charts by default).
* **≥2 input formats** — PDF, CSV, and TXT (see examples above).
* **Missing fields handled gracefully** — every field is optional; absent values
  render as `—`. Empty analyst rating/target on raw company filings is normal and
  shown blank rather than invented (the extractor is instructed never to guess).
* **One-click PDF download** — the `⬇ Download PDF report` button.

### Nice-to-haves
* **Multiple chart types** — grouped bars (revenue/segment) and line charts (margins),
  driven by declarative `ChartSpec`s from the model.
* **Modular** — add fields via `schema.py`; tables are fully generic.

---

## Project structure

```
research-report-generator/
├── app.py                  # Streamlit UI (entry point)
├── run.sh                  # macOS launch wrapper (WeasyPrint lib path)
├── generate_examples.py    # CLI: batch-generate example PDFs
├── core/
│   ├── schema.py           # ReportData — single source of truth for template fields
│   ├── ingest.py           # PDF / CSV / TXT → normalised text (+ native PDF bytes)
│   ├── extractor.py        # LLM tool-use (via LiteLLM) → validated ReportData
│   ├── charts.py           # ChartSpec → base64 PNG (matplotlib)
│   └── report.py           # Jinja2 render + WeasyPrint → PDF bytes
├── templates/
│   ├── report.html         # report layout (Geojit-style)
│   └── report.css          # report styling
├── test_data/              # sample CSV + extracted TXT inputs
└── result/                 # generated example PDFs
```

## Notes & limitations

* Raw company filings (investor presentations) carry no analyst rating/target/CMP,
  so those header fields render blank — by design. Feed a broker note to populate them.
* The extractor sends up to ~60k characters of document text; for very long decks the
  tail is truncated (scanned PDFs are sent to the model as a file for vision-capable models).
* Figures are AI-extracted — verify against the source before any real use.
