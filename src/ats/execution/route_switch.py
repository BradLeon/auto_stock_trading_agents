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
from .order_disposition import PENDING_RECONCILIATION, disposition_report
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
                   expected_generation: int | None = None,
                   lifecycle: AuthorizationLifecycle | None = None,
                   environment: str = "", account: str = "",
                   path: str | Path | None = None) -> SwitchReport:
    """Run all four steps, or abort at the first one that cannot hold.

    `lifecycle` supplies the drain evidence. It is a parameter rather than an
    import because "is anything unfinished" is a question about a specific set of
    cycles, and a caller that has already enumerated them should not have them
    re-derived from a different source here — that divergence is exactly what
    makes a drain check untrustworthy.

    `expected_generation` is the caller's claim about which generation it believes
    is current, and it is required for a real cutover. Without it the freeze would
    supply the base value instead, which silently voids the compare-and-set: the
    second of two concurrent operators would freeze *after* the first committed,
    read the already-bumped generation, and succeed — so both would believe they
    performed the switch, and the generation would have advanced twice. Passing it
    explicitly makes the second operator fail on its stale claim instead.
    """
    target = path or route_registry.default_registry_path()
    report = SwitchReport()

    # --- step 1: freeze ----------------------------------------------------- #
    current = route_registry.read_state(target)
    report.from_route, report.from_generation = current.route_id, current.generation
    report.to_route = new_route

    if expected_generation is not None and int(expected_generation) != current.generation:
        # Refuse BEFORE freezing: a stale claim must not be able to close
        # submissions on a route it did not intend to switch.
        report.aborted_at = "freeze"
        report.reasons.append(
            f"caller believes the current generation is {expected_generation}, but "
            f"it is {current.generation} (route {current.route_id!r}); another "
            f"operator moved it, so this attempt is stale")
        return report

    try:
        freeze = route_registry.freeze_submissions(
            actor=actor, reason=reason or f"switch to {new_route}", path=target,
            expected_generation=current.generation)
    except RouteRegistryError as exc:
        report.aborted_at = "freeze"
        report.reasons.append(str(exc))
        return report  # never abort a competing operator's freeze
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

    except Exception as exc:  # noqa: BLE001 - post-bump failures must keep the freeze
        report.aborted_at = _current_step(report)
        report.reasons.append(str(exc))
        # Reopening under the SAME generation: nothing was applied, so nothing
        # needs re-approving. A rollback would be the wrong verb here.
        current = route_registry.try_read_state(target)
        if current is None or current.generation != freeze.from_generation:
            report.reasons.append("generation changed or authority unreadable; keep frozen for explicit recovery")
        else:
            try:
                route_registry.abort_freeze(freeze.switch_token, actor=actor,
                                            reason=str(exc), path=target)
            except Exception:  # noqa: BLE001 - failure must preserve the closed state
                report.reasons.append("CRITICAL: submissions stay closed until freeze recovery")
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
    for receipt in route_registry.submission_receipts(path):
        if receipt["status"] not in {"filled", "cancelled", "rejected"}:
            reasons.append(f"broker intent {receipt['intent_id']} is {receipt['status']} "
                           f"(pid={receipt['pid']}, generation={receipt['generation']}); "
                           "read-only reconciliation is required")
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

    # Task 2.7: an order the lifecycle calls unfinished may still be CONFIRMABLE
    # once a read-only reconciliation pass runs (notably `expired`, which is an
    # inference from silence). Those do not block with the orders above — they are
    # routed through `disposition_report`, which decides per status and names the
    # reconciliation each one needs.
    if lifecycle is not None:
        for row in disposition_report(lifecycle)["orders"]:
            if row["disposition"] == PENDING_RECONCILIATION:
                reasons.append(
                    f"order {row['order_id'] or '(no id)'} is {row['status']} by "
                    f"inference only; run read-only reconciliation before the switch "
                    f"({row['reason']})")
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
        "remedies": ["re-run the switch", ("abort_freeze(...) to reopen on the "
                     "same generation")],
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


