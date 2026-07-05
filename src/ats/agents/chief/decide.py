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


def run(*, use_llm: bool = True, live_broker: bool = True) -> ChiefResult:
    now = datetime.now(timezone.utc)
    cycle_id = f"chief-{now:%Y%m%d-%H%M%S}"
    ctx = assemble.build(live_broker=live_broker)
    log.info("chief context: %s", ctx.stats())

    if not use_llm:
        return ChiefResult(cycle_id=cycle_id, as_of=now, summary="[stub] chief (no-llm)")

    try:
        out: ChiefOutput = run_structured("chief", ChiefOutput, ctx.as_context(),
                                          skill_slug="chief")
    except Exception as exc:  # noqa: BLE001
        log.warning("chief LLM failed: %s", exc)
        return ChiefResult(cycle_id=cycle_id, as_of=now,
                           summary=f"[fallback] chief LLM unavailable: {exc}")

    decisions = [_to_decision(d) for d in out.decisions if d.action != "hold"]
    return ChiefResult(cycle_id=cycle_id, as_of=now, summary=out.summary, decisions=decisions)


def _to_decision(v) -> TradeDecision:
    return TradeDecision(
        symbol=v.symbol.upper(), action=v.action,
        target_weight=v.target_weight, notional_usd=v.notional_usd,
        order_type=v.order_type, limit_price=v.limit_price,
        conviction=max(0.0, min(1.0, float(v.conviction))), rationale=v.rationale)
