"""Fundamental data: yfinance key metrics + recent SEC filings (EDGAR).

yfinance needs no key; SEC needs only a descriptive User-Agent (set in .env).
Both degrade to notes on failure.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from functools import lru_cache

from ..config import get_config
from ..schemas.fundamentals import Filing, FinancialStatements, FundamentalData, StatementMetric
from .base import safe_fetch

name = "fundamentals"

_METRIC_KEYS = {
    "market_cap": "marketCap",
    "trailing_pe": "trailingPE",
    "forward_pe": "forwardPE",
    "price_to_sales": "priceToSalesTrailing12Months",
    "profit_margin": "profitMargins",
    "revenue_growth": "revenueGrowth",
    "earnings_growth": "earningsGrowth",
    "free_cashflow": "freeCashflow",
    "dividend_yield": "dividendYield",
}
_FORMS = {"10-K", "10-Q", "8-K"}


def fetch(symbol: str) -> FundamentalData:
    data = FundamentalData(symbol=symbol, as_of=datetime.now(timezone.utc))

    info = safe_fetch(lambda: _yf_info(symbol), source=f"yf-info:{symbol}")
    if info is None:
        data.notes.append("yfinance fundamentals unavailable")
    else:
        for field, key in _METRIC_KEYS.items():
            val = info.get(key)
            if isinstance(val, (int, float)):
                setattr(data, field, float(val))

    data.statements = safe_fetch(lambda: _statements(symbol), source=f"yf-stmt:{symbol}")
    if data.statements is None:
        data.notes.append("quarterly statements unavailable")

    filings = safe_fetch(lambda: _sec_filings(symbol), source=f"sec:{symbol}", attempts=2)
    if filings:
        data.recent_filings = filings
    elif filings is None:
        data.notes.append("SEC filings unavailable")
    return data


def _yf_info(symbol: str) -> dict:
    import yfinance as yf

    info = yf.Ticker(symbol).get_info()
    if not info:
        raise ValueError(f"no info for {symbol}")
    return info


_LIGHT_KEYS = {"market_cap": "marketCap", "pe": "trailingPE", "fwd_pe": "forwardPE",
               "gross_margin": "grossMargins", "op_margin": "operatingMargins",
               "rev_growth": "revenueGrowth", "beta": "beta"}


def fetch_light(symbol: str) -> dict:
    """One-call valuation/margin/beta snapshot for wide-universe scans.
    Returns {market_cap, pe, fwd_pe, gross_margin, op_margin, rev_growth, beta} (None-filled).
    Never raises."""
    out: dict = {k: None for k in _LIGHT_KEYS}
    info = safe_fetch(lambda: _yf_info(symbol), source=f"yf-light:{symbol}", attempts=2)
    if info:
        for field, key in _LIGHT_KEYS.items():
            val = info.get(key)
            if isinstance(val, (int, float)):
                out[field] = float(val)
    return out


# --------------------------------------------------------------------------- #
# Quarterly statements (income / balance / cash flow) with QoQ + YoY
# --------------------------------------------------------------------------- #
def _row(df, *candidates):
    """Latest, prior-quarter, and year-ago values for the first matching row."""
    if df is None or df.empty:
        return None, None, None
    for name in candidates:
        if name in df.index:
            cols = list(df.columns)  # descending: col0=latest
            vals = [df.loc[name, c] for c in cols]
            cur = _num(vals[0]) if len(vals) > 0 else None
            qoq = _num(vals[1]) if len(vals) > 1 else None
            yoy = _num(vals[4]) if len(vals) > 4 else None
            return cur, qoq, yoy
    return None, None, None


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _pct(cur, base):
    if cur is None or not base:
        return None
    if (cur < 0) != (base < 0):   # sign flip -> percentage change is not meaningful
        return None
    return round((cur / base - 1) * 100, 1)


def _dollar_metric(label, cur, prev, yago):
    return StatementMetric(label=label, value=round(cur / 1e6, 0) if cur is not None else None,
                           qoq=_pct(cur, prev), yoy=_pct(cur, yago), unit="$M", delta_unit="%")


def _statements(symbol: str) -> FinancialStatements:
    import yfinance as yf

    t = yf.Ticker(symbol)
    inc, bs, cf = t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow
    if inc is None or inc.empty:
        raise ValueError(f"no quarterly statements for {symbol}")

    period = str(inc.columns[0])[:10]
    rev = _row(inc, "Total Revenue", "Operating Revenue")
    gp = _row(inc, "Gross Profit")
    op = _row(inc, "Operating Income", "Operating Income Or Loss")
    ni = _row(inc, "Net Income", "Net Income Common Stockholders")
    eps = _row(inc, "Diluted EPS", "Basic EPS")
    capex = _row(cf, "Capital Expenditure", "Capital Expenditures")
    fcf = _row(cf, "Free Cash Flow")
    debt = _row(bs, "Total Debt")

    lines = [_dollar_metric("Revenue", *rev)]
    lines.append(_margin("Gross Margin", gp, rev))
    lines.append(_margin("Operating Margin", op, rev))
    lines.append(_dollar_metric("Net Income", *ni))
    if eps[0] is not None:
        lines.append(StatementMetric(label="Diluted EPS", value=round(eps[0], 2),
                                     qoq=_pct(eps[0], eps[1]), yoy=_pct(eps[0], eps[2]), unit="$"))
    lines.append(_dollar_metric("CapEx", *capex))
    lines.append(_dollar_metric("Free Cash Flow", *fcf))
    lines.append(_dollar_metric("Total Debt", *debt))
    return FinancialStatements(period=period, lines=[ln for ln in lines if ln.value is not None])


def _margin(label, profit, rev):
    """Margin (%) with QoQ/YoY as percentage-point deltas."""
    def m(p, r):
        return round(p / r * 100, 1) if (p is not None and r) else None

    cur, qoq_v, yoy_v = m(profit[0], rev[0]), m(profit[1], rev[1]), m(profit[2], rev[2])
    return StatementMetric(label=label, value=cur,
                           qoq=round(cur - qoq_v, 1) if (cur is not None and qoq_v is not None) else None,
                           yoy=round(cur - yoy_v, 1) if (cur is not None and yoy_v is not None) else None,
                           unit="%", delta_unit="pp")


# --- SEC EDGAR -------------------------------------------------------------- #
def _headers() -> dict:
    return {"User-Agent": get_config().secrets.sec_edgar_user_agent,
            "Accept-Encoding": "gzip, deflate"}


@lru_cache(maxsize=1)
def _ticker_to_cik() -> dict[str, str]:
    import httpx

    r = httpx.get("https://www.sec.gov/files/company_tickers.json", headers=_headers(), timeout=20)
    r.raise_for_status()
    return {row["ticker"].upper(): f"{int(row['cik_str']):010d}" for row in r.json().values()}


def _sec_filings(symbol: str, limit: int = 5) -> list[Filing]:
    import httpx

    cik = _ticker_to_cik().get(symbol.upper())
    if not cik:
        return []
    r = httpx.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=_headers(), timeout=20)
    r.raise_for_status()
    recent = r.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    out: list[Filing] = []
    for form, filed, accn, doc in zip(forms, dates, accns, docs):
        if form not in _FORMS:
            continue
        accn_nodash = accn.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/{doc}"
        out.append(Filing(form=form, filed=date.fromisoformat(filed), url=url))
        if len(out) >= limit:
            break
    return out
