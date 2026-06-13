"""Risk guardrail contract produced by the risk manager."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RiskGuardrails(BaseModel):
    """Hard constraints the Manager's decisions must satisfy.

    A deterministic validator (see agents/manager) enforces these after the
    Manager LLM produces decisions — the LLM is never trusted to self-comply.
    """

    as_of: datetime
    max_position_pct: float = Field(0.20, gt=0, le=1)
    max_sector_pct: float = Field(0.40, gt=0, le=1)
    max_gross_leverage: float = Field(1.0, gt=0)
    max_single_order_usd: float = Field(25000, gt=0)
    cash_floor_pct: float = Field(0.05, ge=0, le=1)
    no_add_list: list[str] = Field(default_factory=list, description="symbols not allowed to add")
    forced_trim: list[str] = Field(default_factory=list, description="symbols that must be reduced")
    notes: str = ""
