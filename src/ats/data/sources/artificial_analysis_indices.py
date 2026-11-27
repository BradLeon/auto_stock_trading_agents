"""Artificial Analysis headline indices from the official free Data API.

Each collection is stored as one dated snapshot of every listed model: the
Intelligence Index, the six capability indexes and the cost of running the
Intelligence Index.  Per-benchmark scores are not part of the free tier and are
never filled from anywhere else.

Terms (Data Platform Terms v1.1, 2026-08-19): internal use only, attribution
required, no redistribution of raw data, no use to develop or train AI systems
without consent, and raw data must be deleted after access ends.  The website
itself must not be scraped; only this API is used.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
)

API_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
DOCS_URL = "https://artificialanalysis.ai/data-api/docs"
API_KEY_ENV = "ARTIFICIAL_ANALYSIS_API_KEY"
SOURCE_ID = "artificial_analysis_indices"
DATASET_ID = "artificial_analysis_model_indices"
PARSER_VERSION = "artificial_analysis_indices/v1"
METHODOLOGY_REGIME = "aa_free_data_api/v2"
# A new major version redefines the index; it needs a review before collection resumes.
INDEX_MAJOR_VERSION = 5
ATTRIBUTION = "Source: Artificial Analysis (artificialanalysis.ai)"
TERMS = ("internal use only; attribution required; no raw redistribution; "
         "no AI development or training use without consent; delete raw data after access ends")
# The free list had 767 models, 157 with an Intelligence Index, on 2026-11-27.
MIN_MODELS = 300
MIN_INDEXED_MODELS = 50
MAX_PAGES = 8           # four pages today; the free tier allows 100 requests a day
MAX_PAGE_BYTES = 5 * 1024 * 1024
# Provider field -> key under ``evaluations``.  Coding and Agentic are no longer published.
INDEX_FIELDS = {
    "intelligence_index": "artificial_analysis_intelligence_index",
    "finance_and_accounting_index": "artificial_analysis_finance_and_accounting_index",
    "strategy_and_ops_index": "artificial_analysis_strategy_and_ops_index",
    "legal_index": "artificial_analysis_legal_index",
    "healthcare_and_medical_index": "artificial_analysis_healthcare_and_medical_index",
    "engineering_index": "artificial_analysis_engineering_index",
    "economics_index": "artificial_analysis_economics_index",
}
COST_PER_TASK = "intelligence_index_cost_per_task"
RUN_COST = "intelligence_index_run_cost"
# Only these carry the Intelligence Index version; capability indexes are versioned
# separately and the API does not report their version.
VERSIONED_FIELDS = frozenset({"intelligence_index", COST_PER_TASK, RUN_COST})
# Stable creator identifiers -> panel lab.  Closed list: no name matching.
CREATOR_LABS = {
    "e67e56e3-15cd-43db-b679-da4660a69f41": "OPENAI",
    "f0aa413f-e8ae-4fcd-9c48-0e049f4f3128": "ANTHROPIC",
    "faddc6d9-2c14-445f-9b28-56726f59c793": "GOOGLE",
    "a1e3ddcf-d3e4-44a5-9e8f-029a69850875": "XAI",          # listed as SpaceXAI
    "58b835bf-4c87-4f87-a846-df4b692c6e7d": "DEEPSEEK",
    "0a177021-87dd-4250-9a37-f01df196bfe0": "MOONSHOT",     # listed as Kimi
    "a4e5bd55-a6cd-4dcf-8d4a-1d9643cd3826": "TENCENT",
    "67437eb6-7dc1-4e93-befd-22c8b8ec2065": "ZAI",
    "d874d370-74d3-4fa0-ba00-5272f92f946b": "ALIBABA",
}


class ArtificialAnalysisError(ValueError):
    """An API response cannot safely enter the governed store."""


def _now() -> datetime:
    return datetime.now(UTC)


def configured_api_key() -> str:
    """The key from the environment or the project's ``.env``; never logged."""
    from ...config import get_config

    return get_config().secrets.artificial_analysis_api_key.strip()


def _number(value: Any, *, field: str, model: str, upper: float) -> float | None:
    if value is None:
        return None          # "not measured", never zero
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ArtificialAnalysisError(f"schema_drift:{field}_not_numeric:{model}")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= upper:
        raise ArtificialAnalysisError(f"schema_drift:{field}_out_of_range:{model}")
    return number


