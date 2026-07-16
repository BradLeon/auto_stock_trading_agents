"""LLM-facing view for the Chief's decision output.

Captures ONLY the analytical fields the model should produce. System
bookkeeping (cycle_id, as_of) is attached in code afterwards. No numeric
(min/max) constraints: some providers reject them in the structured-output
JSON schema; conviction is clamped to [0,1] in code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DecisionView(BaseModel):
    """One actionable trade the Chief proposes."""

    symbol: str
    action: Literal["buy", "add", "hold", "trim", "sell"]
    target_weight: float | None = Field(None, description="desired portfolio weight 0..1; optional")
    notional_usd: float | None = Field(None, description="order size in USD; optional")
    order_type: Literal["market", "limit"] = "limit"
    limit_price: float | None = None
    conviction: float = Field(description="0..1")
    rationale: str = Field(description="why this trade, citing the analyst signals")


class ChiefOutput(BaseModel):
    summary: str = Field(description="overall stance + how the artifacts were weighed")
    decisions: list[DecisionView] = Field(default_factory=list)
