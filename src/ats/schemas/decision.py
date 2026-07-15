"""Trade decision and Boss approval contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Action = Literal["buy", "add", "hold", "trim", "sell"]
OrderType = Literal["market", "limit"]
TimeInForce = Literal["DAY", "GTC"]

# Class shares: LLMs/yfinance write BRK.B / BRK-B; IBKR (our canonical form) uses "BRK B".
_CLASS_SHARE_RE = re.compile(r"^([A-Z]+)[.\-]([A-Z])$")


class TradeDecision(BaseModel):
    """A single proposed action on one symbol, produced by the Manager."""

    symbol: str

    @field_validator("symbol")
    @classmethod
    def _broker_native_symbol(cls, v: str) -> str:
        return _CLASS_SHARE_RE.sub(r"\1 \2", v.strip().upper())
    action: Action
    target_weight: float | None = Field(None, ge=0, le=1, description="desired portfolio weight")
    qty: float | None = Field(None, description="absolute share delta; sign implied by action")
    notional_usd: float | None = Field(None, ge=0)
    order_type: OrderType = "limit"
    limit_price: float | None = None
    time_in_force: TimeInForce = "DAY"
    conviction: float = Field(0.0, ge=0, le=1)
    rationale: str = ""
    references: list[str] = Field(default_factory=list, description="report ids / sources cited")


class BossApproval(BaseModel):
    """Human-in-the-loop verdict injected via interrupt resume."""

    status: Literal["approved", "rejected", "modified"]
    reviewer: str = ""
    reviewed_at: datetime | None = None
    comment: str = ""
    # Symbols the Boss approved / rejected; empty `approved` with status=approved means all.
    approved_symbols: list[str] = Field(default_factory=list)
    rejected_symbols: list[str] = Field(default_factory=list)
    # Optional decisions that fully replace the Manager's (status=modified).
    overrides: list[TradeDecision] = Field(default_factory=list)
    # Free-form instructions from the Boss straight to the Trader (bypass Manager).
    direct_instructions: list[TradeDecision] = Field(default_factory=list)

    def effective_decisions(self, proposed: list[TradeDecision]) -> list[TradeDecision]:
        """Resolve what the Trader should actually execute given this verdict."""
        if self.status == "rejected":
            return list(self.direct_instructions)
        if self.status == "modified" and self.overrides:
            return self.overrides + self.direct_instructions
        # approved: optionally filter to approved_symbols, drop rejected_symbols
        decisions = proposed
        if self.approved_symbols:
            decisions = [d for d in decisions if d.symbol in self.approved_symbols]
        if self.rejected_symbols:
            decisions = [d for d in decisions if d.symbol not in self.rejected_symbols]
        return decisions + self.direct_instructions