def restore_stopped_simulation(switch_token: str, *, expected_generation: int,
                               lifecycle: AuthorizationLifecycle | None, target_check,
                               actor: str, reason: str) -> SwitchReport:
    """Recover a stopped, validated no-network transport; failures remain frozen.

    This is an isolated rehearsal entry, not deployment/live authorization. The
    target proof is re-read before bump and open and pinned to current business
    code. No grant is issued, and old authorizations never regain their generation.
    A post-bump retry may finish opening the same target without another bump.
    """
    from ..workflow.business_replay_inputs import implementation_hashes
    from ..workflow.isolation import verified_isolation_root
    from .route_arbitration import authority_lock
    from .simulation import selected_simulation

    root = verified_isolation_root()
    target = Path(route_registry.default_registry_path()).resolve()
    if root is None or not target.is_relative_to(root):
        raise RouteSwitchBlocked("stop recovery requires verified physical isolation")
    if not actor or not reason:
        raise RouteSwitchBlocked("recovery actor/reason required")
    report = SwitchReport(to_route="simulation", switch_token=switch_token)
    with authority_lock(target):
        try:
            state = route_registry.read_state(target)
            freeze = route_registry.read_freeze(target)
            report.from_route, report.from_generation = freeze.from_route, freeze.from_generation
            if not freeze.frozen or freeze.switch_token != switch_token:
                raise RouteSwitchBlocked("owned durable freeze token required")
            if state.generation != expected_generation:
                raise RouteSwitchBlocked("recovery generation changed")
            report.steps_completed.append("freeze")

            def check_target():
                broker = selected_simulation()
                if broker is None:
                    raise RouteSwitchBlocked("validated simulation target unavailable")
                broker.assert_active()
                proof = target_check() if callable(target_check) else {}
                reference = Path(proof.get("reference", "")).resolve()
                if (proof.get("valid") is not True or proof.get("route_id") != "simulation"
                        or proof.get("environment") != "paper" or proof.get("account") != broker.account
                        or not reference.is_file() or not reference.is_relative_to(root)
                        or proof.get("implementation") != implementation_hashes()):
                    raise RouteSwitchBlocked("current validated target proof missing or drifted")
                return broker, proof

            broker, proof = check_target()
            drain = _drain_reasons(freeze, lifecycle, path=target)
            # Do not let a single-cycle (or invented empty) view hide another
            # approved authorization in this transport's actual isolated store.
            from .authorization_lifecycle import lifecycle_for_cycle
            for row in broker.store.conn.execute("SELECT cycle_id FROM decision_cycles"):
                actual = lifecycle_for_cycle(row["cycle_id"],store=broker.store)
                if actual.blocks_route_switch():
                    drain.append("actual cycle still unfinished: " + row["cycle_id"])
            report.drain = {"lifecycle": lifecycle.blocking_summary() if lifecycle else None,
                            "target_reference": proof["reference"], "blockers": drain}
            if drain:
                raise RouteSwitchBlocked("stopped route drain blocked: " + ";".join(drain))
            report.steps_completed.append("drain")
            if state.generation == freeze.from_generation:
                check_target()
                report.state = route_registry.switch_route(
                    state.generation, "simulation", environment="paper", account=broker.account,
                    actor=actor, reason=reason, path=target)
            elif (state.generation == freeze.from_generation + 1 and state.route_id == "simulation"
                  and state.environment == "paper" and state.account == broker.account):
                report.state = state
            else:
                raise RouteSwitchBlocked("unexpected post-stop authority; keep frozen")
            report.to_generation = report.state.generation
            report.steps_completed.append("bump")
            check_target()
            route_registry.open_submissions(switch_token, path=target)
            report.steps_completed.append("open")
        except Exception as exc:  # noqa: BLE001 - recovery must never abort the durable stop
            report.aborted_at = _current_step(report)
            report.reasons.append(str(exc))
    return report
