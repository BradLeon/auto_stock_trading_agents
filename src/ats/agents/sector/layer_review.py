"""Layer analyst — one chain layer at a time: HOW MUCH, and WHO within it.

The weekly review answered "is this layer hot" (`boom_score` + bullish/neutral/bearish),
which carries no position meaning. The budget path (`weight_cap` × cross-section rank)
ran regardless of where in the cycle a layer sat, so the only place that could express
"this layer should not be heavy right now" was a person editing risk.yaml.

This module fills that gap. Per layer, in its own small context:

    common claims   -> 这一层该给多少钱   (the allocation verdict)
    relative claims -> 层内选谁            (per-name rationale; the factor path is
                                            the structure analyst's, not ours)

Deliberately NOT in the context (see design D16): macro. The Chief already consumes the
macro review's sector tilts, so weighing rates here would count one judgement twice —
and when a layer verdict worsens, nobody could tell whether the industry or the macro
moved. Those two call for opposite actions (trim this layer vs trim total exposure).

A failed layer degrades to its previous verdict and is never persisted, so one bad call
cannot overwrite a good one or stop the other layers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.sector import ALLOCATIONS, LayerNameCall, LayerVerdict, SectorConfig
from ..base import run_structured
from .outputs import LayerVerdictView

log = logging.getLogger("ats.agents.sector.layer_review")

# Verdict when we have nothing to go on. Not a neutral read of the industry — a refusal
# to guess; `confidence` carries that, and the phrasing in the report must say which of
# the two "nothing to go on" cases it was (no claims vs. claims that said nothing).
DEFAULT_ALLOCATION = "标配"
BLIND_CONFIDENCE_CAP = 0.3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_context(cfg: SectorConfig, layer, *, basket=None, prior: LayerVerdict | None = None,
                  snapshot_block: str = "") -> str:
    """Assemble ONE layer's context. Never includes another layer's material, and never
    includes macro."""
    from ...data import industry

    from . import assemble

    parts = [
        f"# {layer.label}  [layer key = {layer.key}]",
        f"本层要回答的问题：{layer.question}" if layer.question else "",
    ]

    common_block, relative_block = assemble.layer_evidence_blocks(cfg, layer)
    if not layer.claims:
        # A config gap, not an evidence gap. Saying "证据不足" here would file the
        # missing claims under "the industry was quiet this quarter" — and then nobody
        # ever goes looking for them.
        parts.append(
            "## ⚠️ 本层无命题\n"
            "本层的 claims 列表是空的——这是**配置缺口**，不是本季没人发声。\n"
            "结论只能来自快照与判据笔记，且必须在 rationale 里显式写明「本层无命题」，"
            "**不要写成「证据缺失」**。配置结论取标配、confidence ≤0.3。")
    else:
        parts.append(common_block or (
            "## 共同需求议题（common）\n"
            "本层有命题，但本期**没有一条产出结论**——这是**证据缺口**（本季没人发声），"
            "与「本层无命题」不同。配置结论取标配、confidence ≤0.3，并说明是证据缺失。"))
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
        parts.append(
            f"## 上一轮本层结论（{prior.as_of:%Y-%m-%d}）\n"
            f"配置：{prior.allocation}（confidence {prior.confidence:.2f}）· "
            f"周期：{prior.cycle_position or '—'}\n"
            f"当时写下的反转触发条件——**本次必须逐条说明是否已被触发**：\n{trig}")

    return "\n\n".join(p for p in parts if p)


def _basket_block(layer, basket) -> str:
    if not basket.rows or not basket.cross_section_applicable:
        names = "、".join(r.symbol for r in basket.rows)
        return ("## 截面排名\n"
                "⚠️ **本层截面不适用**：可比样本少于两个，z 分全为 0，名次只是配置顺序的"
                "副产品，不是发现。**只出配置结论，不要据名次做层内取舍。**"
                + (f"\n本层预算全额落在 {names} 上（仍受单票限额约束）。" if names else ""))
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
            "| 代码 | 子层 | 排名 | 复合分 | 建议权重 | 技术久期 | 护城河/定价权 | 结构分析师理由 |",
            "|---|---|---|---|---|---|---|---|"]
    for r in sorted(basket.rows, key=lambda x: x.rank):
        def n(v):
            return f"{v:+.1f}" if v is not None else "—"
        flag = "" if r.data_ok else " ⚠️数据缺失"
        head.append(f"| {r.symbol}{flag} | {r.subgroup or '—'} | {r.rank} | "
                    f"{r.composite:+.2f} | {r.weight:.1%} | {n(r.tech_tenor)} | "
                    f"{n(r.moat_pricing)} | {(r.rationale or '—')[:80]} |")
    return "\n".join(head)


def run(cfg: SectorConfig, layer, *, basket=None, prior: LayerVerdict | None = None,
        snapshot_block: str = "", use_llm: bool = True) -> tuple[LayerVerdict, bool]:
    """Assess ONE layer. Returns (verdict, ok). `ok=False` means the call failed and the
    verdict is a carried-forward or default stand-in that MUST NOT be persisted."""
    has_claims = bool(layer.claims)
    cross_ok = bool(basket is not None and basket.rows and basket.cross_section_applicable)

    if not use_llm:
        return _fallback(layer, prior, has_claims, cross_ok), False

    ctx = build_context(cfg, layer, basket=basket, prior=prior,
                        snapshot_block=snapshot_block)
    try:
        view: LayerVerdictView = run_structured("layer_analyst", LayerVerdictView, ctx,
                                                skill_slug="layer-analyst")
    except Exception as exc:  # noqa: BLE001 - one layer must not stop the others
        log.warning("layer review failed for %s: %s", layer.key, exc)
        return _fallback(layer, prior, has_claims, cross_ok), False

    return _to_verdict(layer, view, has_claims, cross_ok), True


def _fallback(layer, prior, has_claims: bool, cross_ok: bool) -> LayerVerdict:
    """Carry the previous verdict forward; if there is none, refuse to guess.

    Carrying forward is not "no change" — it is the only honest answer when this round
    produced nothing, and it keeps a failed call from silently reading as a fresh 标配.
    """
    if prior is not None:
        return prior.model_copy(update={
            "as_of": _now(),
            "rationale": f"[本轮评审失败，沿用 {prior.as_of:%Y-%m-%d} 的结论] {prior.rationale}",
        })
    return LayerVerdict(
        layer_key=layer.key, as_of=_now(), allocation=DEFAULT_ALLOCATION,
        confidence=0.0, has_claims=has_claims, cross_section_applicable=cross_ok,
        rationale="本轮评审失败且无上一轮结论可沿用——这是**取不到判断**，不是判断为中性。")


def _to_verdict(layer, view: LayerVerdictView, has_claims: bool,
                cross_ok: bool) -> LayerVerdict:
    allocation = view.allocation if view.allocation in ALLOCATIONS else DEFAULT_ALLOCATION
    if view.allocation not in ALLOCATIONS:
        log.warning("layer %s: unknown allocation %r -> %s",
                    layer.key, view.allocation, DEFAULT_ALLOCATION)
    conf = max(0.0, min(1.0, float(view.confidence or 0.0)))
    # Blind rounds are capped in CODE, not by asking nicely in the prompt: a confident
    # verdict with no claims behind it is exactly the shape that would move a budget.
    if not has_claims:
        conf = min(conf, BLIND_CONFIDENCE_CAP)
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
    return LayerVerdict(
        layer_key=layer.key, as_of=_now(), allocation=allocation, confidence=conf,
        cycle_position=view.cycle_position,
        claim_attributions=list(view.claim_attributions),
        reversal_triggers=list(view.reversal_triggers),
        name_calls=kept, cross_section_applicable=cross_ok, has_claims=has_claims,
        rationale=view.rationale)
