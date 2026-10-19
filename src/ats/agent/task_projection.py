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

from ats.workflow.evaluation_clock import now as evaluation_now

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, ClassVar, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_PROJECTION_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "ats_projection_context", default=None)


@contextmanager
def projection_context(*, workflow_run_id: str, agent_run_id: str,
                       input_refs: Any = None, data_vintage_refs: Any = None, as_of: str = ""):
    """Attach dispatcher provenance to every projection built in this task scope."""
    token = _PROJECTION_CONTEXT.set({
        "workflow_run_id": workflow_run_id, "agent_run_id": agent_run_id, "as_of": as_of,
        "input_refs": normalize_refs(input_refs),
        "data_vintage_refs": normalize_refs(data_vintage_refs),
    })
    try:
        yield
    finally:
        _PROJECTION_CONTEXT.reset(token)

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
    # Additive fields (2026-10-17, TARGET §7.3 enrichment). An EMPTY value is left out of the dump, so a payload
    # that does not use them hashes exactly as it did before they existed: historical projections stay valid,
    # keep their content hash and need no re-run. A non-empty value is hash-bound like any other field.
    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def role_schema_name(cls) -> str:
        return cls.model_fields["schema_name"].default or cls.__name__

    def payload_json(self) -> str:
        body = self.model_dump(mode="json")
        for name in self._OMIT_WHEN_EMPTY:
            if body.get(name) in (None, "", [], {}):
                body.pop(name, None)
        return json.dumps(body, ensure_ascii=False, sort_keys=True)


class _Item(BaseModel):
    """A structured entry inside an additive field: strict, so a typo cannot hide data."""

    model_config = ConfigDict(extra="forbid")


class LayerEntity(_Item):
    symbol: str = Field(min_length=1)
    role: Literal["member", "witness"] = "member"


class CrossSectionRow(_Item):
    """One name's standing inside the layer, from the evidence readings (not from financials)."""

    symbol: str = Field(min_length=1)
    rank: int | None = Field(default=None, ge=1)
    standing: str = ""                  # strong | neutral | weak | unknown
    basis: str = ""                     # corroborated | self_reported | thin
    evidence_clusters: int = Field(default=0, ge=0)
    stance_classes: int = Field(default=0, ge=0)
    claims: list[str] = Field(default_factory=list)
    reason: str = ""


class EvidenceItem(_Item):
    claim_id: str = Field(min_length=1)
    statement: str = ""
    verdict: str = ""
    basis: str = ""
    coverage: str = ""
    evidence_clusters: int = Field(default=0, ge=0)
    observation_ids: list[str] = Field(default_factory=list)
    note: str = ""


class DivergentEvidence(_Item):
    claim_id: str = Field(min_length=1)
    verdict: str = ""
    unresolved_reason: str = ""
    dissenters: list[str] = Field(default_factory=list)
    silent_witnesses: list[str] = Field(default_factory=list)
    note: str = ""


class LayerAnalysisPayload(_Payload):
    """One industry layer read end to end."""

    schema_name: str = "LayerAnalysis"
    layer: str = Field(min_length=1)
    status: Literal["expanding", "steady", "contracting", "unclear"]
    summary: str = Field(min_length=1)
    findings: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    # --- TARGET §7.3 additive fields ---
    cycle_position: str = ""
    has_claims: bool | None = None
    reversal_triggers: list[str] = Field(default_factory=list)
    entities: list[LayerEntity] = Field(default_factory=list)
    cross_section: list[CrossSectionRow] = Field(default_factory=list)
    cross_section_basis: str = ""       # what the ranking rests on, e.g. "evidence_readings"
    cross_section_coverage: str = ""    # e.g. "5/6 members read"
    common_evidence: list[EvidenceItem] = Field(default_factory=list)
    divergent_evidence: list[DivergentEvidence] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = (
        "cycle_position", "has_claims", "reversal_triggers", "entities", "cross_section",
        "cross_section_basis", "cross_section_coverage", "common_evidence", "divergent_evidence", "gaps")

    @field_validator("findings", mode="before")
    @classmethod
    def _non_empty_findings(cls, value: object) -> object:
        return _as_list(value)


