"""The unified TaskProjection envelope every analysis role publishes through.

Why an envelope at all
----------------------
An agent's raw answer is not enough to act on. Downstream code has to be able to ask
"does this still hold?", "what was it derived from?" and "which version of the model
and prompt produced it?" — before it looks at the conclusion. An envelope carries
those answers next to the payload instead of scattering them across call sites, so
caching, idempotence, dependency tracking and audit all read from one row.

Two invariants the rest of the system depends on:

* A payload is only ever written after the *role-specific* schema accepted it. A
  free-text blob is not a legal agent output; refusing to store it is the point.
* The content hash covers the normalized payload **and** the key input references.
  Identical prose derived from different inputs is a different projection — reusing
  the old one would silently attribute stale evidence to a fresh conclusion.

The envelope is stored in ``task_projection_envelopes``, an independent table. The
pre-existing ``task_projections`` table models a different abstraction (evidence fact
projections keyed by profile/target) and is deliberately left alone.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# --------------------------------------------------------------------------- #
# Roles and their payload schemas
# --------------------------------------------------------------------------- #

AgentRole = Literal[
    "layer_analysis",
    "information_brief",
    "sector_allocation",
    "fundamental_expectation_update",
    "fundamental_event_review",
    "macro_review",
    "technical_review",
]

AGENT_ROLES: tuple[AgentRole, ...] = (
    "layer_analysis", "information_brief", "sector_allocation",
    "fundamental_expectation_update", "fundamental_event_review",
    "macro_review", "technical_review",
)

ScopeKind = Literal["entity", "sector", "layer", "portfolio"]

# `portfolio` outranks everything: a whole-book projection answers any narrower query.
# Every other kind must match exactly — a sector view is not evidence about a single
# name, and treating it as such is how a wrong order gets justified.
SCOPE_RANK: dict[ScopeKind, int] = {
    "entity": 0, "sector": 1, "layer": 2, "portfolio": 3,
}


class _Payload(BaseModel):
    """Common base for role payloads: strict about unknown fields, stable to dump."""

    model_config = ConfigDict(extra="forbid")

    schema_name: str = ""
    schema_version: str = "v1"

    @classmethod
    def role_schema_name(cls) -> str:
        return cls.model_fields["schema_name"].default or cls.__name__

    def payload_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


class LayerAnalysisPayload(_Payload):
    """One industry layer read end to end."""

    schema_name: str = "LayerAnalysis"
    layer: str = Field(min_length=1)
    status: Literal["expanding", "steady", "contracting", "unclear"]
    summary: str = Field(min_length=1)
    findings: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("findings", mode="before")
    @classmethod
    def _non_empty_findings(cls, value: object) -> object:
        return _as_list(value)


class InformationBriefPayload(_Payload):
    """A single piece of information, assessed for one target."""

    schema_name: str = "InformationBrief"
    entity: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    relevance: Literal["low", "medium", "high"]
    sources: list[str] = Field(min_length=1)

    @field_validator("sources", mode="before")
    @classmethod
    def _non_empty_sources(cls, value: object) -> object:
        return _as_list(value)


class SectorAllocationPayload(_Payload):
    """Where capital should sit within a sector, relative to the book."""

    schema_name: str = "SectorAllocation"
    sector: str = Field(min_length=1)
    stance: Literal["overweight", "neutral", "underweight"]
    target_weight: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    drivers: list[str] = Field(default_factory=list)

    @field_validator("drivers", mode="before")
    @classmethod
    def _drivers_as_list(cls, value: object) -> object:
        return _as_list(value)

    @field_validator("target_weight", mode="before")
    @classmethod
    def _weight_from_string(cls, value: object) -> object:
        return _as_float(value)


class FundamentalExpectationUpdatePayload(_Payload):
    """A changed expectation for one measurable quantity."""

    schema_name: str = "FundamentalExpectationUpdate"
    entity: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    period: str = Field(min_length=1)
    previous_value: float | None = None
    new_value: float
    driver: str = Field(min_length=1)

    @field_validator("new_value", "previous_value", mode="before")
    @classmethod
    def _values_from_string(cls, value: object) -> object:
        return _as_float(value)


class FundamentalEventReviewPayload(_Payload):
    """What a discrete corporate event means for the expectation."""

    schema_name: str = "FundamentalEventReview"
    entity: str = Field(min_length=1)
    event: str = Field(min_length=1)
    period: str = Field(min_length=1)
    direction: Literal[-1, 0, 1]
    magnitude: float = Field(ge=0.0)
    notes: str = ""

    @field_validator("magnitude", mode="before")
    @classmethod
    def _magnitude_from_string(cls, value: object) -> object:
        return _as_float(value)

    @field_validator("direction", mode="before")
    @classmethod
    def _direction_from_string(cls, value: object) -> object:
        return _as_int(value)


class MacroReviewPayload(_Payload):
    """The regime read that frames every other role's assumptions."""

    schema_name: str = "MacroReview"
    regime: Literal["risk_on", "risk_off", "transition", "unclear"]
    summary: str = Field(min_length=1)
    indicators: list[str] = Field(min_length=1)

    @field_validator("indicators", mode="before")
    @classmethod
    def _indicators_as_list(cls, value: object) -> object:
        return _as_list(value)


