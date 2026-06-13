"""LangGraph state for one trading cycle.

Parallel analyst fan-out writes into the *_reports lists concurrently; the
`operator.add` reducer concatenates each Send's contribution.
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from ..schemas.decision import BossApproval, TradeDecision
from ..schemas.market import MarketSnapshot, Ticker
from ..schemas.memory import TradeLogEntry
from ..schemas.reports import (
    FundamentalReport,
    IndustryReport,
    MacroReport,
    TechnicalReport,
)
from ..schemas.risk import RiskGuardrails


class TradingState(BaseModel):
    cycle_id: str
    as_of: datetime
    dry_run: bool = True

    # Inputs for the cycle.
    watchlist: list[Ticker] = Field(default_factory=list)
    sectors: dict[str, str] = Field(default_factory=dict)  # sector -> supply-chain brief

    # Ingest output.
    market_data: dict[str, MarketSnapshot] = Field(default_factory=dict)

    # Analyst outputs (parallel fan-out; lists use a concat reducer).
    macro_report: MacroReport | None = None
    industry_reports: Annotated[list[IndustryReport], operator.add] = Field(default_factory=list)
    fundamental_reports: Annotated[list[FundamentalReport], operator.add] = Field(default_factory=list)
    technical_reports: Annotated[list[TechnicalReport], operator.add] = Field(default_factory=list)

    # Risk -> decision -> approval -> execution.
    risk_guardrails: RiskGuardrails | None = None
    decisions: list[TradeDecision] = Field(default_factory=list)
    approval: BossApproval | None = None
    order_results: list[TradeLogEntry] = Field(default_factory=list)
