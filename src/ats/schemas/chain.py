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
    # Who DISCLOSED it — the filing this came from. Distinct from `entity` on purpose:
    # NVDA's call discussing HBM supply is a CUSTOMER-side testimony about a supplier's
    # product. Witness stance belongs to the speaker, and two speakers saying the same
    # thing are two independent witnesses; without this they would collapse into one.
    source_entity: str = ""
    metric: str                           # the model's own label, kept for display
    # Which declared claim dimension this fact belongs to, assigned semantically.
    # Empty = unmapped: still stored, and it is exactly what feeds the induction
    # pool in docs/CHAIN_EVIDENCE.md §6.5.
    concept: str = ""
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


class Concept(BaseModel):
    """One dimension a claim is tested on.

    Deliberately a CONCEPT with a description, not a metric name. Observations are
    linked to it semantically — `hbm_market_share`, `hbm_share` and "份额指引下修"
    all belong here. Matching on metric strings would drop evidence over a naming
    accident, which is a real loss dressed up as determinism.
    """

    key: str
    desc: str = ""                        # semantic anchor shown to the linker model
    # Which reading direction supports the claim ON THIS DIMENSION. Per-concept because
    # one claim mixes polarities: under "supply stays tight", lead-time UP supports it
    # while capacity UP refutes it. A claim-level direction would score supply
    # loosening as evidence FOR tightness — confidently inverted.
    supports_when: Direction = "up"
    # Who is expected to speak to this dimension. Makes silence visible as a GAP rather
    # than as neutrality — a single filing is self-interested and may disclose
    # selectively, so cross-validation has to be declared up front.
    expect_from: list[str] = Field(default_factory=list)
    # Gate 3: may a reading here move a `relative` claim? (share / qualification /
    # ASP / margin yes; capacity or demand no.)
    direct: bool = False

    @field_validator("expect_from")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.upper() for s in v]


class ClaimDef(BaseModel):
    """A declared, falsifiable proposition. Lives in config/sectors/<name>.yaml under
    its layer; only a human adds one (agents may propose, see docs/CHAIN_EVIDENCE.md)."""

    id: str
    kind: ClaimKind = "common"
    statement: str = ""
    layer: str = ""
    subject: str = ""                     # required when kind == "relative"
    concepts: list[Concept] = Field(default_factory=list)
    # Witness stances are declared here, NOT read off the document: every filing is the
    # company's own call, so asking a model "who is speaking" always answers "this
    # company", and cross-stance corroboration could never be satisfied.
    witnesses: list[Witness] = Field(default_factory=list)
    falsifiers: list[str] = Field(default_factory=list)
    horizon: Horizon | None = None

    @field_validator("subject")
    @classmethod
    def _subject_upper(cls, v: str) -> str:
        return (v or "").upper()

    def concept(self, key: str) -> Concept | None:
        return next((c for c in self.concepts if c.key == key), None)

    def stance_of(self, entity: str) -> str:
        """Declared stance of the SPEAKER (see Observation.source_entity)."""
        w = next((w for w in self.witnesses if w.entity.upper() == (entity or "").upper()),
                 None)
        return w.stance if w else ""

    def expected_witnesses(self) -> set[str]:
        """Union of who should speak, across dimensions; falls back to the witness list."""
        out = {e for c in self.concepts for e in c.expect_from}
        return out or {w.entity.upper() for w in self.witnesses}

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
    # Declared witnesses that said nothing this period. Named, not folded into a
    # count: a company's silence on a dimension is a gap, not neutrality.
    silent_witnesses: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    note: str = ""

    @property
    def coverage(self) -> str:
        return f"{self.witnesses_reported}/{self.witnesses_expected}"