class InformationBriefPayload(_Payload):
    """A single piece of information, assessed for one target.

    The six evidence elements (facts, impact candidates, entities, confidence,
    freshness, unverified items) plus the three clocks and the source-cluster
    fields are all optional at the schema level — they are additive in v1 so
    pre-existing payloads keep validating. The information analyst's own
    publication path treats the six elements as REQUIRED (a brief missing any
    of them is a failed run), which is the `agent/information-analyst`
    contract; the schema only refuses to make old payloads illegal.
    """

    schema_name: str = "InformationBrief"
    entity: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    relevance: Literal["low", "medium", "high"]
    sources: list[str] = Field(min_length=1)
    # --- six evidence elements (Phase D, additive) ---
    fact_changes: list[str] = Field(default_factory=list)
    source_evidence: list[dict[str, Any]] = Field(default_factory=list)
    impact_candidates: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    freshness: str = ""
    unverified: list[str] = Field(default_factory=list)
    # --- three clocks and source clustering (Phase D, additive) ---
    event_time: str = ""       # 事件时间：事情发生的时刻
    published_at: str = ""     # 发布时间：文档对外可见的时刻
    extracted_at: str = ""     # 抽取时间：系统入库并抽取的时刻
    cluster_key: str = ""
    source_count: int | None = Field(default=None, ge=0)
    independent_sources: int | None = Field(default=None, ge=0)

    def payload_json(self) -> str:
        # Additive evidence must not change hashes of historical v1 payloads
        # which predate this field. New nonempty evidence remains hash-bound.
        body = self.model_dump(mode="json")
        if not body["source_evidence"]:
            body.pop("source_evidence")
        return json.dumps(body, ensure_ascii=False, sort_keys=True)

    @field_validator("sources", "fact_changes", "impact_candidates", "entities",
                     "unverified", mode="before")
    @classmethod
    def _non_empty_sources(cls, value: object) -> object:
        return _as_list(value)


class LayerAllocation(_Item):
    layer: str = Field(min_length=1)
    status: str = ""                    # the layer analyst's own status
    allocation: str = ""                # the sector analyst's call: 超配 / 标配 / 低配 / 清仓
    budget: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    cycle_position: str = ""
    projection_ref: str = ""            # the LayerAnalysis projection this rests on
    gaps: list[str] = Field(default_factory=list)


class NameAllocation(_Item):
    symbol: str = Field(min_length=1)
    layer: str = ""
    subgroup: str = ""
    view: str = ""                      # 增持 | 持有 | 减持
    weight: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str = ""
    self_reported_only: bool | None = None


class SectorAllocationPayload(_Payload):
    """Where capital should sit within a sector, relative to the book."""

    schema_name: str = "SectorAllocation"
    sector: str = Field(min_length=1)
    stance: Literal["overweight", "neutral", "underweight"]
    target_weight: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    drivers: list[str] = Field(default_factory=list)
    # --- TARGET §7.3 additive fields: sector -> layer -> name, each with its fact references ---
    layers: list[LayerAllocation] = Field(default_factory=list)
    names: list[NameAllocation] = Field(default_factory=list)
    fact_refs: list[str] = Field(default_factory=list)
    rotation_available: bool | None = None
    rotation_advice: str = ""
    missing_layers: list[str] = Field(default_factory=list)

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = (
        "layers", "names", "fact_refs", "rotation_available", "rotation_advice", "missing_layers")

    @field_validator("drivers", mode="before")
    @classmethod
    def _drivers_as_list(cls, value: object) -> object:
        return _as_list(value)

    @field_validator("target_weight", mode="before")
    @classmethod
    def _weight_from_string(cls, value: object) -> object:
        return _as_float(value)


class ExpectationRow(_Item):
    """One scorecard dimension of the pre-earnings baseline: what a weak, a base and a strong print look like."""

    dim_key: str = Field(min_length=1)
    metric: str = ""
    weight: float | None = Field(default=None, ge=0.0, le=1.0)
    conservative: str = ""
    neutral: str = ""
    optimistic: str = ""
    source: str = ""


class ConsensusView(_Item):
    eps: float | None = None
    eps_low: float | None = None
    eps_high: float | None = None
    revenue: float | None = None
    revenue_low: float | None = None
    revenue_high: float | None = None
    target_price: float | None = None
    rating_summary: str = ""
    recent_actions: list[str] = Field(default_factory=list)
    note: str = ""          # why the estimates are blank (stale or wrong-period snapshot), else ""


