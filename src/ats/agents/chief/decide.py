"""Chief decision run: assemble artifacts -> one Opus synthesis -> TradeDecisions.

LLM failure degrades to zero decisions (never blocks the daily job).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from ...schemas.decision import TradeDecision
from ..base import run_structured
from . import assemble
from .outputs import ChiefOutput

log = logging.getLogger("ats.agents.chief")


class ChiefResult(BaseModel):
    cycle_id: str
    as_of: datetime
    summary: str = ""
    decisions: list[TradeDecision] = Field(default_factory=list)
    context_text: str = ""            # the exact context the Chief saw (for the audit report)


def run(*, use_llm: bool = True) -> ChiefResult:
    now = datetime.now(timezone.utc)
    cycle_id = f"chief-{now:%Y%m%d-%H%M%S}"
    ctx = assemble.build()
    log.info("chief context: %s", ctx.stats())
    return from_context(ctx.as_context(), cycle_id=cycle_id, as_of=now, use_llm=use_llm)


def from_context(context_text: str, *, cycle_id: str, as_of: datetime,
                 use_llm: bool = True) -> ChiefResult:
    """The pure LLM segment: pre-assembled context -> one synthesis -> decisions."""
    if not use_llm:
        return ChiefResult(cycle_id=cycle_id, as_of=as_of, summary="[stub] chief (no-llm)",
                           context_text=context_text)

    try:
        out: ChiefOutput = run_structured("chief", ChiefOutput, context_text, skill_slug="chief")
    except Exception as exc:  # noqa: BLE001
        log.warning("chief LLM failed: %s", exc)
        return ChiefResult(cycle_id=cycle_id, as_of=as_of,
                           summary=f"[fallback] chief LLM unavailable: {exc}",
                           context_text=context_text)

    decisions = [_to_decision(d) for d in out.decisions if d.action != "hold"]
    return ChiefResult(cycle_id=cycle_id, as_of=as_of, summary=out.summary,
                       decisions=decisions, context_text=context_text)


def revise_with_note(context_text: str, *, prior_summary: str, prior_decisions: list[TradeDecision],
                     note: str, cycle_id: str, as_of: datetime, use_llm: bool = True) -> ChiefResult:
    """The Boss sent the revision back with a change request: ONE more synthesis with the request added.

    Same context, same output contract as the first run; the request is the Boss's own words and the
    prior proposal is shown so the Chief changes what was asked rather than starting over. The revised
    orders go through risk again like any other revision.
    """
    prior = "\n".join(
        f"- {d.action} {d.symbol} "
        + (f"${d.notional_usd:,.0f}" if d.notional_usd else f"qty={d.qty}" if d.qty else "")
        + (f" — {d.rationale[:120]}" if d.rationale else "") for d in prior_decisions) or "（无订单）"
    addendum = ("\n\n## Boss 驳回上一版并提出修改意见\n"
                f"上一版摘要：{prior_summary}\n上一版订单：\n{prior}\n\n"
                f"修改意见（Boss 原话）：{note}\n\n请据此重新给出完整的本轮决策；意见与风控硬约束冲突时，以风控为准并说明。")
    return from_context(context_text + addendum, cycle_id=cycle_id, as_of=as_of, use_llm=use_llm)


def _to_decision(v) -> TradeDecision:
    return TradeDecision(
        symbol=v.symbol.upper(), action=v.action,
        target_weight=v.target_weight, notional_usd=v.notional_usd,
        order_type=v.order_type, limit_price=v.limit_price,
        conviction=max(0.0, min(1.0, float(v.conviction))), rationale=v.rationale,
        setup=getattr(v, "setup", "unknown") or "unknown",
        stop_price=getattr(v, "stop_price", None),
        target_price=getattr(v, "target_price", None),
        planned_horizon_days=getattr(v, "planned_horizon_days", None),
        invalidation=getattr(v, "invalidation", "") or "")
