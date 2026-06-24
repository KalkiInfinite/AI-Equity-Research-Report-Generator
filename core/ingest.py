"""
Ingest layer — turn an uploaded context document into something the LLM can read.
=================================================================================

Supports three input formats (satisfies the "works with at least two formats"
acceptance criterion):

  * PDF  — extracted to text with pypdf; the raw bytes are also kept so the
           extractor can fall back to sending the file to a vision-capable
           model for scanned / image-heavy decks where text extraction is empty.
  * CSV  — parsed with pandas and rendered to a clean text table.
  * TXT  — read as-is.

The PDF text-vs-vision strategy is adapted from the nova-trade-pipeline
extractor.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class IngestedDoc:
    """Normalised view of an uploaded document."""
    filename: str
    fmt: str                       # 'pdf' | 'csv' | 'txt'
    text: str                      # best-effort plain text (may be empty for scanned PDFs)
    pdf_bytes: Optional[bytes] = None   # present only for PDFs, for native-vision fallback

    @property
    def has_usable_text(self) -> bool:
        return len(self.text.strip()) > 150


def _read_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception:
        return ""


def _read_csv_text(data: bytes) -> str:
    """Render a CSV as a readable text table (and a quick numeric summary)."""
    import pandas as pd
    for enc in ("utf-8", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(data), encoding=enc)
            break
        except Exception:
            df = None
    if df is None or df.empty:
        return data.decode("utf-8", errors="ignore")

    parts = [f"CSV with {len(df)} rows and columns: {', '.join(map(str, df.columns))}", ""]
    parts.append(df.to_string(index=False))
    return "\n".join(parts)


def ingest(filename: str, data: bytes) -> IngestedDoc:
    """Dispatch on file extension and return a normalised IngestedDoc."""
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        return IngestedDoc(filename=filename, fmt="pdf", text=_read_pdf_text(data), pdf_bytes=data)
    if suffix == ".csv":
        return IngestedDoc(filename=filename, fmt="csv", text=_read_csv_text(data))
    if suffix in (".txt", ".md", ".text"):
        return IngestedDoc(filename=filename, fmt="txt", text=data.decode("utf-8", errors="ignore"))

    raise ValueError(f"Unsupported file type '{suffix}'. Upload a PDF, CSV, or TXT file.")