class MarketImpliedView(_Item):
    """What the price and the options already say before the print."""

    reference_price: float | None = None
    expected_move_pct: float | None = None     # option-implied straddle move
    atm_iv: float | None = None
    iv_skew: float | None = None               # 25-delta put IV minus call IV
    run_up_vs_sector_pct: float | None = None
    run_up_vs_bench_pct: float | None = None
    dist_to_ath_pct: float | None = None
    notes: list[str] = Field(default_factory=list)


class SignalLine(_Item):
    symbol: str = Field(min_length=1)
    role: str = ""
    reported: bool | None = None
    price_chg_pct: float | None = None
    signal: str = ""


class BaselineDetail(_Item):
    """The pre-earnings baseline as the legacy PEAD report carried it (sections 1.1-2.4 and 7), published whole.

    Added 2026-10-19 (parent 7.5): the Dispatcher path used to publish the baseline as one narrative string, which
    dropped the expectation table, consensus, market-implied expectations, valuation band, catalysts and risks the
    analyst had already produced. Every field is optional; an empty one is not published.
    """

    built_at: str = ""              # the cutoff the baseline was built against (before the print)
    provenance: str = ""            # how it came to be, when not a plain routine build
    core_narrative: str = ""
    peer_comparison: str = ""
    focus_ranking: list[str] = Field(default_factory=list)
    watch_metrics: str = ""
    valuation: str = ""
    catalysts: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    falsifiable_conditions: list[str] = Field(default_factory=list)
    expectation_table: list[ExpectationRow] = Field(default_factory=list)
    consensus: ConsensusView | None = None
    market_implied: MarketImpliedView | None = None
    synthesis: str = ""
    signal_chain: list[SignalLine] = Field(default_factory=list)
    signal_chain_summary: str = ""


class ActualMetricView(_Item):
    dim_key: str = ""
    metric: str = ""
    actual: str = ""
    vs_expected: str = ""
    note: str = ""


class ActualsView(_Item):
    reported_eps: float | None = None
    reported_revenue: float | None = None
    reporting_currency: str = ""
    metrics: list[ActualMetricView] = Field(default_factory=list)
    transcript_signals: list[str] = Field(default_factory=list)
    transcript_source: str = ""


class FundamentalExpectationUpdatePayload(_Payload):
    """A metric reading or qualitative narrative change; unknown is never zero."""

    schema_name: str = "FundamentalExpectationUpdate"
    entity: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    period: str = Field(min_length=1)
    previous_value: float | None = None
    new_value: float | None
    driver: str = Field(min_length=1)
    # --- parent 7.5 additive fields (2026-10-19): classification trail and the whole baseline ---
    change_class: Literal["确认", "否定", "新增", "待验证", ""] = ""
    kpi: str = ""                   # the baseline dimension this change was matched to, when it was
    explanation: str = ""           # why the classifier put it in that class
    evidence_refs: list[str] = Field(default_factory=list)
    baseline: BaselineDetail | None = None

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = (
        "change_class", "kpi", "explanation", "evidence_refs", "baseline")

    @field_validator("new_value", "previous_value", mode="before")
    @classmethod
    def _values_from_string(cls, value: object) -> object:
        return _as_float(value)

    @model_validator(mode="after")
    def _honest_values(self):
        # Historical v1 numeric narrative records remain parseable. Publication
        # and current decision/reuse boundaries apply the stricter value contract.
        if self.metric != "narrative" and self.new_value is None:
            raise ValueError("numeric KPI update requires a measurable value")
        return self


# The action vocabulary of `agent/action-vocabulary`. An event review expresses a
# direction (-1|0|1), never an action — a direction that shows up carrying a verb
# from this set is a second action pipeline in disguise, and the schema refuses it.
EVENT_REVIEW_ACTION_VOCAB: frozenset[str] = frozenset(
    {"buy", "add", "hold", "trim", "sell"})

# Sizing fields under any spelling: an event review must not carry quantities.
_SIZING_FIELD_NAMES: frozenset[str] = frozenset({
    "action", "qty", "quantity", "target_qty", "notional", "target_notional",
    "notional_hint", "qty_hint", "weight", "target_weight", "position_size",
})


