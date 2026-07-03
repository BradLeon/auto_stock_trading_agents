"""LangGraph state for one PEAD earnings-event run (one ticker, one phase)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..schemas.decision import BossApproval, TradeDecision
from ..schemas.memory import TradeLogEntry
from ..schemas.pead import (
    Actuals,
    ExpectationSet,
    MarketSetup,
    PeadConfig,
    Scorecard,
    SignalChainItem,
)
from ..schemas.portfolio import PortfolioSnapshot


class PeadState(BaseModel):
    symbol: str
    fiscal_label: str = ""
    phase: str = "prep"                 # "prep" | "score"
    as_of: datetime

    # run flags (mirror the daily cycle)
    dry_run: bool = True
    use_llm: bool = True
    use_broker: bool = True
    live_data: bool = True
    transcript_source: str | None = None

    config: PeadConfig | None = None

    # fetched inputs
    fundamentals_text: str = ""
    consensus: dict = Field(default_factory=dict)
    peer_rows: list[dict] = Field(default_factory=list)
    transcript_text: str = ""
    transcript_resolved_source: str = ""
    documents_text: str = ""        # official docs (SEC 8-K release + investor decks)

    # dossier sections
    prior_narrative: str = ""           # accumulated monitor thesis to continue in prep
    expectation_set: ExpectationSet | None = None
    market_setup: MarketSetup | None = None
    signal_chain: list[SignalChainItem] = Field(default_factory=list)
    actuals: Actuals | None = None
    scorecard: Scorecard | None = None
    portfolio: PortfolioSnapshot | None = None

    # decision / execution
    decisions: list[TradeDecision] = Field(default_factory=list)
    decision_band: str = ""
    risk_adjustments: list[str] = Field(default_factory=list)
    approval: BossApproval | None = None
    order_results: list[TradeLogEntry] = Field(default_factory=list)
