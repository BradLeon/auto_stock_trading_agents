"""Ramp AI Index monthly adoption series published on FRED (release rid=23, since 2026-10-05).

FRED carries 14 of the Ramp page's adoption series (overall, three vendors, seven NAICS sectors, three
business sizes). They are collected only from here, over plain HTTP, so the trend axis does not depend on a
browser. The page runner (`ramp_ai_index`) fills in what FRED does not publish and never stores a series
listed in `fred_series` (see `config/data/structured.yaml`).
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from typing import Any, Callable

from ..core.structured_models import (
    AdapterArtifact, AdapterBatch, AdapterFailure, DiscoveryResult, DiscoveryStatus, FetchRequest,
    IngestionStatus, NativeRecord, ReleaseCandidate,
)
from .rps_genai_adoption import FRED_GRAPH, FRED_METADATA


SOURCE_ID = "ramp_ai_index_fred"
DATASET_ID = "ramp_ai_adoption"
REGIME = "ramp_ai_index_v2"
PARSER_VERSION = "ramp_ai_index_fred/v1"
TITLE_PREFIX = "Artificial Intelligence Adoption by Ramp Businesses"
APPROVED_METADATA = {"frequency": "Monthly", "season": "Not Seasonally Adjusted", "units": "Percent"}


def load_fred_series(catalog=None) -> dict[str, dict[str, str]]:
    """FRED series id -> {scope, entity_id, segment}, as registered in the structured catalog."""
    if catalog is None:
        from ..catalog.structured import StructuredCatalog
        catalog = StructuredCatalog.load()
    row = (catalog.raw.get("sources") or {}).get(SOURCE_ID) or {}
    return {str(series_id): {key: str(value) for key, value in (spec or {}).items()}
            for series_id, spec in (row.get("fred_series") or {}).items()}


def _page_entity(spec: dict[str, str]) -> str:
    """The entity id the page parser gives the same series when the page uses another name."""
    from .ramp_ai_index import _adoption_entity

    return _adoption_entity(spec["scope"], spec["page_segment"])[0]


def fred_page_aliases(catalog=None) -> dict[tuple[str, str], str]:
    """(scope, page entity id) -> FRED entity id, for series the page names differently (`page_segment`)."""
    return {(spec["scope"], _page_entity(spec)): spec["entity_id"]
            for spec in load_fred_series(catalog).values() if spec.get("page_segment")}


def fred_coverage(catalog=None) -> set[tuple[str, str]]:
    """(scope, entity_id) pairs FRED publishes, under FRED's and the page's names; the page must not store these."""
    series = load_fred_series(catalog).values()
    return ({(spec["scope"], spec["entity_id"]) for spec in series}
            | set(fred_page_aliases(catalog)))


def _dimensions(spec: dict[str, str], *, period: str, series_id: str) -> dict[str, Any]:
    scope, entity_id, segment = spec["scope"], spec["entity_id"], spec["segment"]
    return {
        "scope": scope,
        "statistical_unit": "Ramp relevant American businesses",
        "denominator_scope": "Ramp businesses in the relevant chart cohort",
        "technology_scope": "AI products or services with a positive Ramp transaction",
        "geography": "United States / Ramp network (not a national estimate)",
        "reference_period": period,
        "segment": segment,
        "methodology_regime": REGIME,
        "source_method": "fred_csv",
        "fred_series_id": series_id,
        "vendor": segment if entity_id.startswith("RAMP_VENDOR:") else "",
        "naics_group": segment if entity_id.startswith("NAICS:") else "",
        "business_size": segment if entity_id.startswith("RAMP_SIZE:") else "",
        "release_label": period[:7],
    }


def parse_ramp_fred_csv(series_id: str, payload: bytes, *, spec: dict[str, str],
                        slice_key: str) -> list[NativeRecord]:
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    date_field = "DATE" if reader.fieldnames and "DATE" in reader.fieldnames else "observation_date"
    if not reader.fieldnames or date_field not in reader.fieldnames or series_id not in reader.fieldnames:
        raise ValueError(f"fred_schema_missing:{series_id}")
    provider_field = ("vendor_adoption_pct" if spec["scope"] == "adoption_overall_models"
                      else "adoption_rate_pct")
    records: list[NativeRecord] = []
    seen: set[str] = set()
    for row in reader:
        value = row.get(series_id, "")
        if value in {"", ".", "NA"}:
            continue
        try:
            numeric = float(value)
        except ValueError as exc:
            raise ValueError(f"fred_value_invalid:{series_id}:{value}") from exc
        if not 0 <= numeric <= 100:
            raise ValueError(f"fred_percent_out_of_range:{series_id}:{numeric}")
        period = row[date_field]
        if len(period) != 10 or not period.endswith("-01"):
            raise ValueError(f"fred_frequency_drift:{series_id}:{period}")
        if period in seen:
            raise ValueError(f"fred_duplicate_period:{series_id}:{period}")
        seen.add(period)
        records.append(NativeRecord(
            entity_id=spec["entity_id"], provider_field=provider_field, period=period, value=numeric,
            unit="percent", period_basis="calendar_month", period_start=period, period_end=period,
            # Same reasoning as the page parser: FRED gives a reference month, not a publication time;
            # an empty published_at keeps identical re-imports idempotent.
            published_at=None,
            dimensions=_dimensions(spec, period=period, series_id=series_id),
            raw={"provider_row": row, "series_id": series_id, "scope": spec["scope"]},
            slice_key=slice_key))
    return records


class RampAIIndexFredAdapter:
    source_id = SOURCE_ID
    dataset_id = DATASET_ID

    def __init__(self, *, client=None, clock: Callable[[], datetime] | None = None,
                 series: dict[str, dict[str, str]] | None = None):
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._series = series

    @property
    def series(self) -> dict[str, dict[str, str]]:
        if self._series is None:
            self._series = load_fred_series()
        return self._series

    def _bytes(self, client, series_id: str) -> bytes:
        response = client.get(f"{FRED_GRAPH}{series_id}", timeout=60)
        response.raise_for_status()
        return bytes(response.content)

    def _metadata(self, client, series_id: str) -> dict[str, Any]:
        response = client.get(f"{FRED_METADATA}{series_id}", timeout=60)
        response.raise_for_status()
        try:
            item = response.json()["chart_series"][0]["series_objects"]["a"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"fred_metadata_schema_drift:{series_id}") from exc
        metadata = {key: item.get(key, "") for key in
                    ("series_id", "title", "frequency", "season", "units", "notes", "last_updated")}
        for key, value in APPROVED_METADATA.items():
            if metadata.get(key) != value:
                raise ValueError(f"fred_metadata_drift:{series_id}:{key}:{metadata.get(key)}")
        if not str(metadata.get("title", "")).startswith(TITLE_PREFIX):
            raise ValueError(f"fred_title_drift:{series_id}:{metadata.get('title')}")
        if "ramp ai index" not in str(metadata.get("notes", "")).casefold():
            raise ValueError(f"fred_notes_drift:{series_id}")
        # Ramp restates its sample size in the notes ("more than 70,000 businesses") as it grows; that text is
        # kept for audit but is not part of the methodology fingerprint, or every sample update would read as
        # a methodology break.
        metadata["notes_sha256"] = hashlib.sha256(str(metadata.pop("notes", "")).encode()).hexdigest()
        metadata["methodology_fingerprint"] = hashlib.sha256(json.dumps({
            "series_id": series_id, "title": metadata["title"], "frequency": metadata["frequency"],
            "season": metadata["season"], "units": metadata["units"], "methodology_regime": REGIME,
            "parser_version": PARSER_VERSION}, sort_keys=True).encode()).hexdigest()
        metadata["metadata_url"] = f"{FRED_METADATA}{series_id}"
        return metadata

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        import httpx
        checked = self.clock().astimezone(timezone.utc)
        client = self.client or httpx.Client(follow_redirects=True)
        close = self.client is None
        try:
            candidates: list[ReleaseCandidate] = []
            failures: dict[str, str] = {}
            for series_id, spec in self.series.items():
                try:
                    metadata = self._metadata(client, series_id)
                    payload = self._bytes(client, series_id)
                    rows = parse_ramp_fred_csv(series_id, payload, spec=spec, slice_key=f"fred:{series_id}")
                    if not rows:
                        continue
                    latest = max(row.period for row in rows)
                    digest = hashlib.sha256(payload).hexdigest()
                    candidates.append(ReleaseCandidate(
                        identity=f"FRED:{series_id}:{latest}:{metadata['last_updated']}:{digest}",
                        period=latest, urls=[f"{FRED_GRAPH}{series_id}", metadata["metadata_url"]],
                        methodology_fingerprint=metadata["methodology_fingerprint"],
                        metadata={**metadata, "series_id": series_id, "content_hash": digest,
                                  # Drift is compared per `scope` key; one key per FRED series.
                                  "scope": f"fred:{series_id}", "ramp_scope": spec["scope"],
                                  "entity_id": spec["entity_id"], "methodology_regime": REGIME,
                                  "latest_observation_period": latest}))
                except Exception as exc:
                    failures[series_id] = f"{type(exc).__name__}:{exc}"
            if not candidates:
                return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id,
                                       checked_at=checked,
                                       status=(DiscoveryStatus.UNREACHABLE if failures
                                               else DiscoveryStatus.NOT_YET_PUBLISHED),
                                       diagnostics={"series_failures": failures})
            candidates.sort(key=lambda item: item.metadata["series_id"])
            identity = hashlib.sha256("|".join(item.identity for item in candidates).encode()).hexdigest()
            return DiscoveryResult(
                source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                status=DiscoveryStatus.PARTIAL if failures else DiscoveryStatus.NEW_RELEASE,
                latest_upstream_identity=f"FRED:{identity}",
                latest_available_period=max(item.period for item in candidates), candidates=candidates,
                diagnostics={"series_periods": {item.metadata["series_id"]: item.period for item in candidates},
                             "series_failures": failures})
        finally:
            if close:
                client.close()

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        import httpx
        fetched = self.clock().astimezone(timezone.utc)
        client = self.client or httpx.Client(follow_redirects=True)
        close = self.client is None
        try:
            supplied = (request.query_scope or {}).get("discovery_candidates") or []
            selected = ([ReleaseCandidate.model_validate(item) for item in supplied] if supplied
                        else self.discover(request).candidates)
            records: list[NativeRecord] = []
            artifacts: list[AdapterArtifact] = []
            failures: list[AdapterFailure] = []
            for candidate in selected:
                series_id = str(candidate.metadata.get("series_id", ""))
                spec = self.series.get(series_id)
                if spec is None:
                    continue
                try:
                    payload = self._bytes(client, series_id)
                    digest = hashlib.sha256(payload).hexdigest()
                    if candidate.metadata.get("content_hash") and digest != candidate.metadata["content_hash"]:
                        raise ValueError(f"fred_content_changed_after_discovery:{series_id}")
                    slice_key = f"fred:{series_id}:{digest[:16]}"
                    records.extend(parse_ramp_fred_csv(series_id, payload, spec=spec, slice_key=slice_key))
                    artifacts.append(AdapterArtifact(
                        artifact_key=slice_key, payload=payload,
                        query_scope={"series_id": series_id, "scope": spec["scope"]},
                        source_url=f"{FRED_GRAPH}{series_id}", source_version=candidate.identity,
                        media_type="text/csv", retention="query_slice", storage_mode="full",
                        pointer=f"{FRED_GRAPH}{series_id}",
                        metadata={"fred_series_id": series_id, "content_hash": digest,
                                  "scope": spec["scope"], "methodology_regime": REGIME,
                                  "parser_version": PARSER_VERSION,
                                  "candidate": candidate.model_dump(mode="json")}))
                except Exception as exc:
                    failures.append(AdapterFailure(status=IngestionStatus.VALIDATION_FAILED,
                                                   message=f"{type(exc).__name__}:{exc}",
                                                   slice_key=f"fred:{series_id}"))
            status = (IngestionStatus.PARTIAL if failures and records else
                      IngestionStatus.VALIDATION_FAILED if failures else
                      IngestionStatus.SUCCEEDED if records else IngestionStatus.NO_COVERAGE)
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id, status=status,
                                fetched_at=fetched, records=records, artifacts=artifacts, failures=failures,
                                provider_metadata={"series_count": len(artifacts),
                                                   "warnings": ["vendor_shares_may_overlap"]})
        finally:
            if close:
                client.close()
