"""Cluster adjudicator — what does this body of evidence MEAN for this claim?

The one semantic call in the corroboration path, and the only thing here that is not
arithmetic. It exists because the thing it replaced was the wrong shape: config used to
declare `supports_when: up|down` per dimension, a monotone context-free scalar standing
in for a meaning that depends on context, horizon and speaker. Real data settled it —
SK hynix saying customers still want more supply WHILE pulling capacity forward scored
as seven refutations of "supply stays tight", because config asserted capex-up ⇒ looser.
Management expands in every boom; that is a response to demand, not a denial of it.

Deliberately blind, so that moving this judgement to a model does not reopen the
narrative bias the gates exist to prevent. It sees:

    the claim sentence · one cluster's verbatim spans · who spoke · who it is about
    · the periods covered

It does NOT see prices, positions, P&L, the previous verdict, or the other claims. It
cannot add or drop a cluster — the set is fixed by the deterministic gates before it
runs. It cannot change coverage or stance counts, which are computed from that same
fixed set. All it decides is support / refute / neutral, and it must write one line
saying why; that line is stored and printed. A polarity without a recorded reason is
unauditable, and an unauditable judgement is no better than the inverted config.

Short context by construction: one claim, ~10 clusters, a couple of sentences each —
a few thousand characters. That is the regime cheap models are reliable in, and the
opposite of the long-document extraction that needed a menu moved to the end.
"""

from __future__ import annotations

import logging

from ...schemas.chain import ClaimDef, ClusterJudgement, EvidenceCluster
from ..base import run_structured
from .outputs import AdjudicationView

log = logging.getLogger("ats.agents.evidence.adjudicator")

MAX_SPANS = 6            # per cluster; a call restating one plan rarely needs more
SPAN_CHARS = 220


def _cluster_block(claim: ClaimDef, clusters: list[EvidenceCluster]) -> str:
    out = []
    for i, c in enumerate(clusters, 1):
        concept = claim.concept(c.concept)
        about = f"（关于 {c.entity}）" if c.entity and c.entity != c.speaker else ""
        periods = f" · 覆盖期间 {', '.join(c.periods)}" if c.periods else ""
        head = (f"[{i}] id={c.key}\n"
                f"    说话人：{c.speaker}{about} · 维度：{c.concept}"
                f"（{concept.desc if concept else ''}）· 读数方向：{c.direction}{periods}")
        spans = [f"      · {(r.get('evidence_span') or '')[:SPAN_CHARS]}"
                 for r in c.rows[:MAX_SPANS]]
        if len(c.rows) > MAX_SPANS:
            spans.append(f"      · （另有 {len(c.rows) - MAX_SPANS} 条同类表述）")
        out.append(head + "\n" + "\n".join(spans))
    return "\n\n".join(out)


def build_context(claim: ClaimDef, clusters: list[EvidenceCluster]) -> str:
    return (
        f"命题：{claim.statement or claim.id}\n"
        + (f"证伪条件：{'；'.join(claim.falsifiers)}\n" if claim.falsifiers else "")
        + "\n下面每一组是**一份独立证据**——同一个说话人在同一维度同一方向上的全部表述，"
        "已经去过重。同一组里跨多个期间的表述属于同一件事（例如一个分年度铺开的扩产计划），"
        "请整体判断，不要拆开当成多条。\n\n"
        + _cluster_block(claim, clusters)
        + "\n\n逐组判断它对上面这条命题意味着什么：\n"
        "  support  —— 这组证据让命题更可能成立\n"
        "  refute   —— 让命题更不可能成立\n"
        "  neutral  —— 是背景，不构成任一方向的证据\n\n"
        "判断要点：\n"
        "- **看机制，不要看方向词。**同一个方向在不同命题下含义相反：景气周期里扩产通常是"
        "  对需求强度的印证，不是对紧张的否定；而如果证据显示产能增速已经跑到需求前面、"
        "  或客户开始要求降价，那才是反证。请结合期间与上下文判断。\n"
        "- **注意说话人是谁、说的是谁。**相对命题下，主体自己的进展与竞争者的进展方向相反。\n"
        "- 拿不准就给 neutral。判错方向比判成中性代价大得多。\n"
        "- reason 一句话讲清**为什么**，要引到具体内容，不要复述 support/refute。\n"
        "- 你只判断证据含义；**不要**给买卖建议、目标价、仓位，也不要评价这条命题好不好。"
    )


