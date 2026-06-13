"""Terminal BossChannel — Phase 1 HITL adapter.

Supports an `auto` mode (approve everything without prompting) so the dry-run
smoke test can execute end-to-end unattended.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..schemas.channel import ApprovalRequest, Notification, ReportBundle
from ..schemas.decision import BossApproval


class CLIChannel:
    def __init__(self, auto: bool = False, reviewer: str = "cli-boss") -> None:
        self.auto = auto
        self.reviewer = reviewer

    # --- outbound -------------------------------------------------------- #
    def push(self, msg: Notification) -> None:
        prefix = {"error": "❌", "fill_report": "✅", "approval_request": "📝"}.get(msg.kind, "ℹ️")
        print(f"\n{prefix}  {msg.title}\n{msg.body}")

    # --- approval -------------------------------------------------------- #
    def request_approval(self, req: ApprovalRequest) -> BossApproval:
        self._render_request(req)
        if self.auto:
            print(">> auto-approve (non-interactive)")
            return BossApproval(
                status="approved",
                reviewer=self.reviewer,
                reviewed_at=datetime.now(timezone.utc),
                comment="auto-approved",
            )
        return self._prompt(req)

    def _render_request(self, req: ApprovalRequest) -> None:
        print("\n" + "=" * 70)
        print(f"BOSS APPROVAL — cycle {req.cycle_id} @ {req.as_of:%Y-%m-%d %H:%M}")
        print("=" * 70)
        if req.context_summary:
            print(req.context_summary + "\n")
        if not req.decisions:
            print("(Manager proposed no trades.)")
        for i, d in enumerate(req.decisions, 1):
            size = (
                f"{d.notional_usd:,.0f} USD" if d.notional_usd
                else f"{d.qty} sh" if d.qty
                else f"target {d.target_weight:.0%}" if d.target_weight is not None
                else "?"
            )
            px = f"@ {d.limit_price}" if d.limit_price else "(mkt)"
            print(f"  [{i}] {d.action.upper():4} {d.symbol:6} {size:>14} {px}  conv={d.conviction:.2f}")
            if d.rationale:
                print(f"        ↳ {d.rationale}")
        print("-" * 70)

    def _prompt(self, req: ApprovalRequest) -> BossApproval:
        print("Commands: [a]pprove all · [r]eject all · s <SYMs> approve-subset · "
              "x <SYMs> reject · report <SYM> · q quit")
        while True:
            raw = input("boss> ").strip()
            if not raw:
                continue
            cmd, *rest = raw.split()
            arg = " ".join(rest)
            stamp = datetime.now(timezone.utc)
            if cmd in {"a", "approve"}:
                return BossApproval(status="approved", reviewer=self.reviewer, reviewed_at=stamp)
            if cmd in {"r", "reject"}:
                return BossApproval(status="rejected", reviewer=self.reviewer, reviewed_at=stamp,
                                    comment=arg)
            if cmd == "s":  # approve only these symbols
                return BossApproval(status="approved", reviewer=self.reviewer, reviewed_at=stamp,
                                    approved_symbols=rest)
            if cmd == "x":  # reject these symbols, approve the rest
                return BossApproval(status="approved", reviewer=self.reviewer, reviewed_at=stamp,
                                    rejected_symbols=rest)
            if cmd == "report":
                bundle = self.fetch_report_context(arg)
                print(bundle.summary or "(no context available yet)")
                continue
            if cmd in {"q", "quit"}:
                return BossApproval(status="rejected", reviewer=self.reviewer, reviewed_at=stamp,
                                    comment="quit without approving")
            print(f"unknown command: {cmd!r}")

    # --- context pull ---------------------------------------------------- #
    def fetch_report_context(self, query: str) -> ReportBundle:
        from .context import build_report_bundle

        return build_report_bundle(query)
