"""Bank of Korea ECOS monthly semiconductor export-value index levels.

The adapter persists ECOS-native index levels and page responses. YoY/MoM are shared,
versioned query-time derivations; the legacy Chain wrapper merely renders them back to
``SeriesPoint`` while consumers migrate.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
import logging

from ...schemas.chain import SeriesPoint
from ...structured import (
    AdapterArtifact,
    AdapterBatch,
    DerivationDefinition,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
)
from ...structured.derivations import calculate


log = logging.getLogger("ats.data.sources.kr_ecos")

BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
HEADERS = {"User-Agent": "ats-research-data/1.0 (contact configured by operator)"}
PAGE = 10
METRIC_ID = "regional.kr_semiconductor_exports.index"


def _months_back(n: int, *, today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    total = today.year * 12 + (today.month - 1) - n
    return f"{total // 12}{total % 12 + 1:02d}", f"{today.year}{today.month:02d}"


def _period_bounds(request: FetchRequest, fetched_at: datetime) -> tuple[str, str]:
    if request.periods:
        normalized = sorted(period.replace("-", "") for period in request.periods)
        return normalized[0], normalized[-1]
    lookback = int(request.query_scope.get("lookback_months", 19))
    return _months_back(lookback, today=fetched_at.date())


def _month_chunks(start: str, end: str, size: int = 10) -> list[tuple[str, str]]:
    """Inclusive YYYYMM slices, used to respect the sample key's per-call row cap."""
    start_index = int(start[:4]) * 12 + int(start[4:]) - 1
    end_index = int(end[:4]) * 12 + int(end[4:]) - 1
    chunks = []
    cursor = start_index
    while cursor <= end_index:
        stop = min(end_index, cursor + size - 1)
        chunks.append((
            f"{cursor // 12:04d}{cursor % 12 + 1:02d}",
            f"{stop // 12:04d}{stop % 12 + 1:02d}"))
        cursor = stop + 1
    return chunks


def parse_rows(rows: list[dict]) -> list[NativeRecord]:
    records = []
    for row in rows:
        period_value = str(row.get("TIME", ""))
        if len(period_value) != 6 or not period_value.isdigit():
            continue
        year, month = int(period_value[:4]), int(period_value[4:])
        try:
            value = float(row["DATA_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
        period = f"{year:04d}-{month:02d}"
        records.append(NativeRecord(
            entity_id="KR_SEMI_EXPORT", provider_field=METRIC_ID, period=period,
            value=value, unit=str(row.get("UNIT_NAME", "index") or "index"),
            currency="", period_basis="month", period_start=f"{period}-01",
            period_end=f"{period}-{calendar.monthrange(year, month)[1]:02d}",
            raw=dict(row)))
    by_period = {record.period: record for record in records}
    return [by_period[key] for key in sorted(by_period)]


class KoreaECOSAdapter:
    source_id = "kr_ecos_exports"
    dataset_id = "regional_kr_exports"

    def __init__(self, *, client=None, api_key: str | None = None, clock=None):
        self.client = client
        self.api_key = api_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _key(self) -> str:
        if self.api_key is not None:
            return self.api_key
        from ...config import get_config

        return getattr(get_config().secrets, "kr_ecos_api_key", "") or "sample"

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        import httpx

        client = self.client or httpx
        fetched_at = self.clock().astimezone(timezone.utc)
        start, end = _period_bounds(request, fetched_at)
        stat = str(request.query_scope.get("stat", "403Y001"))
        item = str(request.query_scope.get("item", "3091AA"))
        page_size = int(request.query_scope.get("page_size", PAGE))
        all_rows: list[dict] = []
        pages: list[dict] = []
        total = 0
        key = self._key()
        slices = _month_chunks(start, end, PAGE) if key == "sample" else [(start, end)]
        for slice_start, slice_end in slices:
            offset = 0
            slice_total = None
            while slice_total is None or offset < slice_total:
                url = (f"{BASE}/{key}/json/kr/{offset + 1}/{offset + page_size}/"
                       f"{stat}/M/{slice_start}/{slice_end}/{item}")
                body = client.get(url, headers=HEADERS, timeout=30).json()
                pages.append(body)
                payload = body.get("StatisticSearch", {})
                rows = payload.get("row", []) or []
                if slice_total is None:
                    slice_total = int(payload.get("list_total_count", len(rows)) or len(rows))
                    total += slice_total
                all_rows.extend(rows)
                if not rows or len(rows) < page_size or key == "sample":
                    break
                offset += page_size
        records = parse_rows(all_rows)
        requested_periods = set(request.periods)
        if requested_periods:
            records = [record for record in records if record.period in requested_periods]
        version = f"{stat}:{item}:{start}:{end}:{fetched_at.isoformat()}"
        coverage = {
            "requested_start": f"{start[:4]}-{start[4:]}",
            "requested_end": f"{end[:4]}-{end[4:]}",
            "first_period": records[0].period if records else "",
            "last_period": records[-1].period if records else "",
            "period_count": len(records),
            "reported_total_count": total,
            "publication_time_status": "not_supplied_by_statistic_search",
        }
        source_url = f"{BASE}/<redacted>/json/kr/1/{page_size}/{stat}/M/{start}/{end}/{item}"
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH,
            fetched_at=fetched_at, records=records,
            artifacts=[AdapterArtifact(
                payload={"pages": pages}, query_scope={
                    **request.query_scope, "periods": request.periods,
                    "start": start, "end": end},
                source_url=source_url, source_version=version,
                media_type="application/json", retention="query_slice",
                metadata={"coverage": coverage, "stat": stat, "item": item})],
            provider_metadata={"source_version": version, "coverage": coverage})


def _legacy_points(records: list[NativeRecord], lookback_months: int) -> list[SeriesPoint]:
    rows = [record.model_dump(mode="json") | {
        "metric_id": METRIC_ID, "source_id": "kr_ecos_exports",
        "dataset_id": "regional_kr_exports", "observation_id": "",
        "adjustment": "", "dimensions_json": "{}",
    } for record in records]
    yoy = {row["period"]: row for row in calculate(rows, DerivationDefinition(
        id="yoy:regional.kr_semiconductor_exports.index", version="v1",
        operation="yoy", inputs=[METRIC_ID], output_metric_id=f"{METRIC_ID}.yoy"))}
    mom = {row["period"]: row for row in calculate(rows, DerivationDefinition(
        id="mom:regional.kr_semiconductor_exports.index", version="v1",
        operation="mom", inputs=[METRIC_ID], output_metric_id=f"{METRIC_ID}.mom"))}
    return [SeriesPoint(
        period=record.period, value=float(record.value), unit=record.unit,
        yoy=yoy[record.period]["value"], mom=mom[record.period]["value"],
        published_at=record.published_at.date() if record.published_at else None)
        for record in records[-lookback_months:]]


def fetch(*, lookback_months: int = 6, stat: str = "403Y001", item: str = "3091AA",
          **_) -> list[SeriesPoint]:
    requested = lookback_months + 12
    batch = KoreaECOSAdapter().fetch(FetchRequest(
        source_id="kr_ecos_exports", dataset_id="regional_kr_exports",
        entities=["KR_SEMI_EXPORT"], query_scope={
            "lookback_months": requested, "stat": stat, "item": item}))
    points = _legacy_points(batch.records, lookback_months)
    log.info("kr_ecos: %d monthly points, latest %s", len(points),
             points[-1].period if points else "n/a")
    return points
