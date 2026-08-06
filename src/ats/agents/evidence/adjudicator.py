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
    subject = f"\n主体：{claim.subject}（这是一条**相对**命题，问的是该主体相对同业的位置）" \
        if claim.kind == "relative" else ""
    return (
        f"命题：{claim.statement or claim.id}{subject}\n"
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


def judge(claim: ClaimDef, clusters: list[EvidenceCluster]) -> list[ClusterJudgement]:
    """Adjudicate every cluster. Never raises — degrades to all-neutral.

    Degrading to neutral rather than to a guess is the safe direction: a failed
    adjudication leaves the claim `unknown` for want of evidence, which is honest,
    whereas defaulting to a polarity would manufacture a verdict out of an outage.
    """
    if not clusters:
        return []
    try:
        view = run_structured("evidence_adjudicator", AdjudicationView,
                              build_context(claim, clusters),
                              skill_slug="evidence-adjudicator")
    except Exception as exc:  # noqa: BLE001 - one claim must not break the run
        log.warning("adjudicator failed for claim %s: %s", claim.id, exc)
        view = None

    by_key = {c.key: c for c in clusters}
    seen: dict[str, ClusterJudgement] = {}
    for item in (getattr(view, "judgements", None) or []):
        cluster = by_key.get(item.cluster_key)
        if cluster is None:
            log.info("adjudicator: unknown cluster id %r on claim %s — dropped",
                     item.cluster_key, claim.id)
            continue                      # it may not invent clusters
        seen[cluster.key] = ClusterJudgement(
            cluster_key=cluster.key, polarity=item.polarity, reason=(item.reason or "").strip(),
            speaker=cluster.speaker, concept=cluster.concept, stance=cluster.stance,
            primary=cluster.primary, observation_ids=cluster.observation_ids)

    # Anything unjudged stays neutral, and says so. Silence must not read as agreement.
    for c in clusters:
        seen.setdefault(c.key, ClusterJudgement(
            cluster_key=c.key, polarity="neutral", reason="未获判读",
            speaker=c.speaker, concept=c.concept, stance=c.stance,
            primary=c.primary, observation_ids=c.observation_ids))
    return [seen[c.key] for c in clusters]
