"""Layer analyst — one chain layer at a time: WHAT STATE it is in, and WHO within it.

The weekly review answered "is this layer hot" (`boom_score` + bullish/neutral/bearish),
which carries no position meaning. Phase D re-scoped this role: the layer analyst judges
the layer's STATE (`layer_status`) from common-claim evidence and ranks names WITHIN the
layer — it no longer produces an allocation call. 「这一层该给多少钱」 belongs to the
sector analyst, who consumes the LayerAnalysis projection this module publishes.

    common claims   -> 这一层处于什么状态   (the status verdict)
    relative claims -> 层内谁在被验证      (per-name rationale; the factor path is
                                             the structure analyst's, not ours)

Deliberately NOT in the context (see design D16): macro. The Chief already consumes the
macro review's sector tilts, so weighing rates here would count one judgement twice —
and when a layer verdict worsens, nobody could tell whether the industry or the macro
moved. Those two call for opposite actions (trim this layer vs trim total exposure).

A failed layer publishes NOTHING — no projection, no carried-forward old verdict dressed
up as this round's judgement. The sector analyst treats the missing projection as a gap
and degrades conservatively (Phase D: `agent/layer-analyst` 失败不降级).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.sector import (CandidateClaim, LAYER_STATUSES, LayerNameCall,
                               LayerVerdict, SectorConfig)
from ..base import run_structured
from ..sector.outputs import LayerVerdictView

log = logging.getLogger("ats.agents.layer.layer_review")

# Status when we have nothing to go on. Not a neutral read of the industry — a refusal
# to guess; `confidence` carries that, and the phrasing in the report must say which of
# the two "nothing to go on" cases it was (no claims vs. claims that said nothing).
BLIND_STATUS = "steady"
BLIND_CONFIDENCE_CAP = 0.3

# 中文标签仅用于报告渲染；枚举本身保持英文，与 LayerAnalysisPayload 一致。
STATUS_CN = {"expanding": "扩张", "steady": "平稳", "contracting": "收缩",
             "unclear": "不明"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_context(cfg: SectorConfig, layer, *, basket=None, prior: LayerVerdict | None = None,
                  snapshot_block: str = "", assessments=None) -> str:
    """Assemble ONE layer's context. Never includes another layer's material, and never
    includes macro."""
    from ...data.products import sector_inputs as industry

    from ..sector import assemble

    parts = [
        f"# {layer.label}  [layer key = {layer.key}]",
        f"本层要回答的问题：{layer.question}" if layer.question else "",
    ]

    common_block, relative_block = assemble.layer_evidence_blocks(
        cfg, layer, assessments)
    if not layer.claims:
        # A config gap, not an evidence gap. Saying "证据不足" here would file the
        # missing claims under "the industry was quiet this quarter" — and then nobody
        # ever goes looking for them.
        parts.append(
            "## ⚠️ 本层无命题\n"
            "本层的 claims 列表是空的——这是**配置缺口**，不是本季没人发声。\n"
            "结论只能来自快照与判据笔记，且必须在 rationale 里显式写明「本层无命题」，"
            "**不要写成「证据缺失」**。状态判断取 steady、confidence ≤0.3。")
    else:
        parts.append(common_block or (
            "## 共同需求议题（common）\n"
            "本层有命题，但本期**没有一条产出结论**——这是**证据缺口**（本季没人发声），"
            "与「本层无命题」不同。状态判断取 steady、confidence ≤0.3，并说明是证据缺失。"))
        if relative_block:
            parts.append(relative_block)

    notes = list(dict.fromkeys(layer.structure_notes.values()))
    if notes:
        kb = industry.fetch_named(notes)
        if kb:
            # Criteria BEFORE the readings, mirroring assemble/structure: the notes say
            # HOW to weigh a reading, the ledger says what this quarter's reading WAS,
            # and the later block is the one still in view as the model writes.
            parts.append("## 判据知识库（年度级——说的是**怎么判断**，不是**谁排第几**）\n"
                         + industry.as_context(kb))

    if snapshot_block:
        parts.append(snapshot_block)
    if basket is not None:
        parts.append(_basket_block(layer, basket))

    if prior is not None:
        trig = "\n".join(f"  - {t}" for t in prior.reversal_triggers) or "  （上次没写触发条件）"
        prior_status = prior.layer_status or "steady"
        parts.append(
            f"## 上一轮本层结论（{prior.as_of:%Y-%m-%d}）\n"
            f"状态：{STATUS_CN.get(prior_status, prior_status)}"
            f"（confidence {prior.confidence:.2f}）· "
            f"周期：{prior.cycle_position or '—'}\n"
            f"当时写下的反转触发条件——**本次必须逐条说明是否已被触发**：\n{trig}")

    return "\n\n".join(p for p in parts if p)


def _basket_block(layer, basket) -> str:
    if not basket.rows or not basket.cross_section_applicable:
        names = "、".join(r.symbol for r in basket.rows)
        return ("## 截面排名\n"
                "⚠️ **本层截面不适用**：可比样本少于两个，z 分全为 0，名次只是配置顺序的"
                "副产品，不是发现。**只出层内相对排序，不要据名次做取舍。**"
                + (f"\n本层样本：{names}。" if names else ""))
    subgrouped = any(r.subgroup for r in basket.rows)
    head = ["## 截面排名（量化因子"
            + ("＋结构因子混合" if basket.structural else "，未跑结构层") + "）",
            "> z 分在**整层**计算。" + (
                "本层分了 subgroup —— 跨组的名次先后可能只是两组的因子分布不同"
                "（增速/毛利率量级本就不一样），不得仅凭名次断言跨组优劣。"
                if subgrouped else
                "本层未分 subgroup —— 若某票的 note 写明它定价机制不同，"
                "按那条 note 说明它的可比性限制。"),
            "",
            "| 代码 | 子层 | 排名 | 复合分 | 技术久期 | 护城河/定价权 | 结构分析师理由 |",
            "|---|---|---|---|---|---|---|"]
    for r in sorted(basket.rows, key=lambda x: x.rank):
        def n(v):
            return f"{v:+.1f}" if v is not None else "—"
        flag = "" if r.data_ok else " ⚠️数据缺失"
        head.append(f"| {r.symbol}{flag} | {r.subgroup or '—'} | {r.rank} | "
                    f"{r.composite:+.2f} | {n(r.tech_tenor)} | "
                    f"{n(r.moat_pricing)} | {(r.rationale or '—')[:80]} |")
    return "\n".join(head)


def run(cfg: SectorConfig, layer, *, basket=None, prior: LayerVerdict | None = None,
        snapshot_block: str = "", use_llm: bool = True,
        assessments=None, store=None, workflow_run_id: str = "",
        input_refs=None, data_vintage_refs=None) -> tuple[LayerVerdict | None, bool]:
    """Assess ONE layer. Returns (verdict, ok).

    `ok=False` means the call failed: the verdict is `None`, NOTHING is persisted and
    NO projection is published — a failed layer is a registered gap, never last round's
    conclusion wearing this round's timestamp, and one bad layer must not stop the
    others.

    `assessments` lets the caller pass the claim verdicts it already computed, so the
    report and the prompt render the SAME engine output — running it twice would re-read
    the ledger and could disagree with itself. When `store` is given, a successful
    verdict is published as a `layer_analysis` projection (idempotent by content).
    """
    has_claims = bool(layer.claims)

    if not use_llm:
        log.info("layer %s: no-llm run — no status judgement produced", layer.key)
        return None, False

    ctx = build_context(cfg, layer, basket=basket, prior=prior,
                        snapshot_block=snapshot_block, assessments=assessments)
    try:
        view: LayerVerdictView = run_structured("layer_analyst", LayerVerdictView, ctx,
                                                skill_slug="layer-analyst")
    except Exception as exc:  # noqa: BLE001 - one layer must not stop the others
        log.warning("layer review failed for %s: registered as missing (%s)", layer.key, exc)
        return None, False

    verdict = _to_verdict(layer, view, has_claims,
                          bool(basket is not None and basket.rows
                               and basket.cross_section_applicable),
                          assessments=assessments)
    if store is not None:
        _publish_projection(store, layer, verdict, workflow_run_id=workflow_run_id,
                            input_refs=input_refs, data_vintage_refs=data_vintage_refs)
    return verdict, True


def _publish_projection(store, layer, verdict: LayerVerdict, *, workflow_run_id: str = "",
                        input_refs=None, data_vintage_refs=None) -> str | None:
    """Publish the verdict as a `layer_analysis` projection (tasks 2.5/2.6).

    Idempotent by content: the envelope's projection id IS the content hash, and the
    store writes with INSERT OR REPLACE — republishing the same judgement twice lands
    on the same row instead of adding a twin. A publish failure must not un-produce
    the verdict the report path already holds; it leaves the layer without a fresh
    projection, which downstream reuse treats as a gap.
    """
    from ...agent.task_projection import ProjectionScope, build_envelope

    findings = list(verdict.claim_attributions) or [verdict.rationale or verdict.layer_status]
    summary = (verdict.rationale or verdict.layer_status).strip().splitlines()[0]
    try:
        envelope = build_envelope(
            role="layer_analysis",
            payload={"layer": layer.key, "status": verdict.layer_status,
                     "summary": summary, "findings": findings,
                     "confidence": verdict.confidence},
            scope=ProjectionScope(kind="layer", id=layer.key),
            as_of=verdict.as_of.isoformat(timespec="seconds"),
            input_refs=input_refs, data_vintage_refs=data_vintage_refs,
            workflow_run_id=workflow_run_id)
        return store.save_task_projection_envelope(envelope)
    except Exception as exc:  # noqa: BLE001 - a failed publish is a gap, not a crash
        log.warning("layer %s: projection publish failed (%s) — layer treated as "
                    "missing downstream", layer.key, exc)
        return None


def _to_verdict(layer, view: LayerVerdictView, has_claims: bool,
                cross_ok: bool, assessments=None) -> LayerVerdict:
    status = view.layer_status if view.layer_status in LAYER_STATUSES else BLIND_STATUS
    if view.layer_status not in LAYER_STATUSES:
        log.warning("layer %s: unknown layer_status %r -> %s",
                    layer.key, view.layer_status, BLIND_STATUS)
    conf = max(0.0, min(1.0, float(view.confidence or 0.0)))
    rationale = view.rationale or ""

    common = [a for a in (assessments or [])
              if getattr(a, "layer", layer.key) in ("", layer.key)]
    supportive = [a for a in common if a.verdict == "supportive"]
    contrary = [a for a in common if a.verdict in ("contradicted", "falsified")]
    any_conclusion = [a for a in common if a.verdict != "unknown"]

    # Blind rounds are capped in CODE, not by asking nicely in the prompt: a confident
    # status with no claims behind it is exactly the shape that would move a budget.
    if not has_claims:
        status = BLIND_STATUS
        conf = min(conf, BLIND_CONFIDENCE_CAP)
        marker = "本层无命题"
        if marker not in rationale:
            rationale = f"【{marker}——配置缺口，结论仅来自快照与判据笔记】{rationale}"
    elif not any_conclusion:
        # Claims exist but none produced a verdict this round: an EVIDENCE gap, distinct
        # from the config gap above — same blind status, different annotation.
        status = BLIND_STATUS
        conf = min(conf, BLIND_CONFIDENCE_CAP)
        marker = "证据缺失"
        if marker not in rationale:
            rationale = f"【{marker}——本季没有议题产出结论，非景气中性】{rationale}"
    else:
        # A directional status must be ANCHORED in at least one common-claim conclusion
        # pointing the same way; relative readings rank names, they never set the state.
        if status == "expanding" and not supportive:
            log.warning("layer %s: expanding without a supportive common claim -> unclear",
                        layer.key)
            status = "unclear"
        elif status == "contracting" and not contrary:
            log.warning("layer %s: contracting without a contrary common claim -> unclear",
                        layer.key)
            status = "unclear"
        if supportive and contrary:
            # Both sides of the ledger spoke; keep both (attributions already carry
            # them) and mark down the confidence instead of netting them out.
            conf = min(conf, 0.5)

    calls = [LayerNameCall(symbol=c.symbol.strip().upper(), subgroup=c.subgroup,
                           stance=c.stance if c.stance in ("增持", "持有", "减持") else "持有",
                           rationale=c.rationale,
                           self_reported_only=bool(c.self_reported_only))
             for c in view.name_calls]
    universe = {t.symbol.upper() for t in layer.tickers} | {s.upper() for s in layer.cohort_extra}
    kept = [c for c in calls if c.symbol in universe]
    for c in calls:
        if c.symbol not in universe:
            log.warning("layer %s: dropped non-universe name call %r", layer.key, c.symbol)
    # A candidate that cannot name a witness or a falsifier is not a proposition — it is
    # a mood, and letting it through would make this section exactly the kind of空话 the
    # claim discipline exists to prevent.
    candidates = []
    for c in getattr(view, "candidate_claims", []):
        cand = CandidateClaim(statement=c.statement.strip(),
                              witnesses=[w.strip().upper() for w in c.witnesses if w.strip()],
                              falsifier=c.falsifier.strip(), why_now=c.why_now)
        if cand.is_usable():
            candidates.append(cand)
        else:
            log.info("layer %s: dropped candidate claim without witness/falsifier: %r",
                     layer.key, cand.statement[:60])

    return LayerVerdict(
        layer_key=layer.key, as_of=_now(), layer_status=status, confidence=conf,
        candidate_claims=candidates,
        cycle_position=view.cycle_position,
        claim_attributions=list(view.claim_attributions),
        reversal_triggers=list(view.reversal_triggers),
        name_calls=kept, cross_section_applicable=cross_ok, has_claims=has_claims,
        rationale=rationale)