class TechnicalReviewPayload(_Payload):
    """Price/volume read for one instrument."""

    schema_name: str = "TechnicalReview"
    entity: str = Field(min_length=1)
    signal: Literal["bullish", "bearish", "neutral"]
    summary: str = Field(min_length=1)
    levels: dict[str, float] = Field(default_factory=dict)

    @field_validator("levels", mode="before")
    @classmethod
    def _levels_from_string(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{"):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    return value
            return value
        return value


PAYLOAD_SCHEMA_BY_ROLE: dict[AgentRole, type[_Payload]] = {
    "layer_analysis": LayerAnalysisPayload,
    "information_brief": InformationBriefPayload,
    "sector_allocation": SectorAllocationPayload,
    "fundamental_expectation_update": FundamentalExpectationUpdatePayload,
    "fundamental_event_review": FundamentalEventReviewPayload,
    "macro_review": MacroReviewPayload,
    "technical_review": TechnicalReviewPayload,
}


class UnknownRoleError(ValueError):
    """A role outside the declared vocabulary."""

    def __init__(self, role: object) -> None:
        self.role = role
        super().__init__(f"unknown agent role {role!r}; expected one of {', '.join(AGENT_ROLES)}")


class EnvelopeValidationError(ValueError):
    """A payload its own role schema rejected.

    The message names the offending fields rather than echoing the whole payload: the
    caller needs to know what to fix, not to re-read what it just sent.
    """

    def __init__(self, role: str, errors: Sequence[str]) -> None:
        self.role = role
        self.errors = list(errors)
        detail = "; ".join(self.errors) if self.errors else "unspecified schema violation"
        super().__init__(f"{role} payload rejected by its schema: {detail}")


# --------------------------------------------------------------------------- #
# Normalization of structurally fixable model output
# --------------------------------------------------------------------------- #

def _as_list(value: object) -> object:
    """A list delivered as a string is recoverable; anything else is not.

    Models routinely render `["a", "b"]` as `"a, b"` or as one blob with newlines.
    Splitting is safe because every list field here is a list of short strings. A
    JSON-encoded list is decoded rather than split, so quotes survive intact.
    """
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return value
        return decoded if isinstance(decoded, list) else value
    parts = [part.strip() for part in stripped.replace("\n", ",").split(",")]
    return [part for part in parts if part]


def _as_float(value: object) -> object:
    if value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return value
    return value


def _as_int(value: object) -> object:
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return value
    return value


# --------------------------------------------------------------------------- #
# Validation entry point
# --------------------------------------------------------------------------- #

def validate_payload(role: AgentRole, payload: Any) -> _Payload:
    """Normalize then validate `payload` against the schema declared for `role`.

    Raises `EnvelopeValidationError` — never returns a partially populated model and
    never falls back to storing free text. Rejected output is a failed run, which the
    caller must surface rather than quietly degrade.
    """
    schema = PAYLOAD_SCHEMA_BY_ROLE.get(role)
    if schema is None:
        raise UnknownRoleError(role)
    try:
        if isinstance(payload, BaseModel):
            data = payload.model_dump(mode="json")
        else:
            data = dict(payload)  # type: ignore[arg-type]
        return schema.model_validate(data)
    except (ValidationError, TypeError, ValueError) as exc:
        raise EnvelopeValidationError(role, _describe(exc)) from exc


def _describe(exc: Exception) -> list[str]:
    if isinstance(exc, ValidationError):
        return [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        ]
    return [str(exc)]


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #

class ProjectionScope(BaseModel):
    """What a projection speaks about: one name, one sector, one layer, the book."""

    model_config = ConfigDict(frozen=True)

    kind: ScopeKind
    id: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}" if self.id else self.kind

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, value: object) -> object:
        if isinstance(value, str) and value.strip():
            return value.strip().upper() if value.strip().isalpha() else value.strip()
        return ""