# A judged fraction below this is treated as a failed call rather than as a set of
# neutral findings, and the call is made once more. Measured 2026-08-18: one run of a
# 17-cluster claim came back with 7 judgements (one of them mis-keyed), so 11 clusters
# defaulted to neutral — and an immediate re-run of the identical prompt returned all 17
# with every key correct. The shortfall is flake, not capacity: the prompt was 11k chars,
# and a 16-cluster claim in the same batch was judged in full.
#
# Retrying is the right response precisely BECAUSE it is flake. Switching to a stronger
# model would not help — the same model, same context, same prompt already succeeds.
MIN_JUDGED_FRACTION = 0.8


def _resolve_key(raw: str, by_key: dict, clusters: list[EvidenceCluster]) -> EvidenceCluster | None:
    """Match a returned cluster id, tolerating a truncated tail — but never ambiguity.

    Keys are `speaker|entity|concept|direction|primary`, and the observed flake was the
    model echoing `AMD|AMD|xpu_order_visibility|up` — correct, but missing the fifth
    segment. Dropping that judgement threw away a real reading over a formatting slip.

    Prefix matching is only accepted when it identifies exactly ONE cluster. Two clusters
    can differ solely in that last segment (the same speaker on the same dimension, once
    in a filing and once in a press summary), and silently assigning a judgement to the
    wrong one would be worse than not using it at all.
    """
    if raw in by_key:
        return by_key[raw]
    hits = [c for c in clusters if c.key.startswith(f"{raw}|")]
    return hits[0] if len(hits) == 1 else None


def judge(claim: ClaimDef, clusters: list[EvidenceCluster]) -> list[ClusterJudgement]:
    """Adjudicate every cluster. Never raises — degrades to all-neutral.

    Degrading to neutral rather than to a guess is the safe direction: a failed
    adjudication leaves the claim `unknown` for want of evidence, which is honest,
    whereas defaulting to a polarity would manufacture a verdict out of an outage.

    What that safety costs, and why the retry above exists: an unjudged cluster and a
    genuinely neutral one are the same row downstream. A partial response therefore does
    not look like a failure, it looks like a claim with less support than it has. The
    count of unjudged clusters is returned to the caller (see `corroborate`) so the
    shortfall reaches the reader instead of dissolving into the verdict.
    """
    if not clusters:
        return []

    seen: dict[str, ClusterJudgement] = {}
    for attempt in (1, 2):
        try:
            view = run_structured("evidence_adjudicator", AdjudicationView,
                                  build_context(claim, clusters),
                                  skill_slug="evidence-adjudicator")
        except Exception as exc:  # noqa: BLE001 - one claim must not break the run
            log.warning("adjudicator failed for claim %s (attempt %d): %s",
                        claim.id, attempt, exc)
            view = None

        by_key = {c.key: c for c in clusters}
        for item in (getattr(view, "judgements", None) or []):
            cluster = _resolve_key(item.cluster_key, by_key, clusters)
            if cluster is None:
                log.info("adjudicator: unresolvable cluster id %r on claim %s — dropped",
                         item.cluster_key, claim.id)
                continue                  # it may not invent clusters
            seen[cluster.key] = ClusterJudgement(
                cluster_key=cluster.key, polarity=item.polarity,
                reason=(item.reason or "").strip(), speaker=cluster.speaker,
                concept=cluster.concept, stance=cluster.stance,
                primary=cluster.primary, observation_ids=cluster.observation_ids)

        if len(seen) >= MIN_JUDGED_FRACTION * len(clusters):
            break
        if attempt == 1:
            log.warning("adjudicator: %s judged %d/%d clusters — retrying",
                        claim.id, len(seen), len(clusters))

    unjudged = len(clusters) - len(seen)
    if unjudged:
        log.warning("adjudicator: %s left %d/%d cluster(s) unjudged after retry",
                    claim.id, unjudged, len(clusters))

    # Anything still unjudged stays neutral, and says so. Silence must not read as
    # agreement — but it must not read as a considered "neutral" either, which is why
    # the reason is explicit and the count is surfaced on the assessment.
    for c in clusters:
        seen.setdefault(c.key, ClusterJudgement(
            cluster_key=c.key, polarity="neutral", reason="未获判读",
            speaker=c.speaker, concept=c.concept, stance=c.stance,
            primary=c.primary, observation_ids=c.observation_ids))
    return [seen[c.key] for c in clusters]


