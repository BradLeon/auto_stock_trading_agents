"""LangGraph state for one chief decision run — the single trading funnel.

Every order path (chief daily收口, pead-chief, scheduled, manual/stored trader
commands) flows through this state so the 6-layer risk gate and the Boss-approval
interrupt are enforced uniformly. thread_id == cycle_id (`chief-*` / `trader-*`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..schemas.decision import BossApproval, TradeDecision
from ..schemas.memory import TradeLogEntry
from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import DecisionRiskReview


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
    actionable_scores: list = Field(default_factory=list)       # [[symbol, fiscal_label], …] fresh scores this cycle → marked consumed after run

    # chief_decide
    summary: str = ""
    decisions: list[TradeDecision] = Field(default_factory=list)

    # risk_gate
    portfolio: PortfolioSnapshot | None = None
    qty_by_symbol: dict[str, float] = Field(default_factory=dict)
    risk_notes: list[str] = Field(default_factory=list)
    approval_summary: str = ""          # banner + risk block + order lines (the card body)

    # decision-audit loop (Phase B, §5.4). `decisions` stays this round's FULL
    # proposal; `approved_decisions` is what the review let through and what the
    # approval card / trader see.
    research_snapshot: dict = Field(default_factory=dict)   # §10.1 frozen inputs
    gap_report: str = ""                                    # 7.8: incomplete-snapshot report (never a decision input)
    revision_no: int = 0                                    # current revision (per cycle)
    revision_hash: str = ""                                 # content hash of that revision
    parent_revision_no: int | None = None                   # revised from (chief_revise)
    risk_round: int = 0                                     # 1-based once risk_gate ran
    max_risk_rounds: int = 3                                # bounded auto-revision (§5.4)
    risk_review: DecisionRiskReview | None = None           # latest deterministic review
    approved_decisions: list[TradeDecision] = Field(default_factory=list)
    portfolio_snapshot_id: str = ""                         # review binding (§10.2)
    authorization: dict[str, Any] | None = None             # execution gate (Group 7)
    gate_outcome: str = ""                                  # placed | stale | refused
    gate_rejections: list[str] = Field(default_factory=list)
    cycle_status: str = ""                                  # mirrors decision_cycles.status

    # boss_review / trader / persist
    approval: BossApproval | None = None
    order_results: list[TradeLogEntry] = Field(default_factory=list)
    fills: list[dict] = Field(default_factory=list)
