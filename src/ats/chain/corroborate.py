"""Three-gate corroboration engine — evidence hygiene and arithmetic.

Turns stored observations into a verdict per claim. The division of labour is the point:

  * THIS MODULE owns hygiene and scoring — which observations are eligible, how they
    de-duplicate, whose stances are represented, and the arithmetic on top. None of
    that needs to understand what a sentence means, so none of it is a model's call.
  * SEMANTICS are two injected judgements. Which dimension a fact belongs to is decided
    at extraction time and stored as `concept` (never matched on metric strings —
    losing a share reading to the gap between `hbm_market_share` and `hbm_share` is a
    real loss dressed up as determinism). What a cluster MEANS for a claim is decided
    by an adjudicator passed in as `judge`, one reasoned call per cluster.

Given the judgements, everything here is re-derivable arithmetic over declared config.

The three gates, in order (docs/CHAIN_EVIDENCE.md §4.5):

  1. DEDUP    — cluster by (speaker, fact, direction, primary/secondary) before
                counting. Ten reprints of one report are one cluster; a company's press
                release, earnings deck and call quoting the same number are one cluster;
                and one call restating a plan across five fiscal periods is one cluster.
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

from ..schemas.chain import (ClaimAssessment, ClaimDef, EntityReading,
                            EvidenceCluster)

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
    """Gate 3. Returns the Concept this row may move on this claim, or None.

    For a cross-section (`relative`) claim the rule is: only `direct` dimensions — share,
    qualification, pricing — and only about a company in the declared cohort. The
    grouping then happens by `entity`, so each company's reading lands in its own row.

    This is the isolation that matters, and it is unchanged in substance: a competitor's
    capacity expansion still cannot become a statement about our holding's share,
    because capacity is not a `direct` dimension. What changed is that the competitor's
    own SHARE reading now counts — for the competitor — instead of being discarded.
    """
    concept = claim.concept(row.get("concept") or "")
    if concept is None:
        return None                      # unmapped, or belongs to a different claim
    if claim.kind == "relative":
        if not concept.direct:
            return None
        if (row.get("entity") or "").upper() not in set(claim.entities):
            return None
    return concept


def _speaker(row: dict) -> str:
    """Who disclosed it (falls back to the subject for legacy rows)."""
    return ((row.get("source_entity") or row.get("entity")) or "").upper()


def _source_stance(entity: str) -> str:
    """Declared stance of a third-party source, or "" if this is not one."""
    from .sources import load_sources

    return next((s.stance for s in load_sources() if s.entity == entity), "")


def _cluster_key(row: dict) -> tuple:
    """Gate 1. Independence is judged by originating fact + SPEAKER, not volume.

    The speaker is part of the key on purpose: the same number disclosed by the
    supplier AND independently by its customer is genuinely two witnesses, and
    collapsing those would destroy exactly the cross-stakeholder corroboration that
    gate 2 rewards. Ten outlets relaying one company's figure still share that one
    speaker, so they stay a single cluster.

    `period` is deliberately NOT in the key. It used to be, and gate 1 failed at its own
    job: one SK hynix call restated a single expansion plan across 2026 / FY26 / FY26Q2
    / FY26Q3 / 2027, and those five sentences became five independent refutations that
    outvoted two genuine supporting clusters. An earnings call naturally narrates one
    plan across several horizons — that is one piece of evidence with a schedule, and
    keeping the members together is what lets a reader (and the adjudicator) see the
    schedule instead of a stack of votes.
    """
    src = "primary" if row.get("observation_type") in PRIMARY_TYPES else "secondary"
    return (_speaker(row), row.get("entity"), row.get("concept"),
            row.get("direction"), src)


def build_clusters(claim: ClaimDef, rows: list[dict], *,
                   cfg: dict | None = None) -> list[EvidenceCluster]:
    """Gates 1 + 3 + eligibility. The de-duplicated evidence a claim actually rests on.

    Separated from scoring so the adjudicator can be handed exactly this — nothing more.
    It sees no prices, no positions, no P&L and no previous verdict, and it cannot add
    or remove a cluster: the set is fixed here, before it runs.
    """
    cfg = cfg or {}
    min_conf = cfg.get("min_confidence", 0.5)

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

    clusters: dict[tuple, EvidenceCluster] = {}
    for r, _concept in eligible:
        key = _cluster_key(r)
        if key not in clusters:
            # Stance belongs to the SPEAKER, and comes from the claim's declared witness
            # table — never from the document (invariant 8).
            clusters[key] = EvidenceCluster(
                key="|".join(str(k) for k in key), speaker=_speaker(r),
                entity=(r.get("entity") or "").upper(), concept=r.get("concept") or "",
                direction=r.get("direction") or "flat",
                # Companies get their stance from the claim's witness table; a customs
                # bureau is in no claim's witness table, so it declares its own in
                # config/sources.yaml. Either way the stance is DECLARED, never read
                # off the material — which is the invariant that matters.
                stance=claim.stance_of(_speaker(r)) or _source_stance(_speaker(r)),
                primary=r.get("observation_type") in PRIMARY_TYPES)
        clusters[key].rows.append(r)
    return list(clusters.values())


def cross_section(claim: ClaimDef, rows: list[dict], *, cfg: dict | None = None,
                  as_of: datetime | None = None, judge=None) -> ClaimAssessment:
    """Read a `relative` claim as a comparison: one standing per declared entity.

    There is no single verdict here on purpose. "SK Hynix maintains its lead" was a
    question only SK Hynix's own call ever answered, because competitors do not discuss
    each other; "how do share and pricing read across SK / Micron / Samsung" is answered
    by all three, each about itself, and the comparison is what the cross-section factor
    actually needs.
    """
    cfg = cfg or {}
    min_clusters = cfg.get("min_clusters", 2)
    as_of = as_of or datetime.now(timezone.utc)

    assessment = ClaimAssessment(claim_id=claim.id, layer=claim.layer, as_of=as_of,
                                 witnesses_expected=len(claim.entities))
    clusters = build_clusters(claim, rows, cfg=cfg)
    by_entity: dict[str, list] = {e: [] for e in claim.entities}
    for c in clusters:
        by_entity.setdefault(c.entity, []).append(c)

    heard = [e for e in claim.entities if by_entity.get(e)]
    assessment.witnesses_reported = len(heard)
    assessment.silent_witnesses = [e for e in claim.entities if not by_entity.get(e)]
    assessment.evidence_clusters = len(clusters)

    if not clusters:
        assessment.note = "无适用证据 —— 无法比较（注意：取不到数据不等于负面）"
        return assessment

    if judge is None:
        from ..agents.evidence.adjudicator import judge_cross_section as _default

        judge = _default
    readings = {r.entity: r for r in judge(claim, by_entity)}

    out = []
    for entity in claim.entities:
        group = by_entity.get(entity) or []
        reading = readings.get(entity) or EntityReading(entity=entity)
        stances = {c.stance for c in group if c.stance and c.primary}
        speakers = sorted({c.speaker for c in group})
        reading.evidence_clusters = len(group)
        reading.stance_classes = len(stances)
        reading.speakers = speakers
        reading.observation_ids = [i for c in group for i in c.observation_ids]
        # Resolve the clusters the adjudicator cited into the spans a consumer may print
        # under this standing. Everything else stays in `observation_ids` — visible, but
        # never passed off as the justification.
        cited = set(reading.key_clusters)
        reading.key_observation_ids = [i for c in group if c.key in cited
                                       for i in c.observation_ids]
        if not group:
            reading.standing = "unknown"
            reading.basis = "thin"
            reading.reason = reading.reason or "本期未发声"
        elif len(group) < min_clusters:
            reading.basis = "thin"
        elif len(stances) >= 2:
            reading.basis = "corroborated"
        else:
            # Only the company itself spoke. Kept, not dropped: across a cohort where
            # every name self-reports, the bias is common-mode and the COMPARISON still
            # carries information — which a single self-reported claim would not.
            reading.basis = "self_reported"
        out.append(reading)

    assessment.entity_readings = out
    assessment.stance_classes = len({c.stance for c in clusters if c.stance and c.primary})
    graded = [r for r in out if r.standing != "unknown"]
    assessment.verdict = "resolved" if len(graded) >= 2 else "unknown"
    assessment.observation_ids = [i for c in clusters for i in c.observation_ids]
    assessment.note = (
        f"{len(graded)}/{len(claim.entities)} 家有读数 · "
        + " · ".join(f"{r.entity} {r.standing}({r.basis})" for r in graded)
        if graded else "读数不足两家，无法构成比较")
    return assessment


def corroborate(claim: ClaimDef, rows: list[dict], *, cfg: dict | None = None,
                as_of: datetime | None = None, judge=None) -> ClaimAssessment:
    """Aggregate observations into one claim verdict.

    `judge(claim, clusters) -> list[ClusterJudgement]` supplies polarity. Pure given
    that: the same clusters and the same judgements always produce the same verdict.
    Defaults to the LLM adjudicator; tests inject a stub.
    """
    if claim.kind == "relative":
        return cross_section(claim, rows, cfg=cfg, as_of=as_of, judge=judge)
    cfg = cfg or {}
    min_clusters = cfg.get("min_clusters", 2)
    min_stances = cfg.get("min_stance_classes", 2)
    min_conf = cfg.get("min_confidence", 0.5)
    # How much dissent overturns a majority. EITHER condition suffices: a few
    # independent dissenting clusters matter even in a large body of evidence, and a
    # large share matters even when the body is small.
    min_dissent_clusters = cfg.get("min_dissent_clusters", 3)
    min_dissent_share = cfg.get("min_dissent_share", 0.20)
    as_of = as_of or datetime.now(timezone.utc)

    expected = claim.expected_witnesses()
    assessment = ClaimAssessment(claim_id=claim.id, layer=claim.layer, as_of=as_of,
                                 witnesses_expected=len(expected))

    clusters = build_clusters(claim, rows, cfg=cfg)

    spoke = {c.speaker for c in clusters} & expected
    assessment.witnesses_reported = len(spoke)
    # Name who stayed silent. A declared witness saying nothing is a GAP, not
    # neutrality — that is how selective disclosure becomes visible.
    assessment.silent_witnesses = sorted(expected - spoke)

    if not clusters:
        assessment.note = "无适用证据 —— 保持 unknown（注意：取不到数据不等于负面）"
        return assessment

    if judge is None:
        from ..agents.evidence.adjudicator import judge as _default_judge

        judge = _default_judge
    judgements = judge(claim, clusters)
    assessment.judgements = judgements
    by_key = {c.key: c for c in clusters}

    support, refute = [], []
    for j in judgements:
        cluster = by_key.get(j.cluster_key)
        if cluster is None:
            continue
        if j.polarity == "support":
            support.append(cluster)
        elif j.polarity == "refute":
            refute.append(cluster)
        # `neutral` is context: it neither supports nor refutes.

    # Gate 2: only PRIMARY clusters contribute a witness class, and only if the claim
    # actually declared a stance for that entity.
    def _stances(items):
        return {c.stance for c in items if c.stance and c.primary}

    stances = _stances(support) | _stances(refute)

    assessment.support_score = float(len(support))
    assessment.refute_score = float(len(refute))
    assessment.evidence_clusters = len(clusters)
    assessment.stance_classes = len(stances)
    assessment.observation_ids = [i for c in clusters for i in c.observation_ids]

    total = len(support) + len(refute)
    if total < min_clusters:
        assessment.verdict = "unknown"
        assessment.note = f"独立证据簇 {total} < {min_clusters}，证据不足"
        return assessment

    # `mixed` means "the disagreement is unresolved", and a lone dissenting cluster
    # against twenty-seven is not a disagreement — it is one reading worth checking.
    # The old rule (any refute at all -> mixed) made that distinction impossible, and
    # it got worse as coverage improved: with 38 clusters the same data returned
    # `mixed 26/1` and `supportive 28/0` on consecutive runs, because the adjudicator
    # wavers on borderline clusters and one flip decided the verdict.
    #
    # So the minority must be MATERIAL to overturn: either several independent clusters
    # or a real share of the evidence. Below that the majority stands — but the
    # dissenters are still named, and the note still says the dissent exists. It is
    # demoted from veto to caveat, never hidden.
    minority = min(support, refute, key=len) if (support and refute) else []
    if minority:
        assessment.dissenters = sorted({c.speaker for c in minority})
    material = bool(minority) and (
        len(minority) >= min_dissent_clusters
        or len(minority) / total >= min_dissent_share)

    if material:
        assessment.verdict = "mixed"
        assessment.note = f"支持 {len(support)} 簇 vs 反驳 {len(refute)} 簇，分歧未消解"
        return assessment

    if len(stances) < min_stances:
        # Single-stance evidence, however voluminous, cannot confirm. Say WHY, or a
        # reader takes "mixed" to mean "conflicting" rather than "unconfirmed".
        assessment.verdict = "mixed"
        assessment.note = (f"仅 {len(stances)} 类立场（{'/'.join(sorted(stances)) or '无'}），"
                           f"未达 {min_stances} 类确认门槛")
        return assessment

    assessment.verdict = "supportive" if len(support) >= len(refute) else "contradicted"
    assessment.note = (f"{max(len(support), len(refute))} 个独立证据簇 · "
                       f"{len(stances)} 类立场（{'/'.join(sorted(stances))}）")
    if minority:
        assessment.note += (f" · ⚠️ 另有 {len(minority)} 簇异议"
                            f"（{','.join(assessment.dissenters)}），未达翻案门槛")
    if assessment.silent_witnesses:
        assessment.note += f" · 未发声：{','.join(assessment.silent_witnesses)}"
    return assessment


def assess_layer(layer, rows_by_entity, *, cfg: dict | None = None,
                 as_of: datetime | None = None, judge=None) -> list[ClaimAssessment]:
    """Run every claim on one sector layer. `rows_by_entity` maps entity -> observations."""
    from .sources import sources_for_concepts

    out = []
    for claim in layer.claims:
        entities = claim.expected_witnesses() | {w.entity.upper() for w in claim.witnesses}
        entities |= set(claim.entities)
        # Third-party sources bind by DIMENSION, not by being named in the claim: a
        # source declares which concepts it may speak to, a claim declares which it is
        # tested on, and they meet on the concept key. That is what lets a source be
        # added without touching any claim, and vice versa.
        entities |= {s.entity for s in sources_for_concepts({c.key for c in claim.concepts})}
        rows = [r for e in entities for r in rows_by_entity.get(e, [])]
        out.append(corroborate(claim, rows, cfg=cfg, as_of=as_of, judge=judge))
    return out
