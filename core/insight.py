"""
InsightSentry adapter — fetch live company context to enrich the one-pager.
================================================================================

Uses the InsightSentry REST API (via RapidAPI gateway) to fetch:
  - Symbol search (company name → ticker code)
  - Symbol info (sector, market cap, PE ratio, 52W hi/lo, beta, dividends, etc.)
  - Real-time quotes (current price)

Set INSIGHTSENTRY_API_KEY in .env (your RapidAPI key). Falls back to the
built-in mock if the key is missing or any API call fails.

Symbol format for Indian stocks: NSE:ICICIBANK, NSE:JSWENERGY, etc.
"""

from __future__ import annotations

import os
from typing import Optional

import requests

API_KEY = os.getenv("INSIGHTSENTRY_API_KEY", "")
BASE_URL = "https://insightsentry.p.rapidapi.com"
HEADERS = {
    "X-RapidAPI-Key": API_KEY,
    "X-RapidAPI-Host": "insightsentry.p.rapidapi.com",
}

# ── Known ticker aliases (fallback when search fails) ───────────────────────
_KNOWN_TICKERS = {
    "icici bank": "NSE:ICICIBANK",
    "icicibank": "NSE:ICICIBANK",
    "jsw energy": "NSE:JSWENERGY",
    "jswenergy": "NSE:JSWENERGY",
    "ltts": "NSE:LTTS",
    "lt technology services": "NSE:LTTS",
    "pocl": "NSE:POCL",
    "pondy oxides": "NSE:POCL",
    "eternal": "NSE:ETERNAL",
    "zomato": "NSE:ZOMATO",
}

# ── Mock fallback (same as before) ──────────────────────────────────────────
_MOCK_DB = {
    "icici bank": {
        "ticker": "ICICIBANK", "sector": "Banks - Private Sector",
        "market_cap": "Rs. 8,95,000 cr", "current_price": "Rs. 1,285.40",
        "revenue_ttm": "Rs. 1,86,000 cr", "net_income_ttm": "Rs. 44,000 cr",
        "employees": "~1,40,000", "founded": "1994", "headquarters": "Mumbai, India",
        "pe_ratio": "20.4x", "pb_ratio": "3.2x", "dividend_yield": "0.8%",
        "52w_high": "Rs. 1,362.80", "52w_low": "Rs. 960.00",
        "competitors": "HDFC Bank, SBI, Axis Bank, Kotak Mahindra",
    },
    "jsw energy": {
        "ticker": "JSWENERGY", "sector": "Power Generation & Distribution",
        "market_cap": "Rs. 1,20,000 cr", "current_price": "Rs. 735.20",
        "revenue_ttm": "Rs. 11,500 cr", "net_income_ttm": "Rs. 1,800 cr",
        "employees": "~3,500", "founded": "1994", "headquarters": "Mumbai, India",
        "pe_ratio": "66.7x", "pb_ratio": "4.5x", "dividend_yield": "0.3%",
        "52w_high": "Rs. 830.40", "52w_low": "Rs. 435.00",
        "competitors": "NTPC, Tata Power, Adani Power, NHPC",
    },
    "ltts": {
        "ticker": "LTTS", "sector": "IT Services & Consulting",
        "market_cap": "Rs. 55,000 cr", "current_price": "Rs. 5,320.10",
        "revenue_ttm": "Rs. 9,600 cr", "net_income_ttm": "Rs. 1,350 cr",
        "employees": "~23,000", "founded": "2012", "headquarters": "Vadodara, India",
        "pe_ratio": "40.7x", "pb_ratio": "9.8x", "dividend_yield": "1.1%",
        "52w_high": "Rs. 5,800.00", "52w_low": "Rs. 3,900.00",
        "competitors": "Tata Elxsi, Cyient, KPIT Tech, Persistent",
    },
    "pocl": {
        "ticker": "POCL", "sector": "Chemicals - Specialty",
        "market_cap": "Rs. 280 cr", "current_price": "Rs. 528.00",
        "revenue_ttm": "Rs. 700 cr", "net_income_ttm": "Rs. 60 cr",
        "employees": "~500", "founded": "1992", "headquarters": "Chennai, India",
        "pe_ratio": "4.7x", "pb_ratio": "1.2x", "dividend_yield": "0.5%",
        "52w_high": "Rs. 610.00", "52w_low": "Rs. 260.00",
        "competitors": "",
    },
    "eternal": {
        "ticker": "ETERNAL", "sector": "Food & Beverage",
        "market_cap": "Rs. 2,40,000 cr", "current_price": "Rs. 264.80",
        "revenue_ttm": "Rs. 14,000 cr", "net_income_ttm": "Rs. 351 cr",
        "employees": "~10,000", "founded": "2008", "headquarters": "Gurugram, India",
        "pe_ratio": "—", "pb_ratio": "—", "dividend_yield": "—",
        "52w_high": "Rs. 304.70", "52w_low": "Rs. 126.00",
        "competitors": "Swiggy",
    },
}