# --------------------------------------------------------------------------- #
# Cross-section: how does each company in the cohort read?
# --------------------------------------------------------------------------- #
def build_cross_section_context(claim: ClaimDef, by_entity: dict) -> str:
    """One prompt covering the whole cohort, because the question IS comparative.

    Judging each company in isolation and collating afterwards would lose the only
    thing this claim asks: how they stand relative to one another. Seen together, three
    companies each describing their own position can be compared even though each is
    self-reporting — the bias is common-mode.
    """
    blocks = []
    for entity in claim.entities:
        groups = by_entity.get(entity) or []
        if not groups:
            blocks.append(f"### {entity}\n  （本期无可用读数）")
            continue
        lines = [f"### {entity}"]
        for c in groups:
            concept = claim.concept(c.concept)
            src = "自述" if c.speaker == entity else f"由 {c.speaker} 披露"
            lines.append(f"  · id={c.key}\n"
                         f"    [{c.concept}（{concept.desc if concept else ''}）· {src}"
                         + (f" · 期间 {', '.join(c.periods)}" if c.periods else "") + "]")
            for r in c.rows[:MAX_SPANS]:
                lines.append(f"      {(r.get('evidence_span') or '')[:SPAN_CHARS]}")
        blocks.append("\n".join(lines))

    return (
        f"比较的问题：{claim.statement or claim.id}\n"
        f"参与比较的公司：{', '.join(claim.entities)}\n\n"
        "下面按公司分组给出各自的读数（已去重）。请**逐家**判断它在这个维度上处于什么位置。\n\n"
        + "\n\n".join(blocks)
        + "\n\n逐家给出 standing：\n"
        "  strong  —— 在该维度上处于领先/增强的位置\n"
        "  neutral —— 持平，或证据不指向任一方向\n"
        "  weak    —— 处于落后/削弱的位置\n"
        "  unknown —— 证据不足以判断（**没有读数时必须用这个，不要猜**）\n\n"
        "要点：\n"
        "- 这是**横向比较**，不是逐家独立打分。判断某家是 strong 还是 neutral，要看它相对\n"
        "  其他几家的表述强弱，而不是它自己听起来乐观不乐观。\n"
        "- **每家都会把自己说得不错**，这是共同偏差。要找的是**差异**：谁给了具体的量产/\n"
        "  认证/价格证据，谁只给了愿景；谁在追赶，谁被追赶。\n"
        "- 没有读数的公司一律 unknown。缺席不是中性，更不是弱——它只是没说。\n"
        "- reason 一句话讲清依据，要引到具体内容。这句会入库并展示给人看。\n"
        "- **key_clusters 必填**：把你这个判断实际依据的那几组 id 回填进去。下游只展示\n"
        "  这几组的原文作为佐证，所以漏填 = 结论没有出处，填错 = 结论配上不相干的证据。\n"
        "- 你只判读证据；**不要**给买卖建议、目标价或仓位。"
    )


def judge_cross_section(claim: ClaimDef, by_entity: dict) -> list:
    """One reading per declared entity. Never raises — degrades to all-unknown.

    Degrading to unknown is the safe direction: it leaves moat_pricing null
    (cohort-neutral) rather than manufacturing a ranking out of an outage.
    """
    from ...schemas.chain import EntityReading
    from .outputs import CrossSectionView

    if not claim.entities:
        return []
    try:
        view = run_structured("evidence_adjudicator", CrossSectionView,
                              build_cross_section_context(claim, by_entity),
                              skill_slug="evidence-adjudicator")
    except Exception as exc:  # noqa: BLE001
        log.warning("cross-section adjudication failed for %s: %s", claim.id, exc)
        view = None

    valid = {"strong", "neutral", "weak", "unknown"}
    valid_keys = {c.key for g in by_entity.values() for c in g}
    seen: dict[str, EntityReading] = {}
    for item in (getattr(view, "readings", None) or []):
        entity = (item.entity or "").upper()
        if entity not in set(claim.entities):
            log.info("cross-section: unknown entity %r on %s — dropped", entity, claim.id)
            continue                      # it may not add companies to the cohort
        standing = item.standing if item.standing in valid else "unknown"
        # Only ids that exist. A cited cluster we cannot resolve would render as
        # missing evidence under a stated conclusion, which is the failure this field
        # was added to prevent.
        seen[entity] = EntityReading(
            entity=entity, standing=standing, reason=(item.reason or "").strip(),
            key_clusters=[k for k in (item.key_clusters or []) if k in valid_keys])
    for entity in claim.entities:
        seen.setdefault(entity, EntityReading(entity=entity, standing="unknown",
                                              reason="未获判读"))
    return [seen[e] for e in claim.entities]
