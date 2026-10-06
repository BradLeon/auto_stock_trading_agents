"""Boundary enforcement at the actual write points (task 5.3).

Declaring a boundary is only half of 5.2; the other half is a check at the place
the thing actually happens. Without it, `approval_lifecycle` can be pointed at the
target route while `record_approval` keeps writing wherever it always wrote — the
config says Phase F is live and the code says otherwise.

Three write points, and they are enforcement points rather than wrappers around
everything:

- `guard_approval_write` — consulted by `DecisionAuditRepository.record_approval`
  and `record_review`. When the approval boundary is off the target route, the
  write is REFUSED rather than redirected. Same reasoning as the shadow ledger: a
  silent redirect makes the mistake look like it worked.
- `guard_clerk_publication` — consulted by `execution.clerk.clerk_run`. Bypassing
  the Clerk boundary is refused: publishing straight to the ledger from another
  path is exactly what the boundary exists to prevent.
- `guard_analyst_output` — consulted where an analyst's output is recorded.

None of these edit the modules they guard. The checks are injected as callables the
callers make, and the wiring is what the boundary's `declared wiring` names — so a
boundary whose guard was never called reports as unwired rather than as passing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import cutover as cutover_module


class BoundaryWriteRefused(RuntimeError):
    """A write was refused because its boundary does not currently allow it."""

    def __init__(self, boundary: str, message: str, *, reason_code: str,
                 route: str = "") -> None:
        super().__init__(message)
        self.boundary = boundary
        self.reason_code = reason_code
        self.route = route


@dataclass
class WriteDecision:
    """Whether a write is allowed, and under which route it will happen."""

    allowed: bool
    boundary: str
    route: str
    reason_code: str = ""
    reason: str = ""

    def as_row(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "boundary": self.boundary,
                "route": self.route, "reason_code": self.reason_code,
                "reason": self.reason}


def _route_of(boundary: str, reader: Callable[[str], Any] | None) -> str:
    if reader is not None:
        return str(reader(boundary))
    return cutover_module.read_boundary(boundary).route


def guard_write(boundary: str, *, what: str,
                route_reader: Callable[[str], Any] | None = None
                ) -> WriteDecision:
    """Decide whether a write at `boundary` is allowed now.

    Three routes, and `disabled` is a refusal rather than a silent no-op:

    - `target` — the new route is live; the write proceeds to it.
    - `legacy` — the old route is live; the write proceeds to it unchanged.
    - `disabled` — nothing is live for this boundary; the write is refused.

    The `legacy` case is why this does not simply check "is the boundary on target":
    a boundary still on the legacy route must keep working, or a cutover would break
    the system before it starts.
    """
    route = _route_of(boundary, route_reader)
    if route == cutover_module.ROUTE_DISABLED:
        return WriteDecision(
            False, boundary, route, reason_code="boundary_disabled",
            reason=f"{boundary} is disabled, so {what} has nowhere to go; enable it "
                   f"explicitly rather than writing somewhere unintended")
    return WriteDecision(True, boundary, route)


def guard_approval_write(*, what: str = "an approval record",
                         route_reader: Callable[[str], Any] | None = None
                         ) -> WriteDecision:
    """Guard for the approval-lifecycle write point.

    Separate from `guard_clerk_publication` even though both are ledger writes,
    because they are different boundaries with different compatibility rules: an
    approval can be live on the legacy chain while the Clerk still publishes to the
    legacy ledger, but not half of each.
    """
    decision = guard_write(cutover_module.APPROVAL_LIFECYCLE, what=what,
                           route_reader=route_reader)
    if not decision.allowed:
        raise BoundaryWriteRefused(
            cutover_module.APPROVAL_LIFECYCLE,
            f"refused to write {what}: {decision.reason}",
            reason_code=decision.reason_code, route=decision.route)
    return decision


def guard_clerk_publication(*, what: str = "a ledger publication",
                            route_reader: Callable[[str], Any] | None = None
                            ) -> WriteDecision:
    """Guard for the Clerk publication boundary.

    Bypassing the Clerk is refused rather than allowed for convenience: a
    publication that skips the boundary means the ledger contains rows no
    reconciliation pass will ever look at, which is the failure mode the Clerk
    exists to prevent.
    """
    decision = guard_write(cutover_module.CLERK_PUBLICATION, what=what,
                           route_reader=route_reader)
    if not decision.allowed:
        raise BoundaryWriteRefused(
            cutover_module.CLERK_PUBLICATION,
            f"refused to write {what}: {decision.reason}",
            reason_code=decision.reason_code, route=decision.route)
    return decision


def guard_analyst_output(*, what: str = "an analyst output",
                         route_reader: Callable[[str], Any] | None = None
                         ) -> WriteDecision:
    """Guard for the analyst-output boundary."""
    decision = guard_write(cutover_module.ANALYST_OUTPUT, what=what,
                           route_reader=route_reader)
    if not decision.allowed:
        raise BoundaryWriteRefused(
            cutover_module.ANALYST_OUTPUT,
            f"refused to write {what}: {decision.reason}",
            reason_code=decision.reason_code, route=decision.route)
    return decision


# --------------------------------------------------------------------------- #
# 5.5 — owner boundary for projection rendering and legacy read compatibility
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class OwnerBoundary:
    """Who is responsible for a slice of the migration, and what "done" means.

    Stated explicitly because the alternative is the state this task exists to
    prevent: a range that both sides believe the other owns. `migration_of` names
    the task that carries it, so "nobody" is a visible string rather than an absence.
    """

    slice_name: str
    current_owner: str
    target_owner: str
    migration_task: str
    done_when: str

    def as_row(self) -> dict[str, Any]:
        return {"slice": self.slice_name, "current_owner": self.current_owner,
                "target_owner": self.target_owner, "migration_task": self.migration_task,
                "done_when": self.done_when}


# The slices Phase F hands over. Both are named in the task list, and both have the
# same hazard: during the handover a range can be owned by nobody.
OWNER_BOUNDARIES: tuple[OwnerBoundary, ...] = (
    OwnerBoundary(
        slice_name="chief_projection_rendering",
        current_owner="ats.agents.chief.assemble",
        target_owner="ats.data.products (target projection)",
        migration_task="F.6.2 — Chief 投影渲染迁移",
        done_when="Chief assembles its context exclusively from the target projection "
                  "and the legacy context assembler has no callers left"),
    OwnerBoundary(
        slice_name="sector_cli_legacy_read",
        current_owner="ats.runtime.cli sector",
        target_owner="ats.data.products (target projection)",
        migration_task="F.6.3 — Sector CLI 旧读模型迁移",
        done_when="the Sector CLI reads through the target product API and the "
                  "legacy read model has no remaining consumer"),
)


def unowned_slices(declared: dict[str, str] | None = None) -> list[str]:
    """Slices with no declared owner.

    A slice counts as owned only when the declaration names an owner AND the slice
    carries a migration task. A missing task means the handover was described but
    never scheduled, which is the same practical situation as nobody owning it.
    """
    claimed = dict(declared or {})
    out = []
    for boundary in OWNER_BOUNDARIES:
        owner = str(claimed.get(boundary.slice_name, "")).strip()
        if not owner:
            out.append(boundary.slice_name)
        elif not boundary.migration_task.strip():
            out.append(boundary.slice_name)
    return out


def assert_no_unowned_slice(declared: dict[str, str] | None = None) -> None:
    """Refuse a cutover that would leave a migration range ownerless.

    The failure is not a bug that shows up later; it is a range where two parties
    each believe the other is handling it, which produces no error at all.
    """
    unowned = unowned_slices(declared)
    if unowned:
        detail = "; ".join(
            f"{b.slice_name} (current {b.current_owner} -> target {b.target_owner}, "
            f"migration {b.migration_task})" for b in OWNER_BOUNDARIES
            if b.slice_name in unowned)
        raise BoundaryWriteRefused(
            cutover_module.PROJECTION_READ,
            f"migration ranges with no declared owner: {detail}. Name an owner per "
            f"range before switching; a range both sides assume the other owns "
            f"produces no error, only silence.",
            reason_code="migration_range_unowned")


# The wiring this codebase declares. Kept as data so the boundary table, the
# pre-flight and the runbook all read the same names — a runbook that lists a
# switch name the code does not have is the failure task 5.13 is about.
DECLARED_BOUNDARY_WIRING: dict[str, tuple[tuple[str, str, str], ...]] = {
    cutover_module.PROJECTION_READ: (
        ("ats.workflow.cutover_routing.read_route",
         "ats.workflow.cutover_routing",
         "resolves the read route and gates it on exact-scope qualification"),
    ),
    cutover_module.ANALYST_OUTPUT: (
        ("ats.workflow.cutover_wiring.guard_analyst_output",
         "ats.workflow.cutover_wiring",
         "refuses an analyst output write when the boundary is disabled"),
    ),
    cutover_module.DISPATCHER_SCHEDULE: (
        ("ats.workflow.dispatch_claims.claim",
         "ats.workflow.dispatch_claims",
         "claims a logical trigger before executing it, under the current owner"),
    ),
    cutover_module.APPROVAL_LIFECYCLE: (
        ("ats.workflow.cutover_wiring.guard_approval_write",
         "ats.workflow.cutover_wiring",
         "refuses an approval write when the boundary is disabled"),
        ("ats.decision.repository.DecisionAuditRepository.record_approval",
         "ats.decision.repository",
         "writes the approval row; guarded through guard_approval_write"),
        ("ats.decision.repository.DecisionAuditRepository.record_review",
         "ats.decision.repository",
         "writes the review row; guarded through guard_approval_write"),
    ),
    cutover_module.CLERK_PUBLICATION: (
        ("ats.workflow.cutover_wiring.guard_clerk_publication",
         "ats.workflow.cutover_wiring",
         "refuses a ledger publication that bypasses the Clerk boundary"),
        ("ats.execution.clerk.clerk_run",
         "ats.execution.clerk",
         "publishes to the ledger for one window"),
    ),
    cutover_module.LIVE_TRADER: (
        ("ats.execution.broker_write_guard.check_grant",
         "ats.execution.broker_write_guard",
         "re-verifies the route generation and account at every submission"),
        ("ats.execution.authorization.validate_authorization",
         "ats.execution.authorization",
         "refuses an authorization not bound to the active route generation"),
    ),
}


def declare_repository_wiring(path: str | Path | None = None) -> list[Wiring]:
    """Declare this repository's wiring for all six boundaries.

    Called by bootstrap so a fresh installation is wired rather than reporting every
    boundary as unwired — which would make the pre-check fail for a reason that has
    nothing to do with the operator's decision.
    """
    declared = []
    for boundary, sites in DECLARED_BOUNDARY_WIRING.items():
        for call_site, authority, semantics in sites:
            declared.append(cutover_module.declare_wiring(
                boundary=boundary, call_site=call_site, authority=authority,
                semantics=semantics, path=path))
    return declared


def bootstrap_wired(*, actor: str = "", path: str | Path | None = None) -> None:
    """Bootstrap the plane AND declare this repository's wiring.

    The two belong together: an unwired plane is the correct state for a codebase
    that has not implemented a boundary, and an incorrect one for this codebase,
    which has implemented all six.
    """
    cutover_module.bootstrap(actor=actor, path=path)
    declare_repository_wiring(path=path)
