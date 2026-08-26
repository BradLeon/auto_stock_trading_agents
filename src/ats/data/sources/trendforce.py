"""TrendForce — DRAM contract prices, from the public price-trends table.

The only independent reading available on `hbm_pricing`. Everything else on that
dimension is a memory maker describing its own pricing power, and three vendors each
saying their ASP rose is three interested parties, not corroboration.

Why DRAM contract price is on-mechanism for an HBM claim, despite not being HBM: HBM
consumes roughly three times the wafer per bit of conventional DRAM, so an HBM ramp
crowds conventional DRAM capacity out and pushes its price up. SK hynix said exactly
this on its Q2 FY2026 call ("conventional DRAM prices rising sharply in recent
months"). A rising conventional contract price is evidence the crowding-out is real;
a falling one, with HBM still ramping, would be evidence against it.

Publicly readable — the paywall covers the downloadable detail, not this summary table.
The published figures lag roughly a month (the page carried 2H Jun on 2026-08-07),
which is still far more frequent than a quarterly call. Fetched monthly.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from ...schemas.chain import SeriesPoint
from ...structured import (
    AdapterArtifact,
    AdapterBatch,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
)

log = logging.getLogger("ats.data.sources.trendforce")

URL = "https://www.trendforce.com/price/dram/dram_contract"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
# ▲ / ▼ arrive as HTML entities; a dash means unchanged.
_UP, _DOWN = "&#9650;", "&#9660;"
_PCT = re.compile(r"([\d.]+)\s*%")
_SESSION = re.compile(r"DRAM Contract Price\s*\(([^)]+)\)")
_UPDATED = re.compile(r"Last Update\s*(\d{4}-\d{2}-\d{2})")
# Default basket: mainstream server/PC parts. Deliberately not the whole table — the
# eTT and legacy DDR3 lines move on different end markets and would add noise, which
# is the same reason a witness gets excluded for having no discriminating read.
DEFAULT_ITEMS = ("DDR5 8GB SO-DIMM", "DDR4 16Gb 2Gx8", "DDR4 8Gb 1Gx8")


def _cells(row: str) -> list[str]:
    out = []
    for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S):
        text = re.sub(r"<[^>]+>", "", cell)
        out.append(re.sub(r"\s+", " ", text).strip())
    return out


def _change(cell: str) -> float | None:
    """Signed fractional change from a `▲ 2.68 %` cell. None when unparseable."""
    m = _PCT.search(cell)
    if not m:
        return None
    value = float(m.group(1)) / 100
    if _DOWN in cell or "▼" in cell:
        return -value
    if _UP in cell or "▲" in cell:
        return value
    return 0.0                       # an em-dash row: published as unchanged


def parse_html(html: str, *, items: tuple[str, ...] = DEFAULT_ITEMS) -> tuple[str, str, list[SeriesPoint]]:
    """Parse one public table snapshot without network access."""
    session = (_SESSION.search(html).group(1).strip() if _SESSION.search(html)
               else "latest")
    updated = _UPDATED.search(html)
    wanted = {i.lower() for i in items}

    out: list[SeriesPoint] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [c for c in _cells(row) if c]
        if len(cells) < 5 or cells[0].lower() not in wanted:
            continue
        # Contract rows are [item, high, low, average, avg change, low change]; the
        # spot table on the same page has six numeric columns and no `%`, so requiring
        # a parseable percentage is what tells them apart.
        change = _change(cells[4])
        if change is None:
            continue
        try:
            average = float(cells[3])
        except ValueError:
            continue
        out.append(SeriesPoint(period=session, series=cells[0], value=average,
                               unit="USD", mom=change))
    log.info("trendforce: session %s, %d items", session, len(out))
    if updated and not out:
        log.warning("trendforce: page parsed but no tracked item matched %s", items)
    return session, updated.group(1) if updated else "", out


def fetch(*, lookback_months: int = 6, items: tuple[str, ...] = DEFAULT_ITEMS,
          **_) -> list[SeriesPoint]:
    """One point per tracked item for the latest published session.

    `lookback_months` is ignored: the public table shows only the current session. The
    change TrendForce publishes is against the prior session, so `mom` comes straight
    from the page rather than being derived — we never see the history to derive it
    from, and inventing one from a single snapshot would be fabrication.
    """
    import httpx

    html = httpx.get(URL, headers=HEADERS, timeout=30, follow_redirects=True).text
    return parse_html(html, items=items)[2]


class TrendForceDRAMAdapter:
    source_id = "trendforce_dram"
    dataset_id = "industry_dram_contract_price"

    def __init__(self, *, client=None, clock=None):
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        import httpx

        client = self.client or httpx
        fetched_at = self.clock().astimezone(timezone.utc)
        response = client.get(URL, headers=HEADERS, timeout=30, follow_redirects=True)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        html = response.text
        items = tuple(request.query_scope.get("items") or DEFAULT_ITEMS)
        session, updated, points = parse_html(html, items=items)
        published = None
        if updated:
            published = datetime.strptime(updated, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        records = [NativeRecord(
            entity_id="DRAM_CONTRACT_PRICE",
            provider_field="industry.dram_contract_price",
            period=point.period,
            value=point.value,
            unit="USD/item",
            currency="USD",
            period_basis="published_session",
            published_at=published,
            dimensions={"item": point.series},
            raw={"item": point.series, "published_mom": point.mom,
                 "source_unit": point.unit},
        ) for point in points]
        headers = getattr(response, "headers", {}) or {}
        version = str(headers.get("etag") or headers.get("last-modified") or updated)
        return AdapterBatch(
            source_id=request.source_id,
            dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH,
            fetched_at=fetched_at,
            records=records,
            artifacts=[AdapterArtifact(
                payload=html, query_scope={**request.query_scope, "items": list(items)},
                source_url=URL, source_version=version, media_type="text/html",
                retention="constrained_snapshot",
                metadata={"session": session, "updated": updated})],
            provider_metadata={"session": session, "updated": updated,
                               "source_version": version})
