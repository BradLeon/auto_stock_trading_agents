"""Boss interaction contracts (used by the BossChannel port, see channel/)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .decision import TradeDecision
from .memory import TradeLogEntry

NotificationKind = Literal["approval_request", "fill_report", "info", "error"]


class Notification(BaseModel):
    """A message pushed to the Boss (decision pending, fills, errors)."""

    kind: NotificationKind = "info"
    title: str = ""
    body: str = ""
    data: dict = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    """Payload handed to a BossChannel when the graph interrupts for approval."""

    cycle_id: str
    as_of: datetime
    decisions: list[TradeDecision] = Field(default_factory=list)
    context_summary: str = ""   # condensed analyst/risk rationale for the Boss


class ReportBundle(BaseModel):
    """Context the Boss can pull on demand while reviewing (fetch_report_context)."""

    query: str = ""
    reports: list[dict] = Field(default_factory=list)   # serialized analyst reports
    trade_logs: list[TradeLogEntry] = Field(default_factory=list)
    summary: str = ""
