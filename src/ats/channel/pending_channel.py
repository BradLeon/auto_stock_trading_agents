"""The channel of an unattended decision run: leave the cycle waiting and tell the Boss once.

The run process checkpoints at the approval interrupt and exits; the Boss answers later with
`ats approval approve|revise|reject`, which resumes the same thread in its own process.
The notification is one-way text — no links, no callbacks — sent through the configured channel,
so changing the instant-messaging tool only changes that configuration.
"""

from __future__ import annotations

import logging

from ..schemas.channel import ApprovalRequest, Notification, ReportBundle

log = logging.getLogger("ats.channel.pending")


def notify(title: str, body: str) -> bool:
    """Send a one-way text; an unavailable messenger never fails the run."""
    try:
        from . import get_channel

        channel = get_channel()
        if isinstance(channel, PendingApprovalChannel):      # never relay to ourselves
            print(f"\n📝  {title}\n{body}")
            return True
        channel.push(Notification(kind="approval_request", title=title, body=body))
        return True
    except Exception as exc:  # noqa: BLE001 - a reminder must not block or fail the decision cycle
        log.warning("approval reminder not delivered: %s", exc)
        return False


def pending_summary(req: ApprovalRequest) -> str:
    lines = []
    for d in req.decisions[:6]:
        size = (f"${d.notional_usd:,.0f}" if d.notional_usd else f"{d.qty:g} 股" if d.qty
                else f"目标 {d.target_weight:.0%}" if d.target_weight is not None else "")
        lines.append(f"{d.action} {d.symbol} {size}".strip())
    more = f" 等共 {len(req.decisions)} 笔" if len(req.decisions) > 6 else ""
    return "；".join(lines) + more


def reminder_text(cycle_id: str, revision_no: int, summary: str) -> tuple[str, str]:
    return (f"待审批：{cycle_id}",
            f"修订 {revision_no}：{summary or '（无订单摘要）'}\n"
            f"查看：ats approval show {cycle_id}")


def superseded_text(old_cycle: str, new_cycle: str) -> tuple[str, str]:
    return (f"已过期未审批：{old_cycle}",
            f"该周期未被审批，已被 {new_cycle} 取代，不会再执行。")


class PendingApprovalChannel:
    """Async channel for `chief-daily`: a reminder at the interrupt, the answer comes from the CLI."""

    is_async = True
    kind = "pending"

    def push(self, msg: Notification) -> None:
        notify(msg.title, msg.body)

    def send_approval_request(self, req: ApprovalRequest, thread_id: str) -> None:
        title, body = reminder_text(req.cycle_id, req.revision_no, pending_summary(req))
        notify(title, body)

    def request_approval(self, req: ApprovalRequest):
        raise RuntimeError("pending channel is answered through `ats approval`, not in-process")

    def fetch_report_context(self, query: str) -> ReportBundle:
        from .context import build_report_bundle

        return build_report_bundle(query)
