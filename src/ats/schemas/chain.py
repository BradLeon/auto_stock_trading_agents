"""Chain evidence — cross-company observations that feed the cross-section factor model.

An earnings print from a company we do NOT hold (Micron, Samsung, AMZN, ORCL) is an
observation of an industry variable, not a standalone event. This module models that
observation. See docs/CHAIN_EVIDENCE.md for the design and the invariants.

The load-bearing distinction is `kind`:
  * common   — industry-wide demand/supply/pricing. Any witness can move it.
  * relative — one subject's competitive position. ONLY direct evidence moves it;
               a competitor merely expanding capacity proves industry supply grew,
               it does NOT prove our holding lost share.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# What kind of statement the observation is. These may never impersonate each other:
# "HBM revenue was X" (fact) is not "capacity is sold out next year" (company claim)
# is not "HBM is entering a supercycle" (opinion).
ObservationType = Literal[
    "reported_actual",       # realized, disclosed figure
    "guidance",              # company's forward claim — formal, but not a fact yet
    "counterparty",          # a customer/supplier speaking about another link
    "regulatory",            # regulator / customs / statistical
    "research",              # sell-side or independent research judgement
    "media",                 # secondary reporting
    "market",                # price/volume/options — market behaviour, NOT fundamentals
]

# Where the witness sits economically. Corroboration requires >= 2 DIFFERENT stances:
# three sell-side notes are one stance, not three witnesses.
WitnessStance = Literal[
    "customer",              # demand side
    "supplier",              # supply side
    "competitor",            # rival in the same layer
    "incumbent",             # the subject itself
    "regulator",             # regulator / statistical agency
]

Direction = Literal["up", "flat", "down"]
ClaimKind = Literal["common", "relative"]
Verdict = Literal["unknown", "supportive", "mixed", "contradicted", "falsified"]


class Observation(BaseModel):
    """One extracted fact, always traceable to a span of source text.

    `id` is deterministic over (document, entity, metric, period) so re-running the
    observer over the same document is idempotent — reprocessing a transcript must
    not inflate the evidence count.
    """

    id: str = ""
    document_id: str                      # source doc (filing/transcript/article)
    source_url: str = ""
    entity: str                           # economic entity the fact is ABOUT
    metric: str                           # e.g. hbm_asp, capex_guide, lead_time
    period: str = ""                      # fiscal label / quarter the fact covers
    observation_type: ObservationType
    stance: WitnessStance
    direction: Direction = "flat"
    value: float | None = None            # optional magnitude; None when qualitative
    unit: str = ""
    # Verbatim source text. Required: an observation that only carries the model's
    # paraphrase cannot be re-checked, and un-recheckable evidence is not evidence.
    evidence_span: str
    observed_at: datetime
    # Frozen when this observation is what MADE the agent notice a proposition. Such
    # material explains "why look", it may never also serve as "it is true".
    discovery_evidence: bool = False
    extraction_confidence: float = Field(1.0, ge=0.0, le=1.0)

    @field_validator("evidence_span")
    @classmethod
    def _span_required(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("evidence_span is required — an observation must be re-checkable")
        return v

    @field_validator("entity", "metric", "document_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("must not be empty")
        return v

    def model_post_init(self, _ctx) -> None:
        if not self.id:
            object.__setattr__(self, "id", self.deterministic_id(
                self.document_id, self.entity, self.metric, self.period))

    @staticmethod
    def deterministic_id(document_id: str, entity: str, metric: str, period: str) -> str:
        raw = f"{document_id}|{entity.upper()}|{metric.lower()}|{period}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


class ObservationFailure(BaseModel):
    """A failed extraction. Persisted rather than silently dropped or defaulted:
    'we could not read this' and 'this says nothing' are different states, and only
    the second one is allowed to influence a verdict."""

    document_id: str
    entity: str = ""
    reason: str
    at: datetime


class Witness(BaseModel):
    entity: str
    stance: WitnessStance


class Horizon(BaseModel):
    """Claims are time-bounded: "tight through 2027" and "tight this quarter" are
    different propositions. Observations outside the horizon decay to unknown."""

    from_: date | None = Field(None, alias="from")
    to: date | None = None

    model_config = {"populate_by_name": True}

    def covers(self, when: date) -> bool:
        if self.from_ and when < self.from_:
            return False
        return not (self.to and when > self.to)


class ClaimDef(BaseModel):
    """A declared, falsifiable proposition. Lives in config/sectors/<name>.yaml under
    its layer; only a human adds one (agents may propose, see docs/CHAIN_EVIDENCE.md)."""

    id: str
    kind: ClaimKind = "common"
    statement: str = ""
    layer: str = ""
    subject: str = ""                     # required when kind == "relative"
    metrics: list[str] = Field(default_factory=list)
    # Gate 3: only readings on these metrics may move a `relative` claim. A competitor's
    # capacity/demand reading is credited to the linked common claim instead.
    direct_metrics: list[str] = Field(default_factory=list)
    witnesses: list[Witness] = Field(default_factory=list)
    falsifiers: list[str] = Field(default_factory=list)
    horizon: Horizon | None = None
    # Which reading direction SUPPORTS the statement. Declared, not inferred: for
    # "supply stays tight" a capacity increase is counter-evidence, while for
    # "demand keeps growing" it is supporting — no generic rule can tell them apart.
    supporting_direction: Direction = "up"
    # Per-metric override, because one claim legitimately mixes polarities: under
    # "HBM supply stays tight", lead_time UP supports it but capacity UP refutes it.
    # Without this, adding capacity to `metrics` would score supply loosening as
    # evidence FOR tightness — the engine would confidently invert the reading.
    metric_polarity: dict[str, Direction] = Field(default_factory=dict)

    def polarity_of(self, metric: str) -> Direction:
        return self.metric_polarity.get((metric or "").lower(), self.supporting_direction)

    @field_validator("subject")
    @classmethod
    def _subject_upper(cls, v: str) -> str:
        return (v or "").upper()

    def model_post_init(self, _ctx) -> None:
        if self.kind == "relative" and not self.subject:
            raise ValueError(f"relative claim {self.id!r} must declare a subject")


class ClaimAssessment(BaseModel):
    """Aggregated verdict for one claim at a point in time.

    Support and refute are kept SEPARATE — never netted — so strong counter-evidence
    cannot be hidden inside a single score. Coverage travels with the verdict:
    "supportive 4/5 reported" and "supportive 1/5 reported" must be distinguishable.
    """

    claim_id: str
    layer: str = ""
    as_of: datetime
    verdict: Verdict = "unknown"
    support_score: float = 0.0
    refute_score: float = 0.0
    evidence_clusters: int = 0            # AFTER de-duplication by originating source
    stance_classes: int = 0               # distinct witness stances seen
    witnesses_expected: int = 0
    witnesses_reported: int = 0
    dissenters: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    note: str = ""

    @property
    def coverage(self) -> str:
        return f"{self.witnesses_reported}/{self.witnesses_expected}"
