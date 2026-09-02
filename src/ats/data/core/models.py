"""Cross-domain metadata contracts for persistent and runtime data."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("data-layer timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class EntityKind(str, Enum):
    COMPANY = "company"
    PERSON = "person"
    INDUSTRY = "industry"
    REGION = "region"
    SECURITY = "security"
    EVENT = "event"
    OTHER = "other"


class SourceKind(str, Enum):
    OFFICIAL = "official"
    PROVIDER = "provider"
    RESEARCH = "research"
    NEWS = "news"
    USER = "user"
    DERIVED = "derived"


class SourceStatus(str, Enum):
    REGISTERED = "registered"
    PUBLISHED = "published"
    PLANNED = "planned"
    DISABLED = "disabled"
    FAILURE = "failure"
    RUNTIME_EXCLUDED = "runtime/excluded"


class IngestionRunStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NO_CHANGE = "no_change"
    PARTIAL = "partial"
    NO_COVERAGE = "no_coverage"
    NOT_YET_PUBLISHED = "not_yet_published"
    STALE = "stale"
    UNREACHABLE = "unreachable"
    UNAUTHORIZED = "unauthorized"
    PARSE_FAILED = "parse_failed"
    VALIDATION_FAILED = "validation_failed"


class QualityLevel(str, Enum):
    ACCEPTED = "accepted"
    WARNING = "warning"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"


class EntityRef(BaseModel):
    """Stable economic entity reference shared by both data domains."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: EntityKind = EntityKind.OTHER
    display_name: str = ""
    aliases: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("entity id is required")
        return value.upper()


class SourceRef(BaseModel):
    """Provider-independent source identity and lifecycle metadata."""

    model_config = ConfigDict(extra="forbid")

    id: str
    provider: str = ""
    kind: SourceKind = SourceKind.PROVIDER
    status: SourceStatus = SourceStatus.REGISTERED
    adapter: str = ""
    policy: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def source_id_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source id is required")
        return value


class LineageRef(BaseModel):
    """Link from a published result to the source material and transformation."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    run_id: str = ""
    artifact_id: str = ""
    source_version: str = ""
    document_id: str = ""
    document_version_id: str = ""
    span: str = ""
    transform: str = ""
    captured_at: datetime | None = None

    _captured_at_aware = field_validator("captured_at")(_aware)


class QualitySnapshot(BaseModel):
    """Machine-readable quality state attached to a data product result."""

    model_config = ConfigDict(extra="forbid")

    level: QualityLevel = QualityLevel.ACCEPTED
    freshness: str = ""
    coverage: str = ""
    reason_codes: list[str] = Field(default_factory=list)
    checks: dict[str, Any] = Field(default_factory=dict)


class IngestionRun(BaseModel):
    """A single source execution, independent of its payload domain."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source_id: str
    dataset_id: str = ""
    status: IngestionRunStatus = IngestionRunStatus.RUNNING
    started_at: datetime
    finished_at: datetime | None = None
    records_seen: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    _started_at_aware = field_validator("started_at")(_aware)
    _finished_at_aware = field_validator("finished_at")(_aware)
