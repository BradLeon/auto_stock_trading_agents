"""GPU rental list prices: GetDeploying's weekly dataset of listed prices.

The file holds one row per GPU model, week, billing type and reservation term,
for up to 53 trailing weeks.  For the configured models the on-demand rows are the
reading; spot and 12-month reservation rows are kept as corroboration.  Each rental
form is a different price from a different set of providers and is its own series.

A bad on-demand row, or a file whose shape changed, rejects the whole file.  A bad
value in a corroboration row quarantines that row only, so it cannot block the reading.

The stored value is `provider_median_price`: each provider's own median, then the
median across providers.  These are advertised prices, not executed rentals, and
the median moves when providers join or leave, so the provider count is kept
beside every value.  The newest week stays open and may change until it ends.

Data: GetDeploying, https://getdeploying.com/dataset/gpu-prices, Creative Commons
Attribution 4.0.  The licence covers the published dataset files only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..catalog.structured import StructuredCatalog
from ..core.structured_models import (
    AdapterArtifact,
    AdapterBatch,
    AdapterFailure,
    DiscoveryResult,
    DiscoveryStatus,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
    ReleaseCandidate,
    RevisionRule,
)

CSV_URL = "https://getdeploying.com/dataset/gpu-prices/weekly.csv"
DATASET_PAGE = "https://getdeploying.com/dataset/gpu-prices"
SOURCE_ID = "getdeploying_gpu_prices"
DATASET_ID = "gpu_rental_price_weekly"
PARSER_VERSION = "getdeploying_gpu_prices/v1"
METHODOLOGY_REGIME = "getdeploying_listed_weekly_median/v1"
ATTRIBUTION = f"GetDeploying GPU price dataset, {DATASET_PAGE}"
LICENSE = "CC BY 4.0"
UNIT = "USD_per_GPU_hour"
PRICE_FIELD = "provider_median_price"
RENTAL_FORM = "on_demand"
# Rental form -> the file's billing type and reservation term in months.
RENTAL_FORMS = {"on_demand": ("ON_DEMAND", ""), "spot": ("SPOT", ""),
                "reserved_12m": ("RESERVATION", "12")}
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024
# No GPU rents for this much per hour; a larger figure means the unit changed.
MAX_HOURLY_PRICE = 1000.0
REQUIRED_COLUMNS = ("gpu_slug", "date", "billing_type", "reservation_months", "min_price",
                    "max_price", "median_price", PRICE_FIELD, "provider_count", "offering_count")
# A weekly re-read repeats a year of weeks; only a changed reading is a new vintage.
REVISION_RULE = RevisionRule(match=["gpu_slug", "rental_form"])


class GpuPriceError(ValueError):
    """The weekly file cannot safely enter the governed store."""


def _now() -> datetime:
    return datetime.now(UTC)


def dataset_settings(catalog: StructuredCatalog | None = None) -> dict[str, Any]:
    """The declared scope of this dataset: models, rental form and trend thresholds."""
    catalog = catalog or StructuredCatalog.load()
    return dict(catalog.raw["datasets"][DATASET_ID]["quality"])


def _price(row: dict[str, str], field: str, *, where: str) -> float:
    try:
        number = float(row[field])
    except ValueError as exc:
        raise GpuPriceError(f"schema_drift:{field}_not_numeric:{where}") from exc
    if not math.isfinite(number) or not 0 < number <= MAX_HOURLY_PRICE:
        raise GpuPriceError(f"schema_drift:{field}_out_of_range:{where}")
    return number


def _count(row: dict[str, str], field: str, *, where: str) -> int:
    value = row[field].strip()
    if not value.isdigit() or int(value) < 1:
        raise GpuPriceError(f"schema_drift:{field}_not_positive_integer:{where}")
    return int(value)


def _rental_form(row: dict[str, str]) -> str | None:
    """The rental form a row prices, or None for a billing type or term that is not collected."""
    billing = (row.get("billing_type") or "").strip()
    term = (row.get("reservation_months") or "").strip()
    for form, (wanted_billing, wanted_term) in RENTAL_FORMS.items():
        # A term on an on-demand or spot row is a bad value, checked with the other values.
        if billing == wanted_billing and (not wanted_term or term == wanted_term):
            return form
    return None


def _reading(row: dict[str, str], *, form: str, where: str) -> tuple[float, dict[str, Any]]:
    """The checked price of one row and the figures kept beside it."""
    if not RENTAL_FORMS[form][1] and row["reservation_months"].strip():
        raise GpuPriceError(f"schema_drift:{form}_with_reservation_term:{where}")
    value = _price(row, PRICE_FIELD, where=where)
    low = _price(row, "min_price", where=where)
    high = _price(row, "max_price", where=where)
    offering_median = _price(row, "median_price", where=where)
    if not (low <= value <= high and low <= offering_median <= high):
        raise GpuPriceError(f"schema_drift:median_outside_range:{where}")
    providers = _count(row, "provider_count", where=where)
    offerings = _count(row, "offering_count", where=where)
    if providers > offerings:
        raise GpuPriceError(f"schema_drift:more_providers_than_offerings:{where}")
    return value, {"min_price": low, "max_price": high,
                   "offering_median_price": offering_median,
                   "provider_count": providers, "offering_count": offerings}


def parse_weekly_payload(payload: bytes, *, fetched_at: datetime, gpu_models: Iterable[str],
                         min_rows: int, corroboration_forms: Iterable[str] = ()
                         ) -> tuple[list[NativeRecord], dict[str, Any]]:
    """Normalise the configured models, one record per model, rental form and week."""
    wanted = tuple(gpu_models)
    supporting = tuple(corroboration_forms)
    unknown = [form for form in supporting if form not in RENTAL_FORMS or form == RENTAL_FORM]
    if unknown:
        raise GpuPriceError(f"corroboration_form_unsupported:{','.join(unknown)}")
    forms = (RENTAL_FORM, *supporting)
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GpuPriceError("invalid_encoding") from exc
    reader = csv.DictReader(io.StringIO(text))
    missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
    if missing:
        raise GpuPriceError(f"schema_drift:columns_missing:{','.join(missing)}")
    rows = list(reader)
    if len(rows) < min_rows:
        raise GpuPriceError(f"file_too_short:{len(rows)}")
    collected = fetched_at.astimezone(UTC).date()
    records: list[NativeRecord] = []
    quarantined: list[NativeRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        slug = (row.get("gpu_slug") or "").strip()
        form = _rental_form(row)
        if slug not in wanted or form not in forms:
            continue
        where = f"{slug}:{form}:{row.get('date')}"
        # The shape of the file: wrong here means it cannot be trusted for any form.
        if None in row.values() or None in row:
            raise GpuPriceError(f"schema_drift:row_incomplete:{where}")
        try:
            week = date.fromisoformat(row["date"].strip())
        except ValueError as exc:
            raise GpuPriceError(f"schema_drift:date_invalid:{where}") from exc
        if week.weekday() != 0 or week > collected:
            raise GpuPriceError(f"schema_drift:date_not_a_past_monday:{where}")
        if (slug, form, row["date"]) in seen:
            raise GpuPriceError(f"duplicate_week:{where}")
        seen.add((slug, form, row["date"]))
        try:
            value, beside = _reading(row, form=form, where=where)
            target = records
        except GpuPriceError as exc:
            if form == RENTAL_FORM:
                raise
            reason = str(exc).removeprefix("schema_drift:").removesuffix(f":{where}")
            value = row[PRICE_FIELD]
            beside = {"quarantine_reasons": [reason], "source_row": dict(row)}
            target = quarantined
        target.append(NativeRecord(
            entity_id=f"GPU_MODEL:{slug.upper()}", provider_field=PRICE_FIELD,
            period=week.isoformat(), period_start=week.isoformat(),
            period_end=date.fromordinal(week.toordinal() + 6).isoformat(),
            value=value, unit=UNIT, currency="USD", period_basis="calendar_week",
            dimensions={"gpu_slug": slug, "rental_form": form,
                        "methodology_regime": METHODOLOGY_REGIME},
            raw=beside, revision_rule=REVISION_RULE))
    primary = [r for r in records if r.dimensions["rental_form"] == RENTAL_FORM]
    found = {record.dimensions["gpu_slug"] for record in primary}
    absent = [slug for slug in wanted if slug not in found]
    if absent:
        # A renamed or dropped model must not turn into a silently shorter series.
        raise GpuPriceError(f"schema_drift:models_missing:{','.join(absent)}")
    by_form = {form: sum(r.dimensions["rental_form"] == form for r in records) for form in forms}
    combined = sorted(records + quarantined, key=lambda record: (
        record.entity_id, record.dimensions["rental_form"], record.period))
    diagnostics = {
        "file_rows": len(rows), "kept_rows": len(records), "gpu_models": list(wanted),
        "rental_form": RENTAL_FORM, "rows_by_form": by_form,
        # Corroboration may be missing for one model; a form gone altogether is stated.
        "forms_absent": [form for form, count in by_form.items() if not count],
        "quarantined_rows": len(quarantined),
        "first_week": min(r.period for r in primary),
        "latest_week": max(r.period for r in primary), "snapshot_date": collected.isoformat(),
        "source_url": CSV_URL, "parser_version": PARSER_VERSION, "license": LICENSE,
        "attribution": ATTRIBUTION,
    }
    return combined, diagnostics


class GetDeployingGpuPriceAdapter:
    source_id = SOURCE_ID
    dataset_id = DATASET_ID

    def __init__(self, *, opener: Callable[..., Any] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 gpu_models: Iterable[str] | None = None, min_rows: int | None = None,
                 corroboration_forms: Iterable[str] | None = None):
        self.opener = opener or urlopen
        self.clock = clock or _now
        declared = (gpu_models, min_rows, corroboration_forms)
        settings = dataset_settings() if any(value is None for value in declared) else {}
        self.gpu_models = tuple(settings["gpu_models"] if gpu_models is None else gpu_models)
        self.min_rows = int(settings["min_file_rows"] if min_rows is None else min_rows)
        self.corroboration_forms = tuple(settings["corroboration_forms"]
                                         if corroboration_forms is None else corroboration_forms)

    def _request(self) -> bytes:
        # Public dataset file: no credential is sent.
        request = Request(CSV_URL, headers={"Accept": "text/csv",
                                            "User-Agent": "ATS-EvidenceObserver/1.0"})
        try:
            with self.opener(request, timeout=30) as response:
                payload = response.read(MAX_PAYLOAD_BYTES + 1)
        except HTTPError as exc:
            raise ConnectionError(f"getdeploying_http_{exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise ConnectionError(
                f"getdeploying_unreachable:{getattr(exc, 'reason', exc)}") from exc
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise GpuPriceError("payload_too_large")
        return payload

    def _request_with_retry(self) -> bytes:
        for _attempt in range(2):
            try:
                return self._request()
            except ConnectionError:
                time.sleep(0.05)
        return self._request()

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        fetched = self.clock().astimezone(UTC)

        def failed(status: IngestionStatus, message: str) -> AdapterBatch:
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=status, fetched_at=fetched,
                                failures=[AdapterFailure(status=status, message=message)])

        try:
            payload = self._request_with_retry()
            records, diagnostics = parse_weekly_payload(
                payload, fetched_at=fetched, gpu_models=self.gpu_models,
                min_rows=self.min_rows, corroboration_forms=self.corroboration_forms)
        except ConnectionError as exc:
            return failed(IngestionStatus.UNREACHABLE, str(exc))
        except GpuPriceError as exc:
            message = str(exc)
            return failed(IngestionStatus.METHODOLOGY_DRIFT if "schema_drift" in message
                          else IngestionStatus.PARSE_FAILED, message)
        digest = hashlib.sha256(payload).hexdigest()
        slice_key = f"getdeploying_weekly:{diagnostics['snapshot_date']}:{digest[:16]}"
        records = [record.model_copy(update={"slice_key": slice_key}) for record in records]
        artifact = AdapterArtifact(
            artifact_key=slice_key, payload=payload,
            query_scope={"snapshot_date": diagnostics["snapshot_date"]}, source_url=CSV_URL,
            source_version=f"v1:{diagnostics['snapshot_date']}:{digest}",
            media_type="text/csv", retention="query_slice", storage_mode="full",
            pointer=CSV_URL, metadata=diagnostics)
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED, fetched_at=fetched, records=records,
            artifacts=[artifact],
            provider_metadata={"source_url": CSV_URL, "parser_version": PARSER_VERSION,
                               "methodology_regime": METHODOLOGY_REGIME, "license": LICENSE,
                               "latest_week": diagnostics["latest_week"],
                               "kept_rows": diagnostics["kept_rows"],
                               "rows_by_form": diagnostics["rows_by_form"],
                               "forms_absent": diagnostics["forms_absent"]})

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        batch = self.fetch(request)
        if not batch.records:
            return DiscoveryResult(
                source_id=request.source_id, dataset_id=request.dataset_id,
                checked_at=batch.fetched_at, status=DiscoveryStatus.UNREACHABLE,
                diagnostics={"failures": [item.message for item in batch.failures]})
        latest = str(batch.provider_metadata["latest_week"])
        digest = batch.artifacts[0].source_version.rsplit(":", 1)[1]
        candidate = ReleaseCandidate(
            identity=f"GETDEPLOYING_GPU:{latest}:{digest}", period=latest, urls=[CSV_URL],
            methodology_fingerprint=METHODOLOGY_REGIME,
            metadata={"row_count": int(batch.provider_metadata["kept_rows"])})
        return DiscoveryResult(
            source_id=request.source_id, dataset_id=request.dataset_id,
            checked_at=batch.fetched_at, status=DiscoveryStatus.NEW_RELEASE,
            latest_upstream_identity=candidate.identity, latest_available_period=latest,
            candidates=[candidate])
