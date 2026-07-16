"""LangGraph state for one chief decision run — the single trading funnel.

Every order path (chief daily收口, pead-chief, scheduled, manual/stored trader
commands) flows through this state so the 6-layer risk gate and the Boss-approval
interrupt are enforced uniformly. thread_id == cycle_id (`chief-*` / `trader-*`).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..schemas.decision import BossApproval, TradeDecision
from ..schemas.memory import TradeLogEntry
from ..schemas.portfolio import PortfolioSnapshot


class ChiefDecisionState(BaseModel):
    cycle_id: str                       # == thread_id for checkpoint/resume
    as_of: datetime
    source: str = "chief"               # chief | scheduled | pead-chief | stored-decisions | manual

    # run flags
    dry_run: bool = True
    use_llm: bool = True
    use_broker: bool = True             # False (--offline): no IBKR reads, risk gate degrades
    auto_approve: bool = False          # --yes; must NEVER become a default upstream
    decide: bool = True                 # False: skip the Chief, take seed_decisions as-is
    execute: bool = True                # False (--no-execute): stop after persist_decision
    seed_decisions: list[TradeDecision] = Field(default_factory=list)

    # assemble_context
    context_text: str = ""              # exact context the Chief saw (audit report)
    context_stats: dict = Field(default_factory=dict)
    net_liquidation: float = 0.0
    event_data: dict[str, dict] = Field(default_factory=dict)   # symbol -> {expected_move_pct}

    # chief_decide
    summary: str = ""
    decisions: list[TradeDecision] = Field(default_factory=list)

    # risk_gate
    portfolio: PortfolioSnapshot | None = None
    qty_by_symbol: dict[str, float] = Field(default_factory=dict)
    risk_notes: list[str] = Field(default_factory=list)
    approval_summary: str = ""          # banner + risk block + order lines (the card body)

    # boss_review / trader / persist
    approval: BossApproval | None = None
    order_results: list[TradeLogEntry] = Field(default_factory=list)
    fills: list[dict] = Field(default_factory=list)
