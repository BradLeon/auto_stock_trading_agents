"""LLM-facing view for the Chief's decision output.

Captures ONLY the analytical fields the model should produce. System
bookkeeping (cycle_id, as_of) is attached in code afterwards. No numeric
(min/max) constraints: some providers reject them in the structured-output
JSON schema; conviction is clamped to [0,1] in code.
"""

from __future__ import annotations

from typing import Literal

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
    references: list[str] = Field(
        default_factory=list,
        description="projection ids (32 hex, as shown in the context) this trade rests on")


class HoldingReview(BaseModel):
    """One current stock holding, reviewed whether or not it is traded."""

    symbol: str
    verdict: Literal["hold", "add", "trim", "sell", "watch"]
    basis: str = Field(description="which signals decide it, with their numbers")
    conflicts: str = Field("", description="signals pointing the other way, and why they lose")
    references: list[str] = Field(default_factory=list, description="projection ids relied on")


class EvidenceWeight(BaseModel):
    """How one analyst source entered this round."""

    source: str = Field(description="e.g. 风控姿态 / 宏观 / 行业配置 / 层级分析 / 基本面 SYMBOL / 技术面 SYMBOL")
    said: str = Field(description="what it concluded, briefly")
    use: Literal["adopted", "partial", "overridden", "background"]
    why: str
    references: list[str] = Field(default_factory=list)


class Alternative(BaseModel):
    idea: str = Field(description="a trade considered and not taken")
    why_not: str


class ChiefReasoning(BaseModel):
    """The full reasoning behind the round; the approval card shows only `summary`."""

    portfolio_stance: str = Field(description="overall stance and how risk posture, macro and sector set it")
    holdings_review: list[HoldingReview] = Field(
        default_factory=list, description="every current stock holding, traded or not")
    evidence_weighing: list[EvidenceWeight] = Field(default_factory=list)
    considered_not_taken: list[Alternative] = Field(default_factory=list)
    risks_and_invalidation: str = Field(description="what would show this round's judgment wrong")


class OptionAction(BaseModel):
    """Advice on an EXISTING option position. Executed by hand, never by the system."""

    symbol: str = Field(description="underlying symbol")
    expiry: str = Field(description="YYYYMMDD of the held contract")
    strike: float
    right: Literal["C", "P"]
    action: Literal["close", "reduce", "add", "roll", "hold"]
    contracts: float | None = Field(None, description="contracts to trade; None for hold")
    roll_to_expiry: str | None = Field(None, description="roll only: target YYYYMMDD")
    roll_to_strike: float | None = Field(None, description="roll only: target strike")
    rationale: str
    references: list[str] = Field(default_factory=list)


class ChiefOutput(BaseModel):
    summary: str = Field(description="2-4 sentences for the approval card: stance and the trades")
    reasoning: ChiefReasoning
    decisions: list[DecisionView] = Field(default_factory=list)
    option_actions: list[OptionAction] = Field(
        default_factory=list, description="advice on held option positions only; executed by hand")
