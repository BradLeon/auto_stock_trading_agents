"""Three-gate corroboration engine — deterministic, no LLM.

Turns stored observations into a verdict per claim. Every judgement here is arithmetic
over declared config so a reader can re-derive it. The semantic step — deciding which
claim dimension a fact belongs to — happened upstream at extraction time and is stored
on the observation as `concept`; this module never matches on metric strings, because
losing a share reading to the gap between `hbm_market_share` and `hbm_share` is a real
loss dressed up as determinism.

The three gates, in order (docs/CHAIN_EVIDENCE.md §4.5):

  1. DEDUP    — cluster by (fact, stance, primary/secondary) before counting. Ten
                reprints of one report are one cluster; a company's press release,
                earnings deck and call quoting the same number are one cluster.
  2. STANCE   — a verdict needs >= 2 DIFFERENT witness stances, taken from the CLAIM's
                declared witness table. Read off the document instead, every filing is
                the company's own call and the answer is always "incumbent", so
                cross-stance corroboration could never be satisfied.
  3. ISOLATION— a `relative` claim moves ONLY on `direct` dimensions about its subject.
                A competitor expanding capacity proves industry supply grew; it does
                NOT prove our holding lost share.

Support and refute accumulate separately and are never netted: strong counter-evidence
must stay visible rather than disappear into one score.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..schemas.chain import ClaimAssessment, ClaimDef

log = logging.getLogger("ats.chain.corroborate")

# Primary material can establish a fact on its own. Secondary material is derivative:
# it may add weight but never counts as an independent witness class, which is what
# stops "three sell-side notes" or a wall of reprints from confirming anything.
PRIMARY_TYPES = {"reported_actual", "guidance", "counterparty", "regulatory"}
SECONDARY_TYPES = {"research", "media", "market"}


def _in_horizon(claim: ClaimDef, row: dict) -> bool:
    """Observations outside the claim's horizon become irrelevant, not evidence.

    "Tight through 2027" and "tight this quarter" are different propositions; without
    this, last year's reading would support this year's claim forever.
    """
    if not claim.horizon:
        return True
    try:
        when = datetime.fromisoformat(row.get("observed_at") or "").date()
    except ValueError:
        return True                      # unparseable timestamp: don't silently drop
    return claim.horizon.covers(when)


def _relevant_concept(claim: ClaimDef, row: dict):
    """Gate 3. Returns the Concept this row may move on this claim, or None."""
    concept = claim.concept(row.get("concept") or "")
    if concept is None:
        return None                      # unmapped, or belongs to a different claim
    if claim.kind == "relative":
        # The load-bearing rule of the whole design. `entity` is who the fact is ABOUT,
        # so a competitor's own capacity reading is excluded here, while a reading about
        # the subject's share — whoever disclosed it — counts.
        if (row.get("entity") or "").upper() != claim.subject:
            return None
        if not concept.direct:
            return None
    return concept


def _speaker(row: dict) -> str:
    """Who disclosed it (falls back to the subject for legacy rows)."""
    return ((row.get("source_entity") or row.get("entity")) or "").upper()


def _cluster_key(row: dict) -> tuple:
    """Gate 1. Independence is judged by originating fact + SPEAKER, not volume.

    The speaker is part of the key on purpose: the same number disclosed by the
    supplier AND independently by its customer is genuinely two witnesses, and
    collapsing those would destroy exactly the cross-stakeholder corroboration that
    gate 2 rewards. Ten outlets relaying one company's figure still share that one
    speaker, so they stay a single cluster.
    """
    src = "primary" if row.get("observation_type") in PRIMARY_TYPES else "secondary"
    return (_speaker(row), row.get("entity"), row.get("concept"),
            row.get("period"), row.get("direction"), src)


def corroborate(claim: ClaimDef, rows: list[dict], *, cfg: dict | None = None,
                as_of: datetime | None = None) -> ClaimAssessment:
    """Aggregate observations into one claim verdict. Pure function over `rows`."""
    cfg = cfg or {}
    min_clusters = cfg.get("min_clusters", 2)
    min_stances = cfg.get("min_stance_classes", 2)
    min_conf = cfg.get("min_confidence", 0.5)
    as_of = as_of or datetime.now(timezone.utc)

    expected = claim.expected_witnesses()
    assessment = ClaimAssessment(claim_id=claim.id, layer=claim.layer, as_of=as_of,
                                 witnesses_expected=len(expected))

    eligible: list[tuple[dict, object]] = []
    for r in rows:
        if float(r.get("extraction_confidence") or 1.0) < min_conf:
            continue
        if r.get("discovery_evidence"):   # material that DISCOVERED a claim may never
            continue                      # also confirm it (anti-hindsight)
        if not _in_horizon(claim, r):
            continue
        concept = _relevant_concept(claim, r)
        if concept is not None:
            eligible.append((r, concept))

    spoke = {_speaker(r) for r, _ in eligible} & expected
    assessment.witnesses_reported = len(spoke)
    # Name who stayed silent. A declared witness saying nothing is a GAP, not
    # neutrality — that is how selective disclosure becomes visible.
    assessment.silent_witnesses = sorted(expected - spoke)

    if not eligible:
        assessment.note = "无适用证据 —— 保持 unknown（注意：取不到数据不等于负面）"
        return assessment

    # Gate 1: collapse to independent clusters BEFORE any counting. Stance comes from
    # the claim's declared witness table (gate 2's premise), not from the document.
    clusters: dict[tuple, tuple[dict, object, str]] = {}
    for r, concept in eligible:
        # Stance belongs to the SPEAKER, and comes from the claim's declared witness
        # table — never from the document (invariant 8).
        stance = claim.stance_of(_speaker(r))
        clusters.setdefault(_cluster_key(r), (r, concept, stance))

    support, refute = [], []
    for r, concept, stance in clusters.values():
        direction = r.get("direction")
        if direction == concept.supports_when:
            support.append((r, stance))
        elif direction in ("up", "down"):          # the opposite of supporting
            refute.append((r, stance))
        # `flat` is context: it neither supports nor refutes.

    # Gate 2: only PRIMARY clusters contribute a witness class, and only if the claim
    # actually declared a stance for that entity.
    def _stances(items):
        return {s for r, s in items if s and r.get("observation_type") in PRIMARY_TYPES}

    stances = _stances(support) | _stances(refute)

    assessment.support_score = float(len(support))
    assessment.refute_score = float(len(refute))
    assessment.evidence_clusters = len(clusters)
    assessment.stance_classes = len(stances)
    assessment.observation_ids = [r["id"] for r, _ in eligible if r.get("id")]

    total = len(support) + len(refute)
    if total < min_clusters:
        assessment.verdict = "unknown"
        assessment.note = f"独立证据簇 {total} < {min_clusters}，证据不足"
        return assessment

    if support and refute:
        assessment.verdict = "mixed"
        losing = refute if len(support) >= len(refute) else support
        assessment.dissenters = sorted({_speaker(r) for r, _ in losing})
        assessment.note = f"支持 {len(support)} 簇 vs 反驳 {len(refute)} 簇，分歧未消解"
        return assessment

    if len(stances) < min_stances:
        # Single-stance evidence, however voluminous, cannot confirm. Say WHY, or a
        # reader takes "mixed" to mean "conflicting" rather than "unconfirmed".
        assessment.verdict = "mixed"
        assessment.note = (f"仅 {len(stances)} 类立场（{'/'.join(sorted(stances)) or '无'}），"
                           f"未达 {min_stances} 类确认门槛")
        return assessment

    assessment.verdict = "supportive" if support else "contradicted"
    assessment.note = (f"{len(support) or len(refute)} 个独立证据簇 · "
                       f"{len(stances)} 类立场（{'/'.join(sorted(stances))}）")
    if assessment.silent_witnesses:
        assessment.note += f" · 未发声：{','.join(assessment.silent_witnesses)}"
    return assessment


def assess_layer(layer, rows_by_entity, *, cfg: dict | None = None,
                 as_of: datetime | None = None) -> list[ClaimAssessment]:
    """Run every claim on one sector layer. `rows_by_entity` maps entity -> observations."""
    out = []
    for claim in layer.claims:
        entities = claim.expected_witnesses() | {w.entity.upper() for w in claim.witnesses}
        if claim.subject:
            entities.add(claim.subject)
        rows = [r for e in entities for r in rows_by_entity.get(e, [])]
        out.append(corroborate(claim, rows, cfg=cfg, as_of=as_of))
    return out