def scope_covers(envelope_scope: ProjectionScope, query_scope: ProjectionScope) -> bool:
    """True when `envelope_scope` legitimately answers a question about `query_scope`.

    Only `portfolio` widens. A sector-level read is not evidence about a single name —
    letting it stand in for one is how a thesis about the group gets booked as a thesis
    about the holding.
    """
    if envelope_scope.key == query_scope.key:
        return True
    return SCOPE_RANK[envelope_scope.kind] >= SCOPE_RANK["portfolio"]


# --------------------------------------------------------------------------- #
# Content hash
# --------------------------------------------------------------------------- #

def canonical_json(value: Any) -> str:
    """Serialize for hashing: key order, whitespace and float noise must not matter."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_refs(refs: Any) -> list[str]:
    """Freeze reference collections into a sorted, de-duplicated list of strings."""
    if refs is None:
        return []
    if isinstance(refs, str):
        refs = _as_list(refs)
    if isinstance(refs, Mapping):
        items: list[str] = []
        for key in sorted(refs):
            value = refs[key]
            items.append(f"{key}={value}" if not isinstance(value, (dict, list)) else
                         f"{key}={canonical_json(value)}")
        return items
    if isinstance(refs, Sequence):
        return sorted({str(item) for item in refs})
    return [str(refs)]


def content_hash(*, role: AgentRole, scope: ProjectionScope, as_of: str,
                 schema_name: str, schema_version: str,
                 payload: _Payload | Mapping[str, Any],
                 input_refs: Any, data_vintage_refs: Any) -> str:
    """Identity of a projection: who, about what, when, from which inputs.

    Two projections hash equal only when the role, scope, `as_of`, schema version,
    normalized payload *and* every key input reference match. Change the inputs while
    keeping the wording and the hash moves — which is exactly what prevents a stale
    conclusion from being served as a fresh one.
    """
    body = payload.payload_json() if isinstance(payload, _Payload) else canonical_json(payload)
    material = canonical_json({
        "role": role,
        "scope": scope.key,
        "as_of": as_of,
        "schema": f"{schema_name}@{schema_version}",
        "payload": body,
        "input_refs": normalize_refs(input_refs),
        "data_vintage_refs": normalize_refs(data_vintage_refs),
    })
    return hashlib.sha256(material.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# The envelope
# --------------------------------------------------------------------------- #

EnvelopeStatus = Literal["published", "failed"]


class TaskProjectionEnvelope(BaseModel):
    """One agent output, with everything needed to trust, reuse and audit it."""

    projection_id: str
    workflow_run_id: str = ""
    agent_run_id: str = ""
    agent_role: AgentRole
    scope: ProjectionScope
    as_of: str
    valid_until: str = ""
    schema_name: str
    schema_version: str
    input_refs: list[str] = Field(default_factory=list)
    data_vintage_refs: list[str] = Field(default_factory=list)
    model_version: str = ""
    prompt_version: str = ""
    payload: dict[str, Any]
    content_hash: str
    status: EnvelopeStatus = "published"
    created_at: str = ""
    supersedes_projection_id: str = ""

    def is_expired(self, *, at: datetime | None = None) -> bool:
        """No `valid_until` means "until superseded", not "forever fresh by omission"."""
        if not self.valid_until:
            return False
        now = at or datetime.now(timezone.utc)
        try:
            until = datetime.fromisoformat(self.valid_until)
        except ValueError:
            return True
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return now > until


def build_envelope(*, role: AgentRole, payload: Any, scope: ProjectionScope,
                   as_of: str, input_refs: Any = None, data_vintage_refs: Any = None,
                   workflow_run_id: str = "", agent_run_id: str = "",
                   valid_until: str = "", model_version: str = "",
                   prompt_version: str = "", created_at: str | None = None,
                   supersedes_projection_id: str = "") -> TaskProjectionEnvelope:
    """Validate a payload and wrap it — the only sanctioned way to publish output."""
    validated = validate_payload(role, payload)
    body = json.loads(validated.payload_json())
    stamp = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    digest = content_hash(
        role=role, scope=scope, as_of=as_of,
        schema_name=validated.schema_name, schema_version=validated.schema_version,
        payload=validated, input_refs=input_refs, data_vintage_refs=data_vintage_refs)
    envelope = TaskProjectionEnvelope(
        projection_id=digest[:32], workflow_run_id=workflow_run_id,
        agent_run_id=agent_run_id, agent_role=role, scope=scope, as_of=as_of,
        valid_until=valid_until, schema_name=validated.schema_name,
        schema_version=validated.schema_version,
        input_refs=normalize_refs(input_refs),
        data_vintage_refs=normalize_refs(data_vintage_refs),
        model_version=model_version, prompt_version=prompt_version, payload=body,
        content_hash=digest, created_at=stamp,
        supersedes_projection_id=supersedes_projection_id)
    return envelope


# --------------------------------------------------------------------------- #
# Reuse
# --------------------------------------------------------------------------- #

def reuse_decision(envelope: TaskProjectionEnvelope, *, scope: ProjectionScope,
                   input_refs: Any = None, data_vintage_refs: Any = None,
                   schema_name: str | None = None,
                   schema_version: str | None = None,
                   at: datetime | None = None) -> tuple[bool, str]:
    """Should a downstream role re-run this role, or can it read `envelope`?

    Returns `(reusable, reason)`. A single reason is reported — the first condition
    that fails — because the caller needs to know *what* to do next (re-run, widen the
    scope, wait for a refresh), not a list of everything that is wrong.
    """
    if envelope.status != "published":
        return False, "not_published"
    if envelope.is_expired(at=at):
        return False, "expired"
    if not scope_covers(envelope.scope, scope):
        return False, "scope_mismatch"
    if schema_name is not None and envelope.schema_name != schema_name:
        return False, "schema_mismatch"
    if schema_version is not None and envelope.schema_version != schema_version:
        return False, "schema_version_mismatch"
    wanted_inputs = normalize_refs(input_refs)
    if wanted_inputs and set(wanted_inputs) - set(envelope.input_refs):
        return False, "input_refs_changed"
    wanted_vintages = normalize_refs(data_vintage_refs)
    if wanted_vintages and set(wanted_vintages) != set(envelope.data_vintage_refs):
        return False, "data_vintage_changed"
    return True, "reusable"


def is_reusable(envelope: TaskProjectionEnvelope, **kwargs: Any) -> bool:
    return reuse_decision(envelope, **kwargs)[0]
