"""Fundamental data: yfinance key metrics + recent SEC filings (EDGAR).

yfinance needs no key; SEC needs only a descriptive User-Agent (set in .env).
Both degrade to notes on failure.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from functools import lru_cache

from ..config import get_config
from ..schemas.fundamentals import Filing, FundamentalData
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
