"""Authorization lifecycle, derived rather than recorded (Phase F task 2.4).

Task 11.7 has to ask "is there any valid, unfinished authorization?" before it may
switch the trade route. That question was unanswerable when this was designed:
`ExecutionAuthorization` carries the ten §10.4 causal fields and nothing else — no
identifier, no expiry, no completion state — and it is *constructed on demand* from
the review and approval rows. So there was no state to list and nothing to expire.

Two ways to fix that, and one of them is wrong:

- **A parallel authorization ledger.** Rejected: it would be a second source of
  truth for whether an order may go out, and the two would drift. The gate's whole
  value is that authorization is *derived* from the audit trail, so callers cannot
  fill gaps in.
- **This module.** Lifecycle is *computed* from what already exists: the decision
  cycle's status, and the order-intent rows the Trader wrote. Those are the same
  rows reconciliation reads, so "is this order finished?" and "is this order
  finished?" cannot disagree.

The vocabulary is the existing one, not a new one. `OrderStatus` already
distinguishes filled / cancelled / rejected / error (finished) from submitted /
partial (unfinished); the only thing missing was `pending`, which means "written
down, not yet sent" and is unfinished by definition. A row with no terminal status
is treated as UNKNOWN rather than finished — the asymmetry matters, because an
order that was accepted by the broker while the local write failed must not
disappear from the drain check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

# Order statuses that will never change again: the broker either did the thing or
# refused it. Everything else is unfinished for the purpose of a route switch.
TERMINAL_ORDER_STATUSES: frozenset[str] = frozenset({
    "filled", "cancelled", "rejected", "error",
})

# Statuses where the broker may still act on the order. `submitted` is the case
# that motivates the whole module: `place_orders` polls for three seconds and
# returns, so a submitted order routinely settles after the call that created it.
LIVE_ORDER_STATUSES: frozenset[str] = frozenset({"pending", "submitted", "partial"})

# Cycle states that mean the cycle can no longer produce an order. A cycle that is
# still live may yet submit, which is what a route switch has to be sure about.
from ..decision.state import TERMINAL_STATUSES

TERMINAL_CYCLE_STATUSES: frozenset[str] = frozenset(s.value for s in TERMINAL_STATUSES) | frozenset({
    "rejected", "cancelled", "failed",  # recoverable historic vocabulary
})


class AuthorizationLifecycle:
    """Unfinished-order state for one cycle, read from the audit trail.

    Constructed with callables rather than a repository so the same logic serves
    the real store, a test double, and the reconciliation path — there is exactly
    one implementation of "is this finished".
    """

    def __init__(self, *, cycle_status: Any, order_rows: Iterable[dict[str, Any]],
                 now: datetime | None = None) -> None:
        self._cycle_status = str(cycle_status or "").strip()
        self._rows = [dict(row) for row in order_rows]
        self._now = now or datetime.now(timezone.utc)

    # --- orders ------------------------------------------------------------- #

    @staticmethod
    def classify_status(status: Any) -> str:
        """`terminal` | `live` | `unknown` for one order row.

        `unknown` is a first-class outcome, not an error: a row whose status is
        absent or unrecognised means local state cannot say whether the broker is
        still working on it, and that must block a switch rather than pass it.
        """
        text = str(status or "").strip().lower()
        if text in TERMINAL_ORDER_STATUSES:
            return "terminal"
        if text in LIVE_ORDER_STATUSES:
            return "live"
        return "unknown"

    def orders(self) -> list[dict[str, Any]]:
        return self._rows

    def unfinished(self) -> list[dict[str, Any]]:
        """Orders that may still change: live, or of unknown status.

        A terminal row is excluded even if the cycle is still open — the order
        itself is done, and reconciliation (not a route switch) owns what happens
        to a late fill on it.
        """
        out = []
        for row in self._rows:
            state = self.classify_status(row.get("status"))
            if state != "terminal":
                out.append({**row, "_lifecycle": state})
        return out

    def unknown_status_orders(self) -> list[dict[str, Any]]:
        return [row for row in self.unfinished() if row["_lifecycle"] == "unknown"]

    def has_unfinished(self) -> bool:
        return bool(self.unfinished())

    # --- cycle -------------------------------------------------------------- #

    @property
    def cycle_status(self) -> str:
        return self._cycle_status

    def cycle_is_live(self) -> bool:
        """True when the cycle is in a state that could still produce an order.

        A cycle whose status is not in the terminal list is treated as live. That
        is the fail-closed direction: an unrecognised status must not be read as
        "finished, carry on".
        """
        if not self._cycle_status:
            return True
        return self._cycle_status.lower() not in TERMINAL_CYCLE_STATUSES

    # --- the question task 11.7 asks ---------------------------------------- #

    def blocks_route_switch(self) -> bool:
        """True when a route switch must not proceed.

        Either a live cycle or an unfinished order is enough. The two are
        reported separately so an operator can see WHICH is holding the switch.
        """
        return self.cycle_is_live() or self.has_unfinished()

    def blocking_summary(self) -> dict[str, Any]:
        unfinished = self.unfinished()
        return {
            "cycle_id_status": self._cycle_status,
            "cycle_is_live": self.cycle_is_live(),
            "order_count": len(self._rows),
            "unfinished_count": len(unfinished),
            "unfinished_orders": [
                {"order_id": row.get("order_id", ""), "status": row.get("status", ""),
                 "lifecycle": row["_lifecycle"]}
                for row in unfinished
            ],
            "blocks_switch": self.blocks_route_switch(),
        }


def lifecycle_for_cycle(cycle_id: str, *, store: Any = None, now: datetime | None = None
                        ) -> AuthorizationLifecycle:
    """Derive the lifecycle for a cycle from the existing stores.

    Order rows come from the store's own trade reader (the same rows the Clerk
    reconciles), filtered to this cycle. A store without a usable reader yields no
    rows, and the cycle status then decides on its own — which fails closed,
    because a live cycle blocks a switch regardless of its orders.

    A cycle with no reachable audit repository is treated as live for the same
    reason: "cannot tell" must not read as "nothing outstanding".
    """
    resolved_store = store
    if resolved_store is None:
        from ..memory import get_store

        resolved_store = get_store()

    rows = _orders_for_cycle(resolved_store, cycle_id)

    cycle_status = ""
    try:
        from ..decision.repository import DecisionAuditRepository

        repo = DecisionAuditRepository(resolved_store)
        cycle = repo.get_cycle(cycle_id)
        if cycle is not None and "status" in cycle.keys():
            cycle_status = str(cycle["status"])
    except Exception:  # noqa: BLE001 - an absent audit trail means "unknown"
        cycle_status = ""

    return AuthorizationLifecycle(cycle_status=cycle_status, order_rows=rows, now=now)


def _orders_for_cycle(store: Any, cycle_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Order rows for one cycle, from whichever reader the store exposes.

    Prefers a cycle-scoped reader when the store has one; otherwise filters the
    general trade reader by cycle. Written as a capability probe rather than
    against one signature because the store has moved this table twice (Workflow
    memory → data layer) and the reader's name has changed with it.
    """
    for name in ("orders_for_cycle", "trades_for_cycle", "list_orders"):
        reader = getattr(store, name, None)
        if callable(reader):
            try:
                return [dict(row) for row in reader(cycle_id)]
            except TypeError:
                continue

    recent = getattr(store, "recent_trades", None)
    if callable(recent):
        rows = [dict(row) for row in recent(limit=limit)]
        matched = [row for row in rows if str(row.get("cycle_id", "")) == cycle_id]
        if matched or rows:
            return matched

    # Fall back to the row source itself. Read-only and cycle-scoped, so this
    # cannot be mistaken for a place that mutates trades.
    conn = getattr(store, "conn", None)
    if conn is not None:
        try:
            found = conn.execute(
                "SELECT * FROM trades WHERE cycle_id = ? ORDER BY rowid", (cycle_id,)
            ).fetchall()
            return [dict(row) for row in found]
        except Exception:  # noqa: BLE001 - no such table means nothing is outstanding
            return []
    return []


def unsettled_orders_for_cycle(cycle_id: str, *, store: Any = None) -> list[dict[str, Any]]:
    """Convenience for task 2.7's disposition step."""
    return lifecycle_for_cycle(cycle_id, store=store).unfinished()
