"""Taiwan Ministry of Finance monthly export levels.

The structured adapter returns official source-native levels and a reproducible CSV
artifact. YoY/MoM exist only in the legacy compatibility wrapper and are calculated by
the shared derivation engine, never synthesized by the provider parser.
"""

from __future__ import annotations

import calendar
import csv
from datetime import datetime, timezone
import io
import logging
import re

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


log = logging.getLogger("ats.data.sources.tw_mof")

DATASET = "https://data.gov.tw/api/v2/rest/dataset/8380"
HEADERS = {"User-Agent": "ats-research-data/1.0 (contact configured by operator)"}
ROC_OFFSET = 1911
COLUMN_KEYWORDS = {"electronic_components": "電子零組件", "total": "總計"}
METRIC_ID = "regional.tw_ic_exports.value"
_MONTH = re.compile(r"^(\d{2,3})年\s*(\d{1,2})月$")


def _column(header: list[str], keyword: str) -> int:
    for index, name in enumerate(header):
        if keyword in name:
            return index
    raise LookupError(f"column {keyword!r} not found in the MoF export table")


def _distribution(metadata: dict) -> dict:
    distributions = metadata.get("result", {}).get("distribution", [])
    if not distributions:
        raise ValueError("Taiwan MoF dataset metadata has no distribution")
    return distributions[0]


def _resource_url(metadata: dict) -> str:
    url = _distribution(metadata).get("resourceDownloadUrl", "")
    if not url:
        raise ValueError("Taiwan MoF distribution has no resourceDownloadUrl")
    return str(url)


def _source_version(metadata: dict) -> str:
    distribution = _distribution(metadata)
    for body in (distribution, metadata.get("result", {})):
        for key in ("resourceModified", "modified", "metadataModified", "issued"):
            if body.get(key):
                return str(body[key])
    return ""


def parse_csv(raw: bytes | str, *, item: str = "electronic_components") -> list[NativeRecord]:
    """Parse source rows deterministically; malformed numeric rows are not fabricated."""
    text = (raw.decode("utf-8-sig", "strict") if isinstance(raw, bytes)
            else raw.lstrip("\ufeff"))
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    keyword = COLUMN_KEYWORDS.get(item, item)
    column = _column(rows[0], keyword)
    records = []
    for row in rows[1:]:
        if not row or len(row) <= column:
            continue
        match = _MONTH.match(row[0].strip())
        if not match:
            continue
        year = int(match.group(1)) + ROC_OFFSET
        month = int(match.group(2))
        try:
            value = float(row[column].replace(",", "").strip())
        except ValueError:
            continue
        period = f"{year:04d}-{month:02d}"
        records.append(NativeRecord(
            entity_id="TW_IC_EXPORT", provider_field=METRIC_ID, period=period,
            value=value, unit="百万美元", currency="USD", period_basis="month",
            period_start=f"{period}-01",
            period_end=f"{period}-{calendar.monthrange(year, month)[1]:02d}",
            raw={"roc_period": row[0].strip(), "column": rows[0][column],
                 "source_value": row[column]}))
    return sorted(records, key=lambda record: record.period)


class TaiwanMOFAdapter:
    source_id = "tw_mof_exports"
    dataset_id = "regional_tw_exports"

    def __init__(self, *, client=None, clock=None):
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        import httpx

        client = self.client or httpx
        fetched_at = self.clock().astimezone(timezone.utc)
        metadata = client.get(DATASET, headers=HEADERS, timeout=30).json()
        url = _resource_url(metadata)
        response = client.get(
            url, headers=HEADERS, timeout=60, follow_redirects=True)
        raw = response.content
        item = str(request.query_scope.get("item", "electronic_components"))
        records = parse_csv(raw, item=item)
        requested_periods = set(request.periods)
        if requested_periods:
            records = [record for record in records if record.period in requested_periods]
        lookback = int(request.query_scope.get("lookback_months", 0) or 0)
        if lookback:
            records = records[-lookback:]
        version = _source_version(metadata)
        coverage = {
            "first_period": records[0].period if records else "",
            "last_period": records[-1].period if records else "",
            "period_count": len(records),
            "publication_time_status": "not_supplied_by_dataset_endpoint",
        }
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH,
            fetched_at=fetched_at, records=records,
            artifacts=[AdapterArtifact(
                payload=raw, query_scope={**request.query_scope, "periods": request.periods},
                source_url=url, source_version=version, media_type="text/csv",
                retention="full_response", metadata={
                    "catalog_url": DATASET, "coverage": coverage,
                    "distribution": _distribution(metadata),
                })],
            provider_metadata={"source_version": version, "coverage": coverage})


def _legacy_points(records: list[NativeRecord], lookback_months: int) -> list[SeriesPoint]:
    rows = [record.model_dump(mode="json") | {
        "metric_id": METRIC_ID, "source_id": "tw_mof_exports",
        "dataset_id": "regional_tw_exports", "observation_id": "",
        "adjustment": "", "dimensions_json": "{}",
    } for record in records]
    yoy = {row["period"]: row for row in calculate(rows, DerivationDefinition(
        id="yoy:regional.tw_ic_exports.value", version="v1", operation="yoy",
        inputs=[METRIC_ID], output_metric_id=f"{METRIC_ID}.yoy"))}
    mom = {row["period"]: row for row in calculate(rows, DerivationDefinition(
        id="mom:regional.tw_ic_exports.value", version="v1", operation="mom",
        inputs=[METRIC_ID], output_metric_id=f"{METRIC_ID}.mom"))}
    return [SeriesPoint(
        period=record.period, value=float(record.value), unit=record.unit,
        yoy=yoy[record.period]["value"], mom=mom[record.period]["value"],
        published_at=record.published_at.date() if record.published_at else None)
        for record in records[-lookback_months:]]


def fetch(*, lookback_months: int = 6, item: str = "electronic_components",
          **_) -> list[SeriesPoint]:
    """Legacy Chain contract assembled from raw levels plus shared derivations."""
    requested = lookback_months + 12
    batch = TaiwanMOFAdapter().fetch(FetchRequest(
        source_id="tw_mof_exports", dataset_id="regional_tw_exports",
        entities=["TW_IC_EXPORT"],
        query_scope={"lookback_months": requested, "item": item}))
    points = _legacy_points(batch.records, lookback_months)
    log.info("tw_mof: %d monthly points, latest %s", len(points),
             points[-1].period if points else "n/a")
    return points
