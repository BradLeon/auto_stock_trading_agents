"""Chain evidence — cross-company observations that feed the cross-section factor model.

An earnings print from a company we do NOT hold (Micron, Samsung, AMZN, ORCL) is an
observation of an industry variable, not a standalone event. This module models that
observation. See docs/CHAIN_EVIDENCE.md for the design and the invariants.

The load-bearing distinction is `kind`:
  * common   — industry-wide demand/supply/pricing. Any witness can move it.
  * relative — a CROSS-SECTION over a cohort: how a competitive position (share,
               pricing power, qualification) is distributed across named peers. It
               yields one reading per company, never a single true/false. Only `direct`
               dimensions move it — a competitor merely expanding capacity proves
               industry supply grew, it does NOT prove our holding lost share.
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
# `resolved` belongs to cross-section (relative) claims only: they do not assert
# anything to be supported or contradicted, they ask how a position is distributed, so
# the only question is whether the comparison could be made at all.
Verdict = Literal["unknown", "supportive", "mixed", "contradicted", "falsified", "resolved"]
# How a company reads on the dimension being compared.
Standing = Literal["strong", "neutral", "weak", "unknown"]
# What the reading rests on. Not a gate — a label that travels with the reading, because
# "three companies each self-reporting" is weak per company but informative as a
# comparison: the self-report bias is common-mode and largely cancels across the cohort.
ReadingBasis = Literal["corroborated", "self_reported", "thin"]


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
    # NOTE: there is deliberately no `supports_when` here. It used to declare "this
    # direction on this dimension supports the claim", and it was the wrong shape: a
    # monotone, context-free scalar cannot express a meaning that depends on context,
    # horizon and who is speaking. Real data settled it — SK hynix saying customers
    # still want more supply WHILE pulling capacity forward was scored as seven
    # refutations of "supply stays tight", because config asserted capex-up ⇒ looser.
    # Management expands in every boom; that is a response to demand, not a denial of
    # it. Polarity now comes from an adjudicated, reasoned call per cluster
    # (chain/adjudicate.py). Config declares SCOPE and GOVERNANCE; it does not assert
    # meaning.
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
    # `relative` claims are CROSS-SECTIONS, not assertions about one name. They name the
    # cohort being compared and ask how a position is distributed across it; the answer
    # is one reading per entity, never a single true/false.
    #
    # This replaced a `subject` field, and the reason is worth keeping: with a subject,
    # the isolation gate could only admit evidence ABOUT that subject, and earnings calls
    # essentially never name a competitor. So "SK Hynix maintains its lead" rested
    # entirely on SK Hynix's own testimony — 支持3/反驳0 from one speaker — and was
    # unfalsifiable in practice. Asking instead how share and pricing read across
    # SK/Micron/Samsung lets each company's own disclosure count, for itself, and turns
    # an unanswerable question into a comparison. It is also the honest framing when we
    # hold one of the names.
    entities: list[str] = Field(default_factory=list)
    concepts: list[Concept] = Field(default_factory=list)
    # Witness stances are declared here, NOT read off the document: every filing is the
    # company's own call, so asking a model "who is speaking" always answers "this
    # company", and cross-stance corroboration could never be satisfied.
    witnesses: list[Witness] = Field(default_factory=list)
    falsifiers: list[str] = Field(default_factory=list)
    horizon: Horizon | None = None

    @field_validator("entities")
    @classmethod
    def _entities_upper(cls, v: list[str]) -> list[str]:
        return [s.upper() for s in v]

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
        if self.kind == "relative" and len(self.entities) < 2:
            raise ValueError(
                f"relative claim {self.id!r} is a cross-section and must name at least "
                f"two entities to compare")


Polarity = Literal["support", "refute", "neutral"]


class EvidenceCluster(BaseModel):
    """One de-duplicated body of evidence: everything one speaker said on one dimension
    in one direction.

    Members are kept, not collapsed to a representative row. An earnings call restates
    the same expansion plan across 2026 / FY26Q2 / FY26Q3 / 2027, and those five
    sentences are ONE piece of evidence — a plan with a schedule — not five votes.
    Judging the whole group at once is also what lets a reader see the schedule.
    """

    key: str                              # stable id: speaker|entity|concept|direction
    speaker: str                          # who disclosed it
    entity: str                           # who the facts are ABOUT
    concept: str
    direction: Direction
    stance: str = ""                      # from the claim's declared witness table
    primary: bool = True                  # PRIMARY_TYPES — may contribute a stance class
    rows: list[dict] = Field(default_factory=list)

    @property
    def periods(self) -> list[str]:
        seen = [r.get("period") or "" for r in self.rows]
        return sorted({p for p in seen if p})

    @property
    def observation_ids(self) -> list[str]:
        return [r["id"] for r in self.rows if r.get("id")]


class ClusterJudgement(BaseModel):
    """What one cluster means for one claim, and WHY.

    The reason is not decoration — it is the whole reason this may be a model's call
    rather than a config scalar. A polarity without a recorded reason is unauditable,
    and an unauditable judgement is indistinguishable from the confidently-inverted
    config it replaced.
    """

    cluster_key: str
    polarity: Polarity = "neutral"
    reason: str = ""
    speaker: str = ""
    concept: str = ""
    stance: str = ""
    primary: bool = True
    observation_ids: list[str] = Field(default_factory=list)


class EntityReading(BaseModel):
    """How one company reads on a cross-section claim's dimension.

    Deliberately per-entity and never netted into a ranking number here: the engine
    reports where each name stands and on what, and the cross-section decides what that
    is worth. `basis` travels with the reading so a self-reported "strong" cannot be
    mistaken for a corroborated one.
    """

    entity: str
    standing: Standing = "unknown"
    reason: str = ""
    basis: ReadingBasis = "thin"
    evidence_clusters: int = 0
    stance_classes: int = 0
    speakers: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)


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
    # Every polarity call with its reason. Travels with the verdict into storage and
    # into the report, so "why does SK hynix's own capex count as support here" is
    # answerable without re-running anything.
    judgements: list[ClusterJudgement] = Field(default_factory=list)
    # Cross-section claims only: one row per compared entity. `verdict` then says
    # whether the comparison could be made, not whether something is true.
    entity_readings: list[EntityReading] = Field(default_factory=list)
    note: str = ""

    @property
    def coverage(self) -> str:
        return f"{self.witnesses_reported}/{self.witnesses_expected}"


class ClaimProposal(BaseModel):
    """A proposition the system induced from evidence it could not file anywhere.

    Agents may PROPOSE; only a person adds a claim to the config. That line is what
    keeps the cross-section's factor axes stable enough for history to be comparable —
    see docs/CHAIN_EVIDENCE.md §6.5.

    `observation_ids` are frozen as discovery evidence at proposal time: the material
    that made us notice a proposition explains "why look", it may never also serve as
    "it is true". Without that freeze an agent could induce a theme from a pattern and
    then cite the very same pattern as its confirmation.
    """

    id: str = ""
    signature: str = ""                   # entity+metric fingerprint, for dedup/cooldown
    statement: str
    layer_hint: str = ""                  # may span layers — that is often the point
    kind: ClaimKind = "common"
    subject: str = ""
    concepts: list[Concept] = Field(default_factory=list)
    witnesses: list[Witness] = Field(default_factory=list)
    stance_note: str = ""                 # e.g. "供给方x3 / 需求方x0 — 立场单一"
    observation_ids: list[str] = Field(default_factory=list)
    status: str = "pending"               # pending | accepted | rejected
    created_at: datetime
    reviewed_at: datetime | None = None
    reviewer: str = ""
    rationale: str = ""

    @staticmethod
    def make_signature(entities, metrics) -> str:
        """Fingerprint the EVIDENCE, not the wording.

        Re-proposing the same idea under a new sentence must be recognised as the same
        candidate, so the signature is built from who and what — not the statement.
        """
        raw = "|".join(sorted({e.upper() for e in entities})) + "::" + \
              "|".join(sorted({m.lower() for m in metrics}))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def model_post_init(self, _ctx) -> None:
        if not self.id:
            object.__setattr__(self, "id", f"{self.signature}-{self.created_at:%Y%m%d}")