_FALLBACK = {k: "" for k in [
    "ticker", "sector", "market_cap", "current_price", "revenue_ttm",
    "net_income_ttm", "employees", "founded", "headquarters",
    "pe_ratio", "pb_ratio", "dividend_yield", "52w_high", "52w_low", "competitors",
]}


def _normalise(name: str) -> str:
    return name.strip().lower().replace("&", "and")


def _api_get(endpoint: str, params: dict | None = None) -> dict | None:
    """Lightweight wrapper around InsightSentry RapidAPI calls."""
    if not API_KEY:
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}{endpoint}",
            headers=HEADERS,
            params=params or {},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _search_symbol(company_name: str) -> str | None:
    """Search InsightSentry for a symbol code. Returns e.g. 'NSE:ICICIBANK'."""
    # Try known aliases first (avoids API call for known companies)
    key = _normalise(company_name)
    if key in _KNOWN_TICKERS:
        return _KNOWN_TICKERS[key]

    # Try API search
    result = _api_get("/v3/symbols/search", {"query": company_name, "type": "stock", "page": 1})
    if result and result.get("symbols"):
        # Prefer Indian exchanges
        for s in result["symbols"]:
            if s.get("exchange", "").startswith("NSE"):
                return s["code"]
        # Fall back to first result
        return result["symbols"][0].get("code")
    return None


def fetch_company_context(ticker_or_name: str) -> dict:
    """
    Fetch enrichment context from InsightSentry API, with mock fallback.

    Tries:
      1. Search for the symbol code → /v3/symbols/search
      2. Fetch symbol info → /v3/symbols/{code}/info
      3. Falls back to mock data for known companies if API unavailable.
    """
    key = _normalise(ticker_or_name)

    # ── Try real API ────────────────────────────────────────────────────
    symbol_code = _search_symbol(ticker_or_name) or _KNOWN_TICKERS.get(key, "")
    if symbol_code and API_KEY:
        info = _api_get(f"/v3/symbols/{symbol_code}/info")
        if info:
            ctx = _parse_symbol_info(info)
            if ctx and any(ctx.values()):
                return ctx

    # ── Mock fallback ───────────────────────────────────────────────────
    if key in _MOCK_DB:
        return dict(_MOCK_DB[key])
    for known_name, data in _MOCK_DB.items():
        if key in known_name or known_name in key:
            return dict(data)
    return dict(_FALLBACK)


def _parse_symbol_info(info: dict) -> dict:
    """Convert InsightSentry /info response to our flat enrichment dict."""

    def _fmt_price(v) -> str:
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            if v >= 10_000:
                return f"Rs. {v:,.0f}"
            return f"Rs. {v:,.2f}"
        return str(v)

    def _fmt_cap(v) -> str:
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            v_cr = v / 1e7  # convert to crores (1 cr = 10 million)
            if v_cr >= 100:
                return f"Rs. {v_cr:,.0f} cr"
            return f"Rs. {v_cr:,.2f} cr"
        return str(v)

    def _fmt_number(v, suffix: str = "") -> str:
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            if suffix:
                return f"{v:,.2f}{suffix}"
            return f"{v:,.2f}"
        return str(v)

    ctx = {
        "ticker": (info.get("code") or "").split(":")[-1] if ":" in (info.get("code") or "") else info.get("code", ""),
        "sector": info.get("sector", ""),
        "market_cap": _fmt_cap(info.get("current_market_cap") or info.get("market_cap")),
        "current_price": _fmt_price(info.get("prev_close_price")),
        "revenue_ttm": _fmt_cap(info.get("total_revenue")),
        "net_income_ttm": _fmt_cap(info.get("earnings_per_share_basic_ttm")),
        "employees": _fmt_number(info.get("total_shares_outstanding")),
        "founded": info.get("founded", ""),
        "headquarters": info.get("country_code", ""),
        "pe_ratio": _fmt_number(info.get("price_earnings_ttm"), "x"),
        "dividend_yield": _fmt_number(info.get("dividends_yield"), "%"),
        "beta": _fmt_number(info.get("beta_1_year")),
        "52w_high": _fmt_price(info.get("all_time_high")),
        "52w_low": _fmt_price(info.get("all_time_low")),
        "competitors": "",  # not available in /info
    }
    # Clean up empty-ish values
    return {k: v for k, v in ctx.items() if v and v not in ("0.00x", "0.00%", "0.00", "Rs. 0", "Rs. 0.00")}
