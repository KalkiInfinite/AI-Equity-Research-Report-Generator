"""
Charts — render ChartSpec objects to base64 PNGs for embedding in the report.
=============================================================================

Generic: handles any number of series as grouped bars or lines, so a new chart
type for a new company needs no code change — just a new ChartSpec from the LLM.
"""

from __future__ import annotations

import base64
import io
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

from core.schema import ChartSpec

# Geojit-ish palette.
_COLORS = ["#1f6feb", "#d97706", "#15803d", "#b91c1c", "#7c3aed", "#0891b2"]


def render_chart(spec: ChartSpec) -> Optional[str]:
    """Return a base64-encoded PNG (no data-URI prefix) or None if unrenderable."""
    if not spec.x or not spec.series:
        return None
    # Drop series whose length doesn't line up with x.
    series = [s for s in spec.series if s.y and len(s.y) == len(spec.x)]
    if not series:
        return None

    fig, ax = plt.subplots(figsize=(4.4, 2.6), dpi=150)
    n = len(spec.x)
    idx = range(n)

    if spec.kind == "line":
        for i, s in enumerate(series):
            ax.plot(idx, s.y, marker="o", linewidth=2, markersize=4,
                    color=_COLORS[i % len(_COLORS)], label=s.name)
    else:  # grouped bar
        total = len(series)
        width = 0.8 / total
        for i, s in enumerate(series):
            offset = (i - (total - 1) / 2) * width
            ax.bar([x + offset for x in idx], s.y, width=width,
                   color=_COLORS[i % len(_COLORS)], label=s.name)

    ax.set_xticks(list(idx))
    ax.set_xticklabels(spec.x, fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    if spec.y_label:
        ax.set_ylabel(spec.y_label, fontsize=7)
    ax.set_title(spec.title, fontsize=9, fontweight="bold", pad=8)
    if len(series) > 1:
        ax.legend(fontsize=6.5, frameon=False, loc="best")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout(pad=0.6)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def render_all(specs: list[ChartSpec]) -> list[dict]:
    """Render every spec; return [{title, b64}] for those that succeed."""
    out = []
    for spec in specs:
        b64 = render_chart(spec)
        if b64:
            out.append({"title": spec.title, "b64": b64})
    return out
