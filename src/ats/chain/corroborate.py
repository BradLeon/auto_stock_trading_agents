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
  2. STANCE   — vantage diversity is GRADED, not required. >= 2 declared stances gives
                `basis=corroborated`; one stance still yields the verdict as
                `basis=self_reported` provided MIN_INDEPENDENT_SPEAKERS separate filers
                spoke. Only below that does the engine decline. Stances come from the
                CLAIM's declared witness table — read off the document instead, every
                filing is the company's own call and the answer is always "incumbent".
  3. ISOLATION— a `relative` claim moves ONLY on `direct` dimensions about its subject.
                A competitor expanding capacity proves industry supply grew; it does
                NOT prove our holding lost share.

Gate 2 was a veto until 2026-08-15 and is now a grade. The veto conflated two different
properties: whether the speakers are INDEPENDENT, and whether they sit at DIFFERENT
points in the chain. One company filing three documents fails both — that is real
self-confirmation and is still blocked. But four competing suppliers agreeing fails only
the second, and they are the least likely parties on earth to coordinate a story: if
Lumentum says it is sold out, Coherent has every incentive to say it has capacity.
Blocking that case returned `mixed` — a word meaning "the evidence conflicts" — over
7 supporting clusters and 0 refuting ones, which is not a cautious reading but a wrong
one. The cross-section path had already reached the right answer (EntityReading.basis:
"the bias is common-mode and the COMPARISON still carries information"); this brings the
two paths into line and deletes the per-claim `min_stance_classes` override that existed
only to buy exemptions from the veto.

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
# When only one vantage point is represented, this many INDEPENDENT filers must have
# spoken for the reading to stand (as `self_reported`). It is the substitute for vantage
# diversity, not a waiver of it: the failure mode gate 2 exists to stop is one interested
# party confirming itself, and that is a statement about speaker count, not about seats
# in the value chain. Below this the verdict is withheld.
MIN_INDEPENDENT_SPEAKERS = 3
# How much bigger the majority must be than the minority for the direction to be
# callable. Below this the claim is `mixed` — genuinely indecisive, not merely
# contested. Stated as a ratio on purpose: an absolute dissent count makes 3-against-34
# look like the same kind of doubt as 3-against-5, and a share threshold has to be
# picked, which is how the previous rule ended up drawing the line between "分歧" and
# "印证" two percentage points apart.
DECISIVE_MAJORITY_RATIO = 2.0
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
                # config/data/sources.yaml. Either way the stance is DECLARED, never read
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
    assessment.speakers = sorted({c.speaker for c in clusters if c.speaker})
    graded = [r for r in out if r.standing != "unknown"]
    assessment.verdict = "resolved" if len(graded) >= 2 else "unknown"
    # The cohort-level basis is the weakest link: one corroborated reading does not make
    # the comparison corroborated if the others are all self-reported.
    if graded:
        rank = {"thin": 0, "self_reported": 1, "corroborated": 2}
        assessment.basis = min((r.basis for r in graded), key=lambda b: rank.get(b, 0))
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
    # `min_dissent_clusters` / `min_dissent_share` used to be read here. Both are gone:
    # dissent no longer vetoes, it is named (see DECISIVE_MAJORITY_RATIO).
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

    # `mixed` means "I cannot tell you which way this reads" — nothing weaker. A
    # minority does not overturn a majority merely by existing; it overturns it by
    # making the majority INDECISIVE. The test is therefore a ratio, not a count:
    # unless the majority is at least twice the minority, the split is too close to
    # call and the claim is genuinely unresolved.
    #
    # Two earlier rules failed here, in opposite directions. "Any refute at all ->
    # mixed" made a lone dissenting cluster veto twenty-seven. Replacing it with
    # ">= 3 clusters OR >= 20% share" was still a veto, just a higher one, and the
    # thresholds turned out to sit exactly where the data lives: measured across the
    # book, `capex_funding_quality` tripped at 3 clusters and 20.0% — both conditions
    # at their exact boundary — while `asic_substitution_delivering` passed at 18%.
    # A two-point difference decided between "分歧" and "印证", and the adjudicator's
    # own wobble moved the same claim between 3:12 and 4:15 on consecutive runs. A
    # threshold that unstable is not measuring anything.
    #
    # What the minority IS worth is naming. "Every hyperscaler's free cash flow is
    # tight except Microsoft" is a finding — and a more useful one than the majority
    # alone — but only if Microsoft is reported as a named exception rather than
    # averaged away into "unresolved". So dissent is preserved in full (`dissenters`,
    # and every `judgements` row with its reason) and travels with the verdict.
    minority = min(support, refute, key=len) if (support and refute) else []
    majority = max(support, refute, key=len) if (support and refute) else (support or refute)
    if minority:
        assessment.dissenters = sorted({c.speaker for c in minority})
    indecisive = bool(minority) and len(majority) < DECISIVE_MAJORITY_RATIO * len(minority)

    if indecisive:
        assessment.verdict = "mixed"
        assessment.unresolved_reason = "dissent"
        assessment.note = (f"支持 {len(support)} 簇 vs 反驳 {len(refute)} 簇，"
                           f"多数不足少数的 {DECISIVE_MAJORITY_RATIO:g} 倍，方向无法判定")
        return assessment

    # Gate 2, as a grade. Vantage diversity is what we WANT; independent-speaker count
    # is what we REQUIRE. One company filing three documents fails both and is still
    # withheld; four competing suppliers agreeing fails only the first, and is reported
    # with the caveat attached rather than thrown away.
    # PRIMARY only, exactly as the stance count is. Secondary material may add weight
    # but can never establish a fact on its own, and the speaker floor is the substitute
    # for the stance requirement — so counting research houses here would let three
    # sell-side notes confirm a claim through the back door, which is the single thing
    # PRIMARY_TYPES exists to prevent.
    speakers = sorted({c.speaker for c in (support + refute) if c.speaker and c.primary})
    assessment.speakers = speakers

    if len(stances) < min_stances and len(speakers) < MIN_INDEPENDENT_SPEAKERS:
        # One vantage AND too few filers — this is the self-confirmation case the gate
        # exists for. `unknown`, not `mixed`: nothing here conflicts, there is simply
        # not enough independent testimony to stand on. Say which way it leans anyway,
        # or "unconfirmed" reads as "we learned nothing".
        assessment.verdict = "unknown"
        assessment.basis = "thin"
        assessment.unresolved_reason = "single_stance"
        lean = ("反驳" if len(refute) > len(support)
                else "支持" if len(support) > len(refute) else "两向持平")
        tilt = (f"证据一边倒（{lean} {max(len(support), len(refute))} 簇 vs "
                f"{min(len(support), len(refute))} 簇）" if support or refute else "无方向性证据")
        assessment.note = (
            f"{tilt}，但仅 {len(stances)} 类立场（{'/'.join(sorted(stances)) or '无'}）"
            f"且只有 {len(speakers)} 个独立说话人（需 {MIN_INDEPENDENT_SPEAKERS} 个）"
            f"——证据来自同一视角的少数几方，不足以独立成立")
        return assessment

    assessment.basis = "corroborated" if len(stances) >= min_stances else "self_reported"
    assessment.verdict = "supportive" if len(support) >= len(refute) else "contradicted"
    assessment.note = (f"{max(len(support), len(refute))} 个独立证据簇 · "
                       f"{len(stances)} 类立场（{'/'.join(sorted(stances))}）")
    if assessment.basis == "self_reported":
        # Never let single-vantage support pass as if it were corroborated: the reader
        # must see that everyone who spoke sits on the same side of the table.
        assessment.note += (f" · ⚠️ 仅自述（{'/'.join(sorted(stances))} 单一视角），"
                            f"由 {len(speakers)} 个独立说话人支撑：{','.join(speakers)}")
    if minority:
        # Framed as an EXCEPTION, not as a failed challenge. "未达翻案门槛" told the
        # reader the minority had tried and lost, which invites skipping it — but the
        # minority is often the most informative row in the claim: if every hyperscaler
        # but one reports tightening cash flow, the one is what a reader should look at.
        assessment.note += (f" · 具名例外：{','.join(assessment.dissenters)}"
                            f"（{len(minority)} 簇，反向读数已保留）")
    if assessment.silent_witnesses:
        assessment.note += f" · 未发声：{','.join(assessment.silent_witnesses)}"
    # An unjudged cluster and a genuinely neutral one are the same row downstream, so a
    # partial adjudication does not look like a failure — it looks like a claim with
    # less support than it has. Measured 2026-08-18: 11 of 17 clusters came back
    # unjudged and the weekly output showed only the reduced score, with nothing to
    # distinguish it from a claim whose evidence really was that thin.
    unjudged = sum(1 for j in judgements if j.reason == "未获判读")
    if unjudged:
        assessment.note += (f" · ⚠️ {unjudged}/{len(judgements)} 簇未获判读"
                            f"（判读器本轮未返回，已按中性处理——这是缺口不是中性结论）")
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
