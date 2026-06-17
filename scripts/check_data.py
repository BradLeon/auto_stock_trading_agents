"""Probe each PEAD data source and print a status line.

    PYTHONPATH=src .venv/bin/python scripts/check_data.py            # all sources
    PYTHONPATH=src .venv/bin/python scripts/check_data.py news COHR  # one source

Each line: ✓/✗ · source · what it needs · a sample of what came back. Sources are
fetched live (network); analysis outputs land in Context Memory (var/ats.sqlite),
raw fetches are not separately cached.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from ats.data import (
    consensus,
    earnings_calendar,
    fundamentals,
    macro,
    market_data,
    news,
    options,
    runup,
    transcript,
)
from ats.schemas.market import Ticker

SYM = sys.argv[2] if len(sys.argv) > 2 else "COHR"
NOW = datetime.now(timezone.utc)


def market():
    s = market_data.fetch_snapshot(Ticker(symbol=SYM))
    return bool(s.last_price), f"last={s.last_price} bars={len(s.history)} ind={len(s.indicators)}"


def fund():
    f = fundamentals.fetch(SYM)
    return bool(f.market_cap or f.trailing_pe), \
        f"P/E={f.trailing_pe} margin={f.profit_margin} filings={len(f.recent_filings)}"


def macro_():
    m = macro.fetch()
    return any([m.ust_10y, m.vix, m.spx]), \
        f"UST10Y={m.ust_10y} CPI={m.cpi_yoy} VIX={m.vix} SPX={m.spx} F&G={m.fear_greed}"


def opts():
    ed = earnings_calendar.next_earnings_date(SYM)
    o = options.fetch(SYM, ed)
    return bool(o.get("expected_move_pct")), \
        f"src={o.get('source')} EM={o.get('expected_move_pct')}% IV={o.get('atm_iv')}% " \
        f"skew={o.get('iv_skew')} exp={o.get('expiration')}"


def earn():
    d = earnings_calendar.next_earnings_date(SYM)
    return bool(d), f"next_earnings={d}"


def cons():
    c = consensus.fetch(SYM)
    return bool(c.get("eps") or c.get("revenue")), f"EPS={c.get('eps')} Rev={c.get('revenue')}"


def run():
    r = runup.compute(SYM)
    return r.get("pre_earnings_close") is not None, \
        f"close={r.get('pre_earnings_close')} vsSMH={r.get('run_up_vs_sector_pct')}% " \
        f"vsQQQ={r.get('run_up_vs_bench_pct')}% distATH={r.get('dist_to_ath_pct')}%"


def news_():
    items = news.fetch_news(SYM, NOW - timedelta(days=14))
    return len(items) > 0, f"{len(items)} items; latest: " + (items[0].headline[:60] if items else "-")


def trans():
    text, src = transcript.fetch(SYM, "Q FY2026")
    return bool(text), f"src={src} chars={len(text)}"


CHECKS = {
    "market": ("yfinance (no key)", market),
    "fundamentals": ("yfinance + SEC (SEC_EDGAR_USER_AGENT)", fund),
    "macro": ("FRED_API_KEY + yfinance", macro_),
    "options": ("ThetaData terminal / yfinance", opts),
    "earnings": ("yfinance/Finnhub", earn),
    "consensus": ("yfinance (no key)", cons),
    "runup": ("yfinance (no key)", run),
    "news": ("FINNHUB_API_KEY + RSS", news_),
    "transcript": ("Tavily/FMP/manual", trans),
}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    items = {only: CHECKS[only]} if only in CHECKS else CHECKS
    print(f"Testing data sources for {SYM}:\n")
    for name, (needs, fn) in items.items():
        try:
            ok, detail = fn()
            mark = "✓" if ok else "✗"
        except Exception as exc:  # noqa: BLE001
            mark, detail = "✗", f"ERROR: {exc}"
        print(f"  {mark} {name:13} [{needs}]\n      {detail}")


if __name__ == "__main__":
    main()
