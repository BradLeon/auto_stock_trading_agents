"""Analyst consensus (EPS / revenue) for the upcoming quarter — yfinance, free.

Returns a dict {eps, revenue, eps_low, eps_high, revenue_low, revenue_high} with
None for anything unavailable. Never raises.
"""

from __future__ import annotations

from .base import safe_fetch

name = "consensus"


def _num(x):
    try:
        v = float(x)
        return v if v == v else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _yf_consensus(symbol: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(symbol)
    out: dict = {"eps": None, "revenue": None, "eps_low": None, "eps_high": None,
                 "revenue_low": None, "revenue_high": None}

    # earnings_estimate / revenue_estimate: rows '0q' (current quarter) etc., col 'avg'.
    ee = getattr(t, "earnings_estimate", None)
    if ee is not None and hasattr(ee, "index") and "0q" in ee.index and "avg" in ee.columns:
        out["eps"] = _num(ee.loc["0q", "avg"])
        if "low" in ee.columns:
            out["eps_low"] = _num(ee.loc["0q", "low"])
        if "high" in ee.columns:
            out["eps_high"] = _num(ee.loc["0q", "high"])

    re = getattr(t, "revenue_estimate", None)
    if re is not None and hasattr(re, "index") and "0q" in re.index and "avg" in re.columns:
        out["revenue"] = _num(re.loc["0q", "avg"])
        if "low" in re.columns:
            out["revenue_low"] = _num(re.loc["0q", "low"])
        if "high" in re.columns:
            out["revenue_high"] = _num(re.loc["0q", "high"])

    # Fallback to the calendar dict for EPS/revenue averages.
    cal = getattr(t, "calendar", None)
    if isinstance(cal, dict):
        if out["eps"] is None:
            out["eps"] = _num(cal.get("EPS Estimate"))
        if out["revenue"] is None:
            out["revenue"] = _num(cal.get("Revenue Estimate"))

    if out["eps"] is None and out["revenue"] is None:
        raise ValueError(f"no consensus for {symbol}")
    return out


def fetch(symbol: str) -> dict:
    return safe_fetch(lambda: _yf_consensus(symbol), source=f"{name}:{symbol}") or {
        "eps": None, "revenue": None}
