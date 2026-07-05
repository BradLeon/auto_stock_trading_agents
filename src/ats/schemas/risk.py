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


# --------------------------------------------------------------------------- #
# Risk officer — full 6-layer picture (deterministic)
# --------------------------------------------------------------------------- #
class Cluster(BaseModel):
    members: list[str] = Field(default_factory=list)
    weight: float = 0.0               # total portfolio weight of the cluster
    avg_corr: float = 0.0


class StressResult(BaseModel):
    scenario: str
    loss_pct: float = 0.0             # portfolio loss vs NAV (negative)


class EventRisk(BaseModel):
    symbol: str
    weight: float = 0.0
    expected_move_pct: float | None = None
    event_loss_pct: float = 0.0       # weight * expected_move (as % NAV)


class LayerExposure(BaseModel):
    key: str
    label: str = ""
    weight: float = 0.0
    cap: float | None = None
    breached: bool = False


class Breach(BaseModel):
    layer: str                        # e.g. "L1-chain-layer", "L3-beta", "L6-event"
    limit: str                        # human-readable limit
    actual: str
    action: str                       # what enforcement did / would do


class RiskReview(BaseModel):
    as_of: datetime
    net_liquidation: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    cash_pct: float = 0.0
    portfolio_beta: float | None = None
    chain_layers: list[LayerExposure] = Field(default_factory=list)
    clusters: list[Cluster] = Field(default_factory=list)
    drawdown_pct: float | None = None
    daily_pnl_pct: float | None = None
    stress: list[StressResult] = Field(default_factory=list)
    event_risks: list[EventRisk] = Field(default_factory=list)
    breaches: list[Breach] = Field(default_factory=list)
    risk_state: str = "normal"        # normal | caution | derisk
    notes: str = ""

    def regime_block(self, max_chars: int = 800) -> str:
        parts = [f"[风控 {self.as_of:%Y-%m-%d}] 状态={self.risk_state} beta={self.portfolio_beta} "
                 f"回撤={self.drawdown_pct}% 现金={self.cash_pct:.0%}"]
        for b in self.breaches:
            parts.append(f"  ⚠️ {b.layer}: {b.actual} vs {b.limit} → {b.action}")
        return "\n".join(parts)[:max_chars]