class FundamentalEventReviewPayload(_Payload):
    """What a discrete corporate event means for the expectation.

    Carries the non-executable investment view only: direction (expectation-gap
    direction, -1|0|1), magnitude, confidence, reasoning and falsifiable
    conditions. Action vocabulary and sizing fields are rejected outright —
    tradability belongs to the chief and the risk officer (Phase D decision 12).
    """

    schema_name: str = "FundamentalEventReview"
    entity: str = Field(min_length=1)
    event: str = Field(min_length=1)
    period: str = Field(min_length=1)
    direction: Literal[-1, 0, 1]
    magnitude: float = Field(ge=0.0)
    notes: str = ""
    # --- non-executable view, additive in v1 ---
    scorecard: list[dict[str, Any]] = Field(default_factory=list)
    guidance: str = ""
    narrative: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    falsifiable_conditions: list[str] = Field(default_factory=list)
    # --- parent 7.5 additive fields (2026-10-19): what was reported and what it was compared with ---
    actuals: ActualsView | None = None
    baseline_ref: str = ""          # the frozen baseline projection the gap was measured against
    baseline_as_of: str = ""
    comparison_basis: Literal["frozen_baseline", "consensus_only", "none", ""] = ""

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = (
        "actuals", "baseline_ref", "baseline_as_of", "comparison_basis")

    @field_validator("magnitude", mode="before")
    @classmethod
    def _magnitude_from_string(cls, value: object) -> object:
        return _as_float(value)

    @field_validator("direction", mode="before")
    @classmethod
    def _direction_from_string(cls, value: object) -> object:
        return _as_int(value)

    @field_validator("falsifiable_conditions", mode="before")
    @classmethod
    def _conditions_as_list(cls, value: object) -> object:
        return _as_list(value)

    @model_validator(mode="before")
    @classmethod
    def _reject_sizing_fields(cls, data: object) -> object:
        if isinstance(data, Mapping):
            present = sorted(_SIZING_FIELD_NAMES & set(data))
            if present:
                raise ValueError(
                    f"event review must not carry sizing/action fields: {', '.join(present)}")
        return data

    @model_validator(mode="after")
    def _reject_action_values(self) -> "FundamentalEventReviewPayload":
        for name, value in self.__dict__.items():
            if isinstance(value, str) and value.strip().lower() in EVENT_REVIEW_ACTION_VOCAB:
                raise ValueError(
                    f"field {name!r} carries an action-vocabulary value {value!r}; "
                    "an event review expresses a direction, not an action")
        return self


class MacroTransmission(_Item):
    theme: str = Field(min_length=1)
    label: str = ""
    direction: str = ""
    transmission: str = ""
    signal: str = ""


class MacroScenario(_Item):
    name: str = Field(min_length=1)
    description: str = ""
    trigger: str = ""
    sector_impact: str = ""


class MacroTilt(_Item):
    sector: str = Field(min_length=1)
    stance: str = ""
    rationale: str = ""


class MacroReviewPayload(_Payload):
    """The regime read that frames every other role's assumptions."""

    schema_name: str = "MacroReview"
    regime: Literal["risk_on", "risk_off", "transition", "unclear"]
    summary: str = Field(min_length=1)
    indicators: list[str] = Field(min_length=1)
    # --- TARGET §7.3 additive fields ---
    # `regime` above is derived by code from the quadrant (deterministic, checkable); `narrative_regime` is the
    # review's own one-line wording. They can legitimately differ and are both shown, labelled.
    narrative_regime: str = ""
    quadrant: str = ""
    quadrant_state: str = ""
    rate_path: str = ""
    transmission: list[MacroTransmission] = Field(default_factory=list)
    scenarios: list[MacroScenario] = Field(default_factory=list)
    horizon: str = ""
    risks: list[str] = Field(default_factory=list)
    falsifier: str = ""
    sector_tilts: list[MacroTilt] = Field(default_factory=list)
    asset_implications: str = ""

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = (
        "narrative_regime", "quadrant", "quadrant_state", "rate_path", "transmission", "scenarios",
        "horizon", "risks", "falsifier", "sector_tilts", "asset_implications")

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
    # --- TARGET §7.3 additive fields, all computed by code (no model call) ---
    trend_state: str = ""
    score: int | None = Field(default=None, ge=0, le=7)
    score_detail: dict[str, bool] = Field(default_factory=dict)
    suggested_exposure: float | None = Field(default=None, ge=0.0, le=1.0)   # a CEILING, not an order
    previous_exposure: float | None = Field(default=None, ge=0.0, le=1.0)
    invalidation: list[str] = Field(default_factory=list)
    panic_fired: bool | None = None
    bear_fired: bool | None = None

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = (
        "trend_state", "score", "score_detail", "suggested_exposure", "previous_exposure", "invalidation",
        "panic_fired", "bear_fired")

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

    NOTE: this class is re-defined later in this module (role-level publication
    contract) with a polymorphic constructor that also accepts a plain message;
    the later definition wins and keeps both call shapes working.
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
        now = at or evaluation_now(timezone.utc)
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
    context = _PROJECTION_CONTEXT.get() or {}
    # New Dispatcher outputs describe the frozen evaluation request. Publication
    # time stays in created_at; source data vintages and historical rows are untouched.
    as_of = context.get("as_of") or as_of
    workflow_run_id = workflow_run_id or context.get("workflow_run_id", "")
    agent_run_id = agent_run_id or context.get("agent_run_id", "")
    input_refs = [*normalize_refs(context.get("input_refs")), *normalize_refs(input_refs)]
    data_vintage_refs = [*normalize_refs(context.get("data_vintage_refs")),
                         *normalize_refs(data_vintage_refs)]
    validated = validate_payload(role, payload)
    body = json.loads(validated.payload_json())
    stamp = created_at or evaluation_now(timezone.utc).isoformat(timespec="seconds")
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


