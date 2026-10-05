"""Atomic trade-route switch protocol (Phase F task 2.6, step ordering).

The four steps and why the order is not interchangeable:

1. **Freeze** new issuance and new submissions.
2. **Drain** — verify nothing valid is unfinished.
3. **Bump** the generation and write the new route.
4. **Open** submissions for the new route.

Two orderings that look equivalent are not, and both are silent failures:

- **Drain before freeze.** Between the drain reading "nothing outstanding" and
  the generation bump, a fresh authorization can be signed on the old generation
  and submitted after the switch. The drain result was true when taken and false
  when acted on. Freezing first closes that window: step 2 inspects a state that
  cannot change underneath it.
- **Open before bump.** Reopening before the generation moves restores the old
  route's ability to write for a moment, and any submission in that window is
  attributed to the new route while actually executing on the old one. Bumping
  first means the reopen is the only moment that changes write capability, and it
  changes it once.

Crash behaviour is designed rather than incidental. If the process dies between
steps 3 and 4, submissions stay frozen on restart: `recover_interrupted_switch`
reports "frozen at generation N" and no route can submit until an operator
explicitly opens or aborts. That is the conservative direction — a route that
cannot trade is recoverable, a route that trades on a half-applied switch is not.

The concurrency check in step 2 is a counter comparison, not a scan: the freeze
records the issuance counter at the moment it closed, and step 2 refuses if the
counter has moved. A scan cannot be made atomic with the freeze; a counter read in
the same transaction can.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import route_registry
from .authorization_lifecycle import AuthorizationLifecycle
from .route_registry import RouteRegistryError, RouteState


class RouteSwitchBlocked(RouteRegistryError):
    """The switch cannot proceed; `reasons` says why."""


@dataclass
class SwitchReport:
    """What a switch attempt did, in a form an operator can read back."""

    from_route: str = ""
    from_generation: int = 0
    to_route: str = ""
    to_generation: int = 0
    switch_token: str = ""
    steps_completed: list[str] = field(default_factory=list)
    aborted_at: str = ""
    reasons: list[str] = field(default_factory=list)
    drain: dict[str, Any] = field(default_factory=dict)
    state: RouteState | None = None

    @property
    def succeeded(self) -> bool:
        return not self.reasons and self.to_generation > self.from_generation

    def as_row(self) -> dict[str, Any]:
        return {
            "from_route": self.from_route, "from_generation": self.from_generation,
            "to_route": self.to_route, "to_generation": self.to_generation,
            "switch_token": self.switch_token,
            "steps_completed": list(self.steps_completed),
            "aborted_at": self.aborted_at, "reasons": list(self.reasons),
            "succeeded": self.succeeded,
        }


def perform_switch(new_route: str, *, actor: str = "", reason: str = "",
                   lifecycle: AuthorizationLifecycle | None = None,
                   environment: str = "", account: str = "",
                   path: str | Path | None = None) -> SwitchReport:
    """Run all four steps, or abort at the first one that cannot hold.

    `lifecycle` supplies the drain evidence. It is a parameter rather than an
    import because "is anything unfinished" is a question about a specific set of
    cycles, and a caller that has already enumerated them should not have them
    re-derived from a different source here — that divergence is exactly what
    makes a drain check untrustworthy.
    """
    target = path or route_registry.default_registry_path()
    report = SwitchReport()

    # --- step 1: freeze ----------------------------------------------------- #
    current = route_registry.read_state(target)
    report.from_route, report.from_generation = current.route_id, current.generation
    report.to_route = new_route

    freeze = route_registry.freeze_submissions(
        actor=actor, reason=reason or f"switch to {new_route}", path=target)
    report.switch_token = freeze.switch_token
    report.steps_completed.append("freeze")

    # Everything from here on either completes or aborts. The freeze is a
    # reservation on the registry; leaving it set on an exception would wedge the
    # installation with no way to trade, so the abort path always releases it.
    try:
        # --- step 2: drain --------------------------------------------------- #
        drain_reasons = _drain_reasons(freeze, lifecycle, path=target)
        report.drain = (lifecycle.blocking_summary() if lifecycle is not None
                        else {"supplied": False})
        if drain_reasons:
            raise RouteSwitchBlocked(
                "route switch blocked during drain: " + "; ".join(drain_reasons))
        report.steps_completed.append("drain")

        # --- step 3: bump + write the new route ------------------------------ #
        report.state = route_registry.switch_route(
            freeze.from_generation, new_route, environment=environment,
            account=account, actor=actor, reason=reason, path=target)
        report.to_generation = report.state.generation
        report.steps_completed.append("bump")

        # --- step 4: open ---------------------------------------------------- #
        route_registry.open_submissions(freeze.switch_token, path=target)
        report.steps_completed.append("open")
        return report

    except Exception as exc:  # noqa: BLE001 - every failure must release the freeze
        report.aborted_at = _current_step(report)
        report.reasons.append(str(exc))
        # Reopening under the SAME generation: nothing was applied, so nothing
        # needs re-approving. A rollback would be the wrong verb here.
        try:
            route_registry.abort_freeze(freeze.switch_token, actor=actor,
                                        reason=str(exc), path=target)
        except Exception:  # noqa: BLE001 - a wedged registry must still surface
            report.reasons.append(
                f"CRITICAL: the freeze could not be released; submissions stay "
                f"closed until it is")
        return report


def _drain_reasons(freeze: route_registry.FreezeState,
                   lifecycle: AuthorizationLifecycle | None,
                   *, path: str | Path) -> list[str]:
    """Why the switch may not proceed, given the freeze and the lifecycle.

    Both halves are checked, and they fail differently on purpose:

    - **Issuance moved.** Something was signed after the freeze, so the freeze is
      not holding and any lifecycle view is already incomplete. Reported as a
      *concurrency* failure, because no amount of waiting on the orders helps.
    - **Unfinished work.** Orders or a live cycle exist. Reported with the
      specific order ids so the operator can act, and retryable once settled.
    """
    reasons: list[str] = []
    freeze_state, counter = route_registry.read_freeze_with_counter(path)
    if freeze_state.switch_token != freeze.switch_token:
        reasons.append(
            f"the freeze was replaced by switch "
            f"{freeze_state.switch_token!r}; this attempt is {freeze.switch_token!r}")
    if counter > freeze.issuance_at_freeze:
        reasons.append(
            f"{counter - freeze.issuance_at_freeze} authorization(s) were signed "
            f"after the freeze (counter {freeze.issuance_at_freeze} -> {counter}); "
            f"the drain cannot be trusted for this window")
    if lifecycle is None:
        # Fail closed: "no lifecycle supplied" is not "nothing outstanding". A
        # caller that has not looked has not established that the drain holds.
        reasons.append(
            "no lifecycle evidence was supplied for the drain; the switch cannot "
            "assume the queue is empty")
    elif lifecycle.blocks_route_switch():
        summary = lifecycle.blocking_summary()
        if summary["cycle_is_live"]:
            reasons.append(
                f"decision cycle {summary['cycle_id_status'] or '(unknown)'} is "
                f"still live")
        for order in summary["unfinished_orders"]:
            reasons.append(
                f"order {order['order_id'] or '(no id)'} is "
                f"{order['status'] or '(no status)'} ({order['lifecycle']})")
    return reasons


def _current_step(report: SwitchReport) -> str:
    done = report.steps_completed
    if "bump" not in done:
        return "open" if "drain" in done else "drain"
    return "open"


def recover_interrupted_switch(path: str | Path | None = None) -> dict[str, Any]:
    """Report a switch that was interrupted, without deciding what to do about it.

    Recovery deliberately does not pick a side. A switch interrupted between bump
    and open has already changed the generation, so the old route's grants are
    dead either way; but whether to open on the new route or abort back depends on
    whether the operator wanted this switch at all, which only they know. Opening
    automatically would turn a crash into a completed cutover.
    """
    target = path or route_registry.default_registry_path()
    freeze, counter = route_registry.read_freeze_with_counter(target)
    if not freeze.frozen:
        return {"interrupted": False, "frozen": False}
    state = route_registry.try_read_state(target)
    generation_moved = bool(state and state.generation != freeze.from_generation)
    return {
        "interrupted": True,
        "frozen": True,
        "switch_token": freeze.switch_token,
        "from_route": freeze.from_route,
        "from_generation": freeze.from_generation,
        "to_generation": state.generation if state else None,
        "generation_moved": generation_moved,
        "issuance_at_freeze": freeze.issuance_at_freeze,
        "issuance_now": counter,
        "stage": ("bump_completed_open_pending" if generation_moved
                  else "freeze_completed_drain_pending"),
        "can_submit": False,
        "remedies": ["re-run the switch", "abort_freeze(...) to reopen on the "
                     "same generation"],
    }


def open_after_recovery(switch_token: str, *, path: str | Path | None = None) -> RouteState:
    """Finish an interrupted switch whose bump already landed.

    The generation is NOT re-bumped: it moved before the crash, and bumping again
    would retire authorizations that were signed against the route the operator is
    trying to keep.
    """
    target = path or route_registry.default_registry_path()
    route_registry.open_submissions(switch_token, path=target)
    return route_registry.read_state(target)


def abort_after_recovery(switch_token: str, *, actor: str = "", reason: str = "",
                         path: str | Path | None = None) -> RouteState:
    """Abort an interrupted switch, reopening on whatever generation is current."""
    route_registry.abort_freeze(switch_token, actor=actor, reason=reason, path=path)
    return route_registry.read_state(path)
