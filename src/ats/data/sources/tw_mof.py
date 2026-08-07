"""Taiwan Ministry of Finance — monthly exports by commodity.

The `電子零組件` (electronic components) line covers TSMC, Nanya and the packaging
chain. Two properties make it worth more than another company's account of itself:
the MoF has no position in the trade, and it publishes monthly rather than quarterly —
the June 2026 print was available on 2026-08-07, while several witnesses' latest
filings were still from Q2.

Keyless: the dataset (data.gov.tw #8380) resolves to a CSV on web02.mof.gov.tw. Values
are millions of USD; years are on the ROC calendar (民國 115 = 2026).
"""

from __future__ import annotations

import csv
import io
import logging
import re

from ...schemas.chain import SeriesPoint

log = logging.getLogger("ats.data.sources.tw_mof")

DATASET = "https://data.gov.tw/api/v2/rest/dataset/8380"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ats-bot)"}
ROC_OFFSET = 1911
# The CSV has no stable column index — the ministry adds commodity lines over time — so
# the column is located by its Chinese label each run rather than pinned to a position.
COLUMN_KEYWORDS = {"electronic_components": "電子零組件", "total": "總計"}
_MONTH = re.compile(r"^(\d{2,3})年\s*(\d{1,2})月$")


def _resource_url() -> str:
    import httpx

    meta = httpx.get(DATASET, headers=HEADERS, timeout=30).json()
    return meta["result"]["distribution"][0]["resourceDownloadUrl"]


def _column(header: list[str], keyword: str) -> int:
    for i, name in enumerate(header):
        if keyword in name:
            return i
    raise LookupError(f"column {keyword!r} not found in the MoF export table")


def fetch(*, lookback_months: int = 6, item: str = "electronic_components",
          **_) -> list[SeriesPoint]:
    """Monthly export value, newest last. Raises on failure — chain.sources.fetch
    converts that into a recorded gap, which is what an agency outage is."""
    import httpx

    keyword = COLUMN_KEYWORDS.get(item, item)
    raw = httpx.get(_resource_url(), headers=HEADERS, timeout=60,
                    follow_redirects=True).content.decode("utf-8-sig", "ignore")
    rows = list(csv.reader(io.StringIO(raw)))
    if not rows:
        return []
    col = _column(rows[0], keyword)

    monthly: list[tuple[str, float]] = []
    for row in rows[1:]:
        if not row or len(row) <= col:
            continue
        m = _MONTH.match(row[0].strip())
        if not m:
            continue          # skips annual totals and "115年 (1~6月)" cumulative lines
        try:
            value = float(row[col])
        except ValueError:
            continue
        monthly.append((f"{int(m.group(1)) + ROC_OFFSET}-{int(m.group(2)):02d}", value))

    by_period = dict(monthly)
    out: list[SeriesPoint] = []
    for period, value in monthly[-lookback_months:]:
        year, month = (int(p) for p in period.split("-"))
        prev_m = f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
        prev_y = f"{year - 1}-{month:02d}"
        out.append(SeriesPoint(
            period=period, value=value, unit="百万美元",
            yoy=_change(value, by_period.get(prev_y)),
            mom=_change(value, by_period.get(prev_m))))
    log.info("tw_mof: %d monthly points, latest %s", len(out),
             out[-1].period if out else "n/a")
    return out


def _change(now: float, before: float | None) -> float | None:
    if not before:
        return None
    return (now - before) / before
