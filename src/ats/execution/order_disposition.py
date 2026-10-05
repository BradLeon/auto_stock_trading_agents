"""Per-order disposition for a route switch (Phase F task 2.7).

Task 11.7 asks which unsettled orders may be *confirmed complete* before a trade
route moves. The answer is not a single yes/no — it differs by status, and the
differences are the whole content of the requirement:

- **Fully filled / cancelled / rejected / error** — confirmed complete. The broker
  did the thing or refused it; nothing will change.
- **Expired (inferred)** — also terminal, but only after a read-only reconciliation
  pass has run. `reconcile` infers `expired` for a DAY order nobody ever resolved.
  It is an inference, so it carries `terminal_basis='inferred'` rather than
  broker evidence, and this module will not accept it on a status alone: the point
  of the pass is to also pick up late fills, which can flip the row back to
  `partial` or `filled`. Accepting `expired` without one would treat "nobody told
  us anything happened" as "nothing happened".
- **Partial** — blocks. Shares outstanding at the broker, and the remainder may
  still fill.
- **Submitted** — blocks. `place_orders` polls for three seconds and returns, so
  this is the normal state of an order that is about to fill.
- **Unknown / absent** — blocks. Local state that cannot say is not local state
  that says "finished".

Late fills belong to reconciliation, not to a route switch (see
`late_fill_disposition`). That split is the point: the switch refuses to *decide*
that an order is finished, and read-only reconciliation decides it later, on
broker evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .authorization_lifecycle import AuthorizationLifecycle, TERMINAL_ORDER_STATUSES

# Statuses that confirm completion on their own, without a reconciliation pass.
# `expired` is deliberately absent — see the module docstring.
SELF_CONFIRMING_STATUSES: frozenset[str] = frozenset(TERMINAL_ORDER_STATUSES)

# The status `reconcile` infers for a DAY order nobody resolved.
INFERRED_EXPIRED = "expired"

# Disposition outcomes.
CONFIRMED_COMPLETE = "confirmed_complete"
BLOCKS_SWITCH = "blocks_switch"
PENDING_RECONCILIATION = "pending_reconciliation"


@dataclass
class OrderDisposition:
    """What a route switch may conclude about one order, and on what basis."""

    order_id: str
    status: str
    disposition: str
    lifecycle: str
    basis: str = ""
    reason: str = ""
    reconciliation_required: bool = False

    @property
    def blocks_switch(self) -> bool:
        return self.disposition == BLOCKS_SWITCH

    def as_row(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id, "status": self.status,
            "disposition": self.disposition, "lifecycle": self.lifecycle,
            "basis": self.basis, "reason": self.reason,
            "reconciliation_required": self.reconciliation_required,
        }


def disposition_for_order(row: dict[str, Any]) -> OrderDisposition:
    """Classify one order row for a route switch.

    `row` may carry `terminal_basis` — `reconcile` writes `broker` or `inferred`
    there, and the two are not interchangeable. An `expired` with
    `terminal_basis='broker'` is broker evidence; without it, the row is waiting
    for a reconciliation pass.
    """
    order_id = str(row.get("order_id") or row.get("rid") or "")
    status = str(row.get("status") or "").strip().lower()
    basis = str(row.get("terminal_basis") or "").strip().lower()
    lifecycle = AuthorizationLifecycle.classify_status(status)

    if lifecycle == "terminal":
        return OrderDisposition(order_id, status, CONFIRMED_COMPLETE, lifecycle,
                                basis=basis or "recorded",
                                reason="the broker acted or refused; nothing will change")

    if status == INFERRED_EXPIRED:
        # Terminal by inference. Acceptable only with evidence behind it, because
        # the inference is precisely "we heard nothing", and a late fill is the
        # case where hearing nothing was wrong.
        if basis == "broker":
            return OrderDisposition(order_id, status, CONFIRMED_COMPLETE, lifecycle,
                                    basis=basis,
                                    reason="expired and confirmed by broker evidence")
        return OrderDisposition(
            order_id, status, PENDING_RECONCILIATION, lifecycle, basis=basis or "none",
            reason="expired is inferred from silence; a read-only reconciliation "
                   "pass must run before this order counts as complete",
            reconciliation_required=True)

    if lifecycle == "live":
        return OrderDisposition(
            order_id, status, BLOCKS_SWITCH, lifecycle,
            reason=f"{status} may still change at the broker")

    return OrderDisposition(
        order_id, status or "(none)", BLOCKS_SWITCH, "unknown",
        reason="local state cannot say whether the broker is still working on this")


def disposition_report(lifecycle: AuthorizationLifecycle) -> dict[str, Any]:
    """Dispositions for every order a drain would consider, plus the verdict."""
    rows = [disposition_for_order(row) for row in lifecycle.orders()]
    blocking = [d for d in rows if d.blocks_switch]
    pending = [d for d in rows if d.disposition == PENDING_RECONCILIATION]
    complete = [d for d in rows if d.disposition == CONFIRMED_COMPLETE]

    reasons: list[str] = []
    if lifecycle.cycle_is_live():
        reasons.append(
            f"decision cycle {lifecycle.cycle_status or '(unknown)'} is still live")
    for row in blocking:
        reasons.append(f"order {row.order_id or '(no id)'} {row.reason}")
    for row in pending:
        reasons.append(f"order {row.order_id or '(no id)'} {row.reason}")

    return {
        "orders": [d.as_row() for d in rows],
        "confirmed_complete": len(complete),
        "blocking": len(blocking),
        "pending_reconciliation": len(pending),
        "blocks_switch": bool(reasons),
        "reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# late fills (read-only reconciliation)
# --------------------------------------------------------------------------- #

@dataclass
class LateFillOutcome:
    """How a late fill was absorbed, and whether it must stop the switch."""

    order_id: str
    fill_time: str
    order_submit_day: str
    late: bool = False
    prior_status: str = ""
    resulting_status: str = ""
    stop_switch: bool = False
    reason: str = ""
    recorded_as: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id, "fill_time": self.fill_time,
            "order_submit_day": self.order_submit_day, "late": self.late,
            "prior_status": self.prior_status,
            "resulting_status": self.resulting_status,
            "stop_switch": self.stop_switch, "reason": self.reason,
            "recorded_as": self.recorded_as,
        }


def late_fill_disposition(order_row: dict[str, Any], fill: dict[str, Any], *,
                          reconcile: Callable[[dict, dict], dict[str, Any]] | None = None
                          ) -> LateFillOutcome:
    """Absorb one fill that arrived after its order's submit day.

    A late fill is not a route-switch decision. It is broker evidence that the
    order was still working, and the correct handling is to let the existing
    read-only reconciliation apply it (`_apply_cumulative_fills`, which is
    cumulative so a late partial cannot erase an earlier fill).

    What it may do is *stop* a switch: if reconciliation reports the order is
    still live — because the late fill only partially covered it — then the switch
    that was about to proceed must not. The asymmetry is deliberate. Absorbing the
    fill is always correct; letting a switch continue past one is not.
    """
    from ..trader.reconcile import _apply_cumulative_fills  # noqa: F401  (doc anchor)

    order_id = str(order_row.get("order_id") or order_row.get("rid") or "")
    prior = str(order_row.get("status") or "")
    fill_time = str(fill.get("time") or "")
    submit_day = str(order_row.get("first_submitted_at")
                     or order_row.get("submitted_at") or "")[:10]

    outcome = LateFillOutcome(order_id=order_id, fill_time=fill_time,
                              order_submit_day=submit_day, prior_status=prior)

    from ..trader.reconcile import _is_late

    outcome.late = _is_late(dict(fill), dict(order_row))
    if not outcome.late:
        outcome.resulting_status = prior
        outcome.reason = "fill is within the order's submit day; ordinary reconciliation"
        return outcome

    outcome.reason = ("fill arrived after the submit day, so the broker was still "
                      "working after the local row looked settled")
    if reconcile is None:
        # No read model was supplied, so the outcome is unknown — and unknown after
        # evidence of movement is exactly the case that must stop a switch.
        outcome.resulting_status = "unknown"
        outcome.stop_switch = True
        outcome.recorded_as = "late_fill_unabsorbed"
        return outcome

    applied = dict(reconcile(dict(order_row), dict(fill)) or {})
    outcome.resulting_status = str(applied.get("status") or "unknown")
    outcome.recorded_as = "reconciled_read_only"
    if outcome.resulting_status not in SELF_CONFIRMING_STATUSES:
        outcome.stop_switch = True
        outcome.reason += (f"; after reconciliation the order is "
                           f"{outcome.resulting_status}, so the switch must stop")
    return outcome


def register_late_fill(store: Any, outcome: LateFillOutcome, *, actor: str = "",
                       reason: str = "") -> str:
    """Record a late fill in the ledger so the switch's stop condition is auditable.

    Uses the existing ledger-exception channel rather than a new table: this is a
    discrepancy between what local state implied and what the broker did, which is
    precisely what that channel exists for.
    """
    from .ledger import register_reconciliation_gap

    detail = (f"order={outcome.order_id} fill_time={outcome.fill_time} "
              f"prior={outcome.prior_status} result={outcome.resulting_status} "
              f"stop_switch={outcome.stop_switch} actor={actor}")
    return register_reconciliation_gap(
        store,
        window_start=outcome.order_submit_day or outcome.fill_time[:10],
        window_end=outcome.fill_time[:10] or outcome.order_submit_day,
        reason=reason or f"late fill on {outcome.order_id} after route switch",
        detail=detail)
