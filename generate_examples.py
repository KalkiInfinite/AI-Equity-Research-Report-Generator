"""
Generate example report PDFs from the provided test documents (CLI, no UI).
===========================================================================

Usage:
    python generate_examples.py "Company Name=path/to/doc.pdf" ...

With no args it runs the default set from docs/AI Software Engineer and writes
PDFs into ./result/.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from core.ingest import ingest
from core.extractor import extract_report, missing_keys_for, DEFAULT_MODEL
from core.report import render_pdf

ASSETS = Path(__file__).resolve().parent / "docs" / "AI Software Engineer"
OUT = Path(__file__).resolve().parent / "result"

DEFAULTS = [
    ("ICICI Bank", ASSETS / "ICICI Q2FY26.pdf"),
    ("LTTS", ASSETS / "LTTS Q2FY26.pdf"),
]


def build(company: str, path: Path, model: str) -> Path:
    print(f"→ {company}: reading {path.name}")
    doc = ingest(path.name, path.read_bytes())
    print(f"  format={doc.fmt} usable_text={doc.has_usable_text}")
    data = extract_report(doc, company, model)
    print(f"  extracted: rating={data.rating!r} highlights={len(data.highlights)} "
          f"tables={[t.title for t in data.financials]} charts={len(data.charts)}")
    pdf = render_pdf(data)
    OUT.mkdir(exist_ok=True)
    out = OUT / f"{company.replace(' ', '_')}_research_report.pdf"
    out.write_bytes(pdf)
    print(f"  wrote {out} ({len(pdf):,} bytes)\n")
    return out


def main():
    model = DEFAULT_MODEL
    missing = missing_keys_for(model)
    if missing:
        sys.exit(f"Model '{model}' needs these API keys (set them in .env): {', '.join(missing)}")

    jobs = []
    for arg in sys.argv[1:]:
        name, _, p = arg.partition("=")
        jobs.append((name.strip(), Path(p.strip())))
    if not jobs:
        jobs = DEFAULTS

    for company, path in jobs:
        if not path.exists():
            print(f"!! skip {company}: {path} not found")
            continue
        build(company, path, model)


if __name__ == "__main__":
    main()