class EnvelopeValidationError(ValueError):
    """A payload fails a validation contract — schema-level OR role-level.

    Two construction shapes share this one class so callers (and tests) can catch
    either failure mode generically:

    * schema rejection — ``EnvelopeValidationError(role, [field errors...])``:
      pydantic rejected the payload; ``.role``/``.errors`` name what to fix.
    * role-level publication contract (NOT the schema) —
      ``EnvelopeValidationError(message)``: the schema stays additive so old
      payloads keep parsing, while a role's own publish path may demand more
      (e.g. the information analyst requires all six evidence elements).
      Raising here means the run FAILED — callers must not fall back to a
      degraded write. ``.role`` is None in this shape.
    """

    def __init__(self, role_or_message: str,
                 errors: Sequence[str] | None = None) -> None:
        if errors is None:
            super().__init__(role_or_message)
            self.role = None
            self.errors: list[str] = []
            return
        self.role = role_or_message
        self.errors = list(errors)
        detail = "; ".join(self.errors) if self.errors else "unspecified schema violation"
        super().__init__(f"{role_or_message} payload rejected by its schema: {detail}")


def is_reusable(envelope: TaskProjectionEnvelope, **kwargs: Any) -> bool:
    return reuse_decision(envelope, **kwargs)[0]


# --------------------------------------------------------------------------- #
# Reads for downstream consumers (Phase D task 1.6)
# --------------------------------------------------------------------------- #

def available_projection(store: Any, *, role: AgentRole, scope: ProjectionScope,
                         **reuse_kwargs: Any) -> TaskProjectionEnvelope | None:
    """Latest still-usable envelope for one role, or `None`.

    Duck-typed over the store on purpose: the contract (role + scope + validity
    + reuse vocabulary) lives here, the storage stays in `memory.store`. This is
    the sanctioned read path for snapshot consumers — direct table reads are the
    thing the `decision/research-snapshot` capability forbids.
    """
    return store.reusable_task_projection(agent_role=role, scope=scope, **reuse_kwargs)


def available_projections_by_role(
    store: Any, *, roles: Sequence[AgentRole], scope: ProjectionScope | None = None,
    scope_by_role: Mapping[AgentRole, ProjectionScope] | None = None,
    **reuse_kwargs: Any,
) -> dict[AgentRole, TaskProjectionEnvelope | None]:
    """Per-role latest usable envelope, `None` where a role has none.

    Snapshot consumers call this once per decision attempt instead of querying
    tables role by role; the returned mapping plugs straight into
    `decision.snapshot.build_research_snapshot`.
    """
    per_role = scope_by_role or {}
    out: dict[AgentRole, TaskProjectionEnvelope | None] = {}
    for role in roles:
        role_scope = per_role.get(role, scope)
        if role_scope is None:
            raise ValueError(f"no scope given for role {role!r}")
        out[role] = available_projection(store, role=role, scope=role_scope,
                                         **reuse_kwargs)
    return out
