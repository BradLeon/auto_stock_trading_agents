"""Provider-neutral domain contracts for persistent structured research data."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class Persistence(str, Enum):
    PERSISTENT = "persistent"
    RUNTIME = "runtime"


class CatalogStatus(str, Enum):
    CURRENT = "current"
    CURRENT_PARTIAL = "current_partial"
    PLANNED = "planned"
    RUNTIME_EXCLUDED = "runtime_excluded"
    DEFERRED = "deferred"


class QualityStatus(str, Enum):
    ACCEPTED = "accepted"
    WARNING = "warning"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"


class VerificationStatus(str, Enum):
    NEEDS_EVIDENCE = "needs_evidence"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class IngestionStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NO_CHANGE = "no_change"
    ZERO_MATCH = "zero_match"
    NOT_YET_PUBLISHED = "not_yet_published"
    NO_COVERAGE = "no_coverage"
    STALE = "stale"
    UNREACHABLE = "unreachable"
    UNAUTHORIZED = "unauthorized"
    PARSE_FAILED = "parse_failed"
    VALIDATION_FAILED = "validation_failed"
    PARTIAL = "partial"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("structured timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class StructuredSource(BaseModel):
    id: str
    provider: str
    adapter: str
    persistence: Persistence = Persistence.PERSISTENT
    catalog_status: CatalogStatus = CatalogStatus.PLANNED
    cadence: str = ""
    retention: str = "source_policy"
    datasets: list[str] = Field(default_factory=list)
    upstream: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def runtime_has_no_datasets(self):
        if self.catalog_status == CatalogStatus.RUNTIME_EXCLUDED:
            if self.persistence != Persistence.RUNTIME or self.datasets:
                raise ValueError("runtime/excluded sources must be runtime with no datasets")
        return self


class StructuredDataset(BaseModel):
    id: str
    catalog_status: CatalogStatus = CatalogStatus.PLANNED
    expected_cadence: str = ""
    primary_sources: list[str] = Field(default_factory=list)
    fallback_sources: list[str] = Field(default_factory=list)
    core_metrics: list[str] = Field(default_factory=list)
    quality: dict[str, Any] = Field(default_factory=dict)
    entities: list[str] = Field(default_factory=list)
    acceptance_samples: list[str] = Field(default_factory=list)


class MetricDefinition(BaseModel):
    id: str
    value_type: str = "number"
    unit_family: str
    cadence: str = ""
    period_basis: str | list[str] = ""
    adjustment: str = ""
    derived: bool = False
    description: str = ""
    version: str = "v1"


class ProviderMapping(BaseModel):
    provider: str
    provider_field: str
    metric_id: str
    version: str = "v1"
    dimensions: dict[str, Any] = Field(default_factory=dict)


class ArtifactDescriptor(BaseModel):
    source_id: str
    dataset_id: str
    fetched_at: datetime
    query_scope: dict[str, Any] = Field(default_factory=dict)
    source_url: str = ""
    source_version: str = ""
    media_type: str = "application/octet-stream"
    retention: str = "source_policy"
    storage_mode: str = "full"
    pointer: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    _fetched_at_aware = field_validator("fetched_at")(_aware)


class RawArtifact(BaseModel):
    id: str
    blob_id: str
    content_hash: str
    relative_path: str = ""
    bytes: int = 0
    descriptor: ArtifactDescriptor


class SeriesIdentity(BaseModel):
    source_id: str
    dataset_id: str
    entity_id: str
    metric_id: str
    unit: str
    currency: str = ""
    period_basis: str = ""
    adjustment: str = ""
    dimensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("entity_id")
    @classmethod
    def entity_upper(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("entity_id is required")
        return value


class ObservationInput(BaseModel):
    series: SeriesIdentity
    period: str
    value: float
    period_start: str = ""
    period_end: str = ""
    event_time: datetime | None = None
    published_at: datetime | None = None
    known_at: datetime
    fetched_at: datetime
    artifact_id: str = ""
    quality_status: QualityStatus = QualityStatus.ACCEPTED
    quality: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    _event_aware = field_validator("event_time")(_aware)
    _published_aware = field_validator("published_at")(_aware)
    _known_aware = field_validator("known_at")(_aware)
    _fetched_aware = field_validator("fetched_at")(_aware)

    @model_validator(mode="after")
    def time_order(self):
        if self.known_at > self.fetched_at:
            raise ValueError("known_at cannot be later than fetched_at")
        if self.published_at and self.published_at > self.known_at:
            raise ValueError("published_at cannot be later than known_at")
        if not self.period.strip():
            raise ValueError("period is required")
        return self


class ObservationVintage(BaseModel):
    id: str
    series_id: str
    content_hash: str
    created: bool = True
    observation: ObservationInput


class FetchRequest(BaseModel):
    source_id: str
    dataset_id: str
    entities: list[str] = Field(default_factory=list)
    periods: list[str] = Field(default_factory=list)
    query_scope: dict[str, Any] = Field(default_factory=dict)

    @field_validator("entities")
    @classmethod
    def entities_upper(cls, values: list[str]) -> list[str]:
        return [value.upper() for value in values]


class NativeRecord(BaseModel):
    entity_id: str
    provider_field: str
    period: str
    value: Any
    unit: str = ""
    currency: str = ""
    period_basis: str = ""
    adjustment: str = ""
    period_start: str = ""
    period_end: str = ""
    event_time: datetime | None = None
    published_at: datetime | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    slice_key: str = ""

    _event_time_aware = field_validator("event_time")(_aware)
    _published_at_aware = field_validator("published_at")(_aware)

    @field_validator("entity_id")
    @classmethod
    def native_entity_upper(cls, value: str) -> str:
        return (value or "").upper()


class AdapterArtifact(BaseModel):
    payload: bytes | str | dict | list | None = None
    query_scope: dict[str, Any] = Field(default_factory=dict)
    source_url: str = ""
    source_version: str = ""
    media_type: str = "application/json"
    retention: str = "source_policy"
    storage_mode: str = "full"
    pointer: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdapterFailure(BaseModel):
    status: IngestionStatus
    message: str
    entity_id: str = ""
    slice_key: str = ""

    @model_validator(mode="after")
    def terminal_failure(self):
        if self.status in {IngestionStatus.RUNNING, IngestionStatus.SUCCEEDED,
                           IngestionStatus.NO_CHANGE}:
            raise ValueError("adapter failure requires a failure/gap status")
        return self


class AdapterBatch(BaseModel):
    source_id: str
    dataset_id: str
    status: IngestionStatus
    fetched_at: datetime
    records: list[NativeRecord] = Field(default_factory=list)
    artifacts: list[AdapterArtifact] = Field(default_factory=list)
    failures: list[AdapterFailure] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    _batch_fetched_aware = field_validator("fetched_at")(_aware)

    @model_validator(mode="after")
    def batch_is_coherent(self):
        if self.status == IngestionStatus.RUNNING:
            raise ValueError("adapter batches must have a terminal provider status")
        if self.status in {IngestionStatus.SUCCEEDED, IngestionStatus.PARTIAL} \
                and not (self.records or self.failures):
            raise ValueError("successful/partial batch must contain records or failures")
        return self


class AdmissionResult(BaseModel):
    candidate_id: str
    status: str
    reason_codes: list[str] = Field(default_factory=list)
    observation_id: str = ""
    metric_id: str = ""
    created: bool = False


class SourceSelection(BaseModel):
    selected: dict[str, Any] | None = None
    selected_source: str = ""
    selection_reason: str
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    conflict: bool = False


class DerivationDefinition(BaseModel):
    id: str
    version: str
    operation: str
    inputs: list[str]
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_metric_id: str = ""


class EvidenceCandidateInput(BaseModel):
    entity: str
    metric_id: str
    value: float
    unit: str
    currency: str = ""
    period: str
    event_type: str
    event_date: str
    event_label: str = ""
    document_id: str
    version_id: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    extraction_method: str
    source_tier: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    published_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    _candidate_published_aware = field_validator("published_at")(_aware)

    @model_validator(mode="after")
    def event_and_span_shape(self):
        if not (self.period or self.event_date):
            raise ValueError("period or event_date is required")
        if self.char_end and self.char_end <= self.char_start:
            raise ValueError("char_end must exceed char_start when a span is supplied")
        return self


class EvidenceLink(BaseModel):
    observation_id: str = ""
    candidate_id: str = ""
    document_id: str
    version_id: str
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    extraction_method: str
    source_tier: str
    verification_status: VerificationStatus = VerificationStatus.NEEDS_EVIDENCE
    reviewer: str = ""
    reviewed_at: datetime | None = None

    _reviewed_aware = field_validator("reviewed_at")(_aware)

    @model_validator(mode="after")
    def valid_span_and_target(self):
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if not (self.observation_id or self.candidate_id):
            raise ValueError("evidence must target an observation or candidate")
        return self


class SnapshotItem(BaseModel):
    observation_id: str
    selected_source: str
    selection_reason: str
    derivation_id: str = ""
    derivation_version: str = ""


class DataSnapshot(BaseModel):
    id: str = ""
    consumer: str
    purpose: str
    as_of: datetime
    created_at: datetime
    items: list[SnapshotItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _as_of_aware = field_validator("as_of")(_aware)
    _created_aware = field_validator("created_at")(_aware)