def _mapping(value: Any, *, field: str, model: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ArtificialAnalysisError(f"schema_drift:{field}_not_object:{model}")
    return value


def merge_pages(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Check that the pages form one complete list and return its rows and index version."""
    if not pages:
        raise ArtificialAnalysisError("no_pages")
    versions, rows = set(), []
    total = None
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise ArtificialAnalysisError("schema_drift:envelope")
        pagination = page.get("pagination") or {}
        if pagination.get("page") != number:
            raise ArtificialAnalysisError(f"pagination_out_of_order:{number}")
        total = total if total is not None else pagination.get("total_pages")
        if pagination.get("total_pages") != total:
            raise ArtificialAnalysisError("pagination_changed_during_collection")
        versions.add(page.get("intelligence_index_version"))
        rows.extend(page["data"])
    if total != len(pages) or (pages[-1].get("pagination") or {}).get("has_more"):
        raise ArtificialAnalysisError(f"incomplete_list:{len(pages)}_of_{total}")
    if len(versions) != 1:
        raise ArtificialAnalysisError("index_version_changed_during_collection")
    version = versions.pop()
    if isinstance(version, bool) or not isinstance(version, int | float):
        raise ArtificialAnalysisError("schema_drift:index_version_missing")
    if int(version) != INDEX_MAJOR_VERSION:
        raise ArtificialAnalysisError(
            f"schema_drift:index_major_version:{version} expected {INDEX_MAJOR_VERSION}")
    return rows, f"{version:g}"


def parse_pages(pages: list[dict[str, Any]], *, fetched_at: datetime,
                min_models: int = MIN_MODELS, min_indexed: int = MIN_INDEXED_MODELS
                ) -> tuple[list[NativeRecord], dict[str, Any]]:
    """Normalise one complete model list into one record per model and published value."""
    rows, version = merge_pages(pages)
    if len(rows) < min_models:
        raise ArtificialAnalysisError(f"model_list_too_short:{len(rows)}")
    period = fetched_at.astimezone(UTC).date().isoformat()
    seen: set[str] = set()
    records: list[NativeRecord] = []
    indexed = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ArtificialAnalysisError("schema_drift:row_not_object")
        model_id, name = str(row.get("id") or ""), str(row.get("name") or "")
        creator = _mapping(row.get("model_creator"), field="model_creator", model=name)
        creator_id = str(creator.get("id") or "")
        if not model_id or not name or not creator_id:
            raise ArtificialAnalysisError(f"schema_drift:identity_missing:{name or '?'}")
        if model_id in seen:
            raise ArtificialAnalysisError(f"duplicate_model:{model_id}")
        seen.add(model_id)
        listed = str(row.get("release_date") or "")
        if listed:
            try:
                date.fromisoformat(listed)
            except ValueError as exc:
                raise ArtificialAnalysisError(f"schema_drift:date_invalid:{name}") from exc
        if "evaluations" not in row:
            raise ArtificialAnalysisError(f"schema_drift:evaluations_missing:{name}")
        evaluations = _mapping(row["evaluations"], field="evaluations", model=name)
        values: dict[str, tuple[float, str]] = {}
        for field, key in INDEX_FIELDS.items():
            number = _number(evaluations.get(key), field=field, model=name, upper=100)
            if number is not None:
                values[field] = (number, "index_points")
        cost = _mapping(row.get("artificial_analysis_intelligence_index_cost"),
                        field="index_cost", model=name)
        per_task = _mapping(cost.get("cost_per_task"), field="cost_per_task", model=name)
        for field, raw_value in ((COST_PER_TASK, per_task.get("total_cost")),
                                 (RUN_COST, cost.get("total_cost"))):
            number = _number(raw_value, field=field, model=name, upper=1e7)
            if number is not None:
                values[field] = (number, "USD")
        indexed += "intelligence_index" in values
        for field, (number, unit) in values.items():
            dimensions = {"model_id": model_id, "creator_id": creator_id,
                          "lab_id": CREATOR_LABS.get(creator_id, ""),
                          "methodology_regime": METHODOLOGY_REGIME}
            if field in VERSIONED_FIELDS:
                dimensions["index_version"] = version
            records.append(NativeRecord(
                entity_id=f"AA_MODEL:{model_id.upper()}", provider_field=field, period=period,
                period_start=period, period_end=period, value=number, unit=unit,
                currency="USD" if unit == "USD" else "", period_basis="snapshot",
                dimensions=dimensions,
                # Names and slugs may be edited upstream, so they are not series identity.
                raw={"model_name": name, "slug": str(row.get("slug") or ""),
                     "creator_name": str(creator.get("name") or ""),
                     "aa_listed_release_date": listed}))
    if indexed < min_indexed:
        raise ArtificialAnalysisError(f"schema_drift:too_few_indexed_models:{indexed}")
    diagnostics = {
        "listed_models": len(rows), "indexed_models": indexed, "pages": len(pages),
        "index_version": version, "snapshot_date": period, "source_url": API_URL,
        "parser_version": PARSER_VERSION, "attribution": ATTRIBUTION, "terms": TERMS,
    }
    return records, diagnostics


class ArtificialAnalysisIndicesAdapter:
    source_id = SOURCE_ID
    dataset_id = DATASET_ID

    def __init__(self, *, api_key: str | None = None,
                 opener: Callable[..., Any] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 min_models: int = MIN_MODELS, min_indexed: int = MIN_INDEXED_MODELS):
        self._api_key = api_key
        self.opener = opener or urlopen
        self.clock = clock or _now
        self.min_models = min_models
        self.min_indexed = min_indexed

    def _page(self, number: int, key: str) -> dict[str, Any]:
        request = Request(f"{API_URL}?page={number}", headers={
            "x-api-key": key, "Accept": "application/json",
            "User-Agent": "ATS-EvidenceObserver/1.0"})
        try:
            with self.opener(request, timeout=30) as response:
                payload = response.read(MAX_PAGE_BYTES + 1)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise PermissionError(f"aa_http_{exc.code}") from exc
            if exc.code == 429:
                raise RuntimeError("aa_rate_limited") from exc
            raise ConnectionError(f"aa_http_{exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise ConnectionError(f"aa_unreachable:{getattr(exc, 'reason', exc)}") from exc
        if len(payload) > MAX_PAGE_BYTES:
            raise ArtificialAnalysisError("payload_too_large")
        try:
            body = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtificialAnalysisError("invalid_json") from exc
        if not isinstance(body, dict):
            raise ArtificialAnalysisError("schema_drift:envelope")
        return body

    def _page_with_retry(self, number: int, key: str) -> dict[str, Any]:
        for _attempt in range(2):
            try:
                return self._page(number, key)
            except ConnectionError:
                time.sleep(0.05)
        return self._page(number, key)

    def _pages(self, key: str) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        while len(pages) < MAX_PAGES:
            body = self._page_with_retry(len(pages) + 1, key)
            pages.append(body)
            if not (body.get("pagination") or {}).get("has_more"):
                return pages
        raise ArtificialAnalysisError(f"too_many_pages:{MAX_PAGES}")

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        fetched = self.clock().astimezone(UTC)

        def failed(status: IngestionStatus, message: str) -> AdapterBatch:
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=status, fetched_at=fetched,
                                failures=[AdapterFailure(status=status, message=message)])

        key = configured_api_key() if self._api_key is None else self._api_key
        if not key:
            return failed(IngestionStatus.UNAUTHORIZED, f"aa_api_key_missing:{API_KEY_ENV}")
        try:
            pages = self._pages(key)
            records, diagnostics = parse_pages(
                pages, fetched_at=fetched, min_models=self.min_models,
                min_indexed=self.min_indexed)
        except PermissionError as exc:
            return failed(IngestionStatus.UNAUTHORIZED, str(exc))
        except RuntimeError as exc:
            return failed(IngestionStatus.STALE, str(exc))
        except ConnectionError as exc:
            return failed(IngestionStatus.UNREACHABLE, str(exc))
        except ArtificialAnalysisError as exc:
            message = str(exc)
            return failed(IngestionStatus.METHODOLOGY_DRIFT if "schema_drift" in message
                          else IngestionStatus.PARSE_FAILED, message)
        payload = json.dumps({"endpoint": API_URL, "pages": pages}, ensure_ascii=False,
                             sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(payload).hexdigest()
        slice_key = f"aa_indices:{diagnostics['snapshot_date']}:{digest[:16]}"
        records = [record.model_copy(update={"slice_key": slice_key}) for record in records]
        artifact = AdapterArtifact(
            artifact_key=slice_key, payload=payload,
            query_scope={"snapshot_date": diagnostics["snapshot_date"]}, source_url=API_URL,
            source_version=f"v1:{diagnostics['snapshot_date']}:{digest}",
            media_type="application/json", retention="query_slice", storage_mode="full",
            pointer=API_URL, metadata={**diagnostics, "credential_redacted": True})
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED, fetched_at=fetched, records=records,
            artifacts=[artifact],
            provider_metadata={"source_url": API_URL, "parser_version": PARSER_VERSION,
                               "methodology_regime": METHODOLOGY_REGIME,
                               "methodology_url": DOCS_URL,
                               "index_version": diagnostics["index_version"],
                               "listed_models": diagnostics["listed_models"],
                               "requests_used": diagnostics["pages"]})

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        batch = self.fetch(request)
        if not batch.records:
            status = (DiscoveryStatus.ACCESS_REQUIRED
                      if batch.status == IngestionStatus.UNAUTHORIZED
                      else DiscoveryStatus.UNREACHABLE)
            return DiscoveryResult(
                source_id=request.source_id, dataset_id=request.dataset_id,
                checked_at=batch.fetched_at, status=status,
                diagnostics={"failures": [item.message for item in batch.failures]})
        latest = batch.records[0].period
        digest = batch.artifacts[0].source_version.rsplit(":", 1)[1]
        candidate = ReleaseCandidate(
            identity=f"AA_INDICES:{latest}:{digest}", period=latest, urls=[API_URL],
            methodology_fingerprint=METHODOLOGY_REGIME,
            metadata={"row_count": len(batch.records)})
        return DiscoveryResult(
            source_id=request.source_id, dataset_id=request.dataset_id,
            checked_at=batch.fetched_at, status=DiscoveryStatus.NEW_RELEASE,
            latest_upstream_identity=candidate.identity, latest_available_period=latest,
            candidates=[candidate])
