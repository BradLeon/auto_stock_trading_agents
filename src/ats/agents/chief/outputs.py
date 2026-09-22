"""LLM-facing view for the Chief's decision output.

Captures ONLY the analytical fields the model should produce. System
bookkeeping (cycle_id, as_of) is attached in code afterwards. No numeric
(min/max) constraints: some providers reject them in the structured-output
JSON schema; conviction is clamped to [0,1] in code.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ...schemas.decision import Action, normalize_action


class DecisionView(BaseModel):
    """One actionable trade the Chief proposes."""

    symbol: str
    # Same declaration as the decision layer — never a second copy of the value list.
    action: Action

    @field_validator("action", mode="before")
    @classmethod
    def _canonical_action(cls, v: object) -> object:
        # The LLM-facing view is the entry point: fold the case here, once, so the
        # domain object downstream only ever sees a canonical value.
        return normalize_action(v, where="DecisionView.action") if isinstance(v, str) else v
    target_weight: float | None = Field(None, description="desired portfolio weight 0..1; optional")
    notional_usd: float | None = Field(None, description="order size in USD; optional")
    order_type: Literal["market", "limit"] = "limit"
    limit_price: float | None = None
    conviction: float = Field(description="0..1")
    rationale: str = Field(description="why this trade, citing the analyst signals")
    # Pre-registered plan. Recorded before the outcome is known, which is what lets the
    # review later ask whether the reasoning held — not just whether it made money.
    setup: Literal["pead_event", "risk_repair", "stop_loss", "sector_rotation",
                   "macro_tilt", "manual", "boss_override", "unknown"] = Field(
        "unknown", description="what kind of trade this is")
    stop_price: float | None = Field(
        None, description="price that would invalidate the thesis; not sent as an order")
    target_price: float | None = None
    planned_horizon_days: int | None = Field(
        None, description="intended holding window in trading days")
    invalidation: str = Field(
        "", description="what observation would falsify this thesis (not a price)")


class ChiefOutput(BaseModel):
    summary: str = Field(description="overall stance + how the artifacts were weighed")
    decisions: list[DecisionView] = Field(default_factory=list)
