"""Three-gate corroboration engine — deterministic, no LLM.

Turns stored observations into a verdict per claim. Every judgement here is arithmetic
over declared config so a reader can re-derive it; the LLM's only role upstream was to
extract facts with a verbatim span.

The three gates, in order (docs/CHAIN_EVIDENCE.md §4):

  1. DEDUP    — cluster by (fact, stance, primary/secondary) before counting. Ten
                reprints of one report are one cluster; a company's press release,
                earnings deck and call quoting the same number are one cluster.
  2. STANCE   — a verdict needs >= 2 DIFFERENT witness stances. Three sell-side notes
                are one witness class, not three. Secondary material (research/media/
                market) may add weight but can never contribute a stance class.
  3. ISOLATION— a `relative` claim moves ONLY on direct evidence about its subject.
                A competitor expanding capacity proves industry supply grew; it does
                NOT prove our holding lost share. That inference needs share /
                qualification / ASP evidence about the subject itself.

Support and refute are accumulated separately and never netted: strong counter-evidence
must stay visible rather than disappear into a single score.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..schemas.chain import ClaimAssessment, ClaimDef

log = logging.getLogger("ats.chain.corroborate")

# Primary material can establish a fact on its own. Secondary material is derivative:
# it may corroborate weight but never counts as an independent witness class, which is
# what stops "three sell-side notes" or a wall of reprints from confirming anything.
PRIMARY_TYPES = {"reported_actual", "guidance", "counterparty", "regulatory"}
SECONDARY_TYPES = {"research", "media", "market"}


def _in_horizon(claim: ClaimDef, row: dict) -> bool:
    """Observations outside the claim's horizon decay to irrelevant, not to evidence.

    "Tight through 2027" and "tight this quarter" are different propositions; without
    this, last year's reading would support this year's claim forever.
    """
    if not claim.horizon:
        return True
    stamp = row.get("observed_at") or ""
    try:
        when = datetime.fromisoformat(stamp).date()
    except ValueError:
        return True                      # unparseable timestamp: don't silently drop
    return claim.horizon.covers(when)


def _is_relevant(claim: ClaimDef, row: dict) -> bool:
    """Gate 3. Decides whether this observation is allowed to move THIS claim."""
    metric = (row.get("metric") or "").lower()
    entity = (row.get("entity") or "").upper()
    if claim.kind == "relative":
        # The load-bearing rule of the whole design. `entity` is who the fact is ABOUT
        # (see schemas/chain.py), so a competitor's own capacity reading has
        # entity=competitor and is correctly excluded here; a reading about the
        # subject's share — whoever disclosed it — has entity=subject and counts.
        if entity != claim.subject:
            return False
        return metric in {m.lower() for m in claim.direct_metrics}
    return not claim.metrics or metric in {m.lower() for m in claim.metrics}


def _cluster_key(row: dict) -> tuple:
    """Gate 1. Independence is judged by originating fact + speaker class, not volume.

    Stance is part of the key on purpose: the same number disclosed by the company AND
    independently by a customer is genuinely two witnesses, and collapsing those would
    destroy exactly the cross-stakeholder corroboration gate 2 rewards.
    """
    src = "primary" if row.get("observation_type") in PRIMARY_TYPES else "secondary"
    return (row.get("entity"), row.get("metric"), row.get("period"),
            row.get("direction"), row.get("stance"), src)


def corroborate(claim: ClaimDef, rows: list[dict], *, cfg: dict | None = None,
                as_of: datetime | None = None) -> ClaimAssessment:
    """Aggregate observations into one claim verdict. Pure function over `rows`."""
    cfg = cfg or {}
    min_clusters = cfg.get("min_clusters", 2)
    min_stances = cfg.get("min_stance_classes", 2)
    min_conf = cfg.get("min_confidence", 0.5)
    as_of = as_of or datetime.now(timezone.utc)

    expected = {w.entity.upper() for w in claim.witnesses}
    assessment = ClaimAssessment(claim_id=claim.id, layer=claim.layer, as_of=as_of,
                                 witnesses_expected=len(expected))

    eligible = [
        r for r in rows
        if float(r.get("extraction_confidence") or 1.0) >= min_conf
        and not r.get("discovery_evidence")      # material that DISCOVERED a claim
        and _in_horizon(claim, r)                # may never also confirm it
        and _is_relevant(claim, r)
    ]
    if not eligible:
        assessment.note = "无适用证据 —— 保持 unknown（注意：取不到数据不等于负面）"
        return assessment

    # Gate 1: collapse to independent clusters BEFORE any counting.
    clusters: dict[tuple, dict] = {}
    for r in eligible:
        clusters.setdefault(_cluster_key(r), r)

    support, refute, ctx = [], [], []
    for key, r in clusters.items():
        direction = r.get("direction")
        # Polarity is per-metric: one claim can mix metrics that point opposite ways
        # (under "supply stays tight", lead_time UP supports but capacity UP refutes).
        supporting = claim.polarity_of(r.get("metric") or "")
        if direction == supporting:
            support.append((key, r))
        elif direction in ("up", "down"):          # the opposite of supporting
            refute.append((key, r))
        else:
            ctx.append((key, r))

    # Gate 2: only PRIMARY clusters contribute a witness class.
    def _stances(items):
        return {k[4] for k, r in items if r.get("observation_type") in PRIMARY_TYPES}

    stances = _stances(support) | _stances(refute)
    reported = {r.get("entity") for _, r in clusters.items()} & expected

    assessment.support_score = float(len(support))
    assessment.refute_score = float(len(refute))
    assessment.evidence_clusters = len(clusters)
    assessment.stance_classes = len(stances)
    assessment.witnesses_reported = len(reported)
    assessment.observation_ids = [r["id"] for r in eligible if r.get("id")]

    total = len(support) + len(refute)
    if total < min_clusters:
        assessment.verdict = "unknown"
        assessment.note = f"独立证据簇 {total} < {min_clusters}，证据不足"
        return assessment

    # Both sides substantial -> mixed, and the two scores stay visible either way.
    if support and refute:
        assessment.verdict = "mixed"
        assessment.dissenters = sorted({r.get("entity") for _, r in
                                        (refute if len(support) >= len(refute) else support)})
        assessment.note = f"支持 {len(support)} 簇 vs 反驳 {len(refute)} 簇，分歧未消解"
        return assessment

    if len(stances) < min_stances:
        # Single-stance evidence, however voluminous, cannot confirm. Say WHY, or the
        # reader would read "mixed" as "conflicting evidence" rather than "unconfirmed".
        assessment.verdict = "mixed"
        assessment.note = (f"仅 {len(stances)} 类立场（{'/'.join(sorted(stances)) or '无'}），"
                           f"未达 {min_stances} 类确认门槛")
        return assessment

    assessment.verdict = "supportive" if support else "contradicted"
    assessment.note = (f"{len(support) or len(refute)} 个独立证据簇 · "
                       f"{len(stances)} 类立场（{'/'.join(sorted(stances))}）")
    return assessment


def assess_layer(layer, rows_by_entity, *, cfg: dict | None = None,
                 as_of: datetime | None = None) -> list[ClaimAssessment]:
    """Run every claim on one sector layer. `rows_by_entity` maps entity -> observations."""
    out = []
    for claim in layer.claims:
        entities = {w.entity.upper() for w in claim.witnesses}
        if claim.kind == "relative" and claim.subject:
            entities.add(claim.subject)
        rows = [r for e in entities for r in rows_by_entity.get(e, [])]
        out.append(corroborate(claim, rows, cfg=cfg, as_of=as_of))
    return out
