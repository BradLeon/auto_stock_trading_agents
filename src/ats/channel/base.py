"""BossChannel port — the abstraction every approval transport implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..schemas.channel import ApprovalRequest, Notification, ReportBundle
from ..schemas.decision import BossApproval


@runtime_checkable
class BossChannel(Protocol):
    """How the system reaches the human Boss.

    Adapters: CLIChannel (Phase 1), FeishuChannel / DiscordChannel (Phase 2).
    For async transports (Feishu/Discord), `request_approval` is expected to
    block the caller while the graph itself stays checkpointed — i.e. the graph
    is resumed from a callback using the same thread_id, not held in memory.
    """

    def push(self, msg: Notification) -> None:
        """Fire-and-forget notification to the Boss (pending review, fills, errors)."""
        ...

    def request_approval(self, req: ApprovalRequest) -> BossApproval:
        """Present decisions and block until the Boss returns a verdict."""
        ...

    def fetch_report_context(self, query: str) -> ReportBundle:
        """Pull supporting reports/trade history from Context Memory on demand."""
        ...
