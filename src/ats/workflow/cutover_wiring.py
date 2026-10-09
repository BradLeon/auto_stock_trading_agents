"""Actual publication guards, declarations and migration responsibility.

The guards execute in analyst projection publishers, review/approval storage and
Clerk orchestration. Declaration inventory remains separate from exact-scope
execution evidence; integration alone does not confer production qualification.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    import sqlite3
    from contextlib import closing

    target = Path(cutover_module.default_cutover_db_path()).resolve()
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT route FROM cutover_boundary_state WHERE boundary=?",
                               (boundary,)).fetchone()
        if row is None or row[0] not in cutover_module.VALID_ROUTES:
            raise ValueError("missing or invalid boundary state")
        return row[0]
    except (sqlite3.Error, ValueError) as exc:
        raise BoundaryWriteRefused(boundary, f"boundary authority unreadable: {exc}",
                                   reason_code="boundary_authority_unreadable") from exc


def assert_write_destination(store, boundary: str) -> None:
    """Check the actual connection, including handles opened before isolation."""
    import os

    from .isolation import active_isolation_root, build_environment
    from .runtime_reads import current_read_context

    context = current_read_context()
    mode = context.mode if context else os.environ.get("ATS_RUN_MODE", "production")
    if os.environ.get("ATS_RUN_MODE") == "isolated":
        mode = "isolated"
    if mode not in {"isolated", "shadow"}:
        return
    from ..execution.broker_write_guard import assert_broker_writes_prohibited

    assert_broker_writes_prohibited(operation="publication", caller=boundary)
    filename = next((row[2] for row in store.conn.execute("PRAGMA database_list")
                     if row[1] == "main"), "")
    if not filename:  # A genuinely in-memory connection cannot reach production.
        return
    destination = Path(filename).resolve()
    allowed = {Path(os.environ[name]).resolve() for name in ("ATS_DB_PATH", "ATS_SHADOW_DB_PATH")
               if os.environ.get(name)}
    if context and context.publication_path:
        allowed.add(Path(context.publication_path).resolve())
    production = (cutover_module.REPO_ROOT / "var/ats.sqlite").resolve()
    root = active_isolation_root()
    if mode == "isolated" and root is None:
        # Child processes inherit the complete redirection, not the parent ContextVar.
        root = Path(os.environ.get("ATS_DB_PATH", "")).resolve().parent
        expected = build_environment(root)
        if any(os.environ.get(name) != filename for name, filename in expected.surfaces.items()):
            raise BoundaryWriteRefused(boundary, "incomplete isolated persistence redirection",
                                       reason_code="publication_destination_not_isolated")
    if (destination == production or destination not in allowed
            or (mode == "isolated" and root is not None and not destination.is_relative_to(root.resolve()))
            or (mode == "shadow" and (not context or not context.publication_path
                                     or destination != Path(context.publication_path).resolve()
                                     or destination == Path(os.environ.get("ATS_DB_PATH", production)).resolve()
                                     or (os.environ.get("ATS_SHADOW_DB_PATH") and destination !=
                                         Path(os.environ["ATS_SHADOW_DB_PATH"]).resolve())))):
        raise BoundaryWriteRefused(boundary, "isolated/shadow write destination is not owned by this run",
                                   reason_code="publication_destination_not_isolated")


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
    from .read_recovery import ReadStopped, current_policy
    from .runtime_reads import current_read_context

    context = current_read_context()
    from .joint_cutover import assert_scope_open, assert_boundary_open, epoch
    if route_reader is None:
        if context:
            assert_scope_open(context.identity,boundary=boundary)
            if epoch(context.identity)!=context.joint_epoch:
                raise BoundaryWriteRefused(boundary,"joint authority changed after worker binding",reason_code="joint_worker_stale")
        else:
            assert_boundary_open(boundary)
    if context:
        policy = current_policy(context.identity)
        if policy and policy["stopped"]:
            raise ReadStopped("stopped read scope cannot publish or authorize dependent work")
        if (policy["event_id"] if policy else 0) != context.recovery_generation:
            raise ReadStopped("recovery strategy changed; old bound worker cannot publish")
    route = _route_of(boundary, route_reader)
    if route == cutover_module.ROUTE_DISABLED:
        return WriteDecision(
            False, boundary, route, reason_code="boundary_disabled",
            reason=f"{boundary} is disabled, so {what} has nowhere to go; enable it "
                   f"explicitly rather than writing somewhere unintended")
    return WriteDecision(True, boundary, route)


def guard_approval_write(*, what: str = "an approval record",
                         store=None,
                         route_reader: Callable[[str], Any] | None = None
                         ) -> WriteDecision:
    """Guard for the approval-lifecycle write point.

    Separate from `guard_clerk_publication` even though both are ledger writes,
    because they are different boundaries with different compatibility rules: an
    approval can be live on the legacy chain while the Clerk still publishes to the
    legacy ledger, but not half of each.
    """
    if store is not None:
        assert_write_destination(store, cutover_module.APPROVAL_LIFECYCLE)
    decision = guard_write(cutover_module.APPROVAL_LIFECYCLE, what=what,
                           route_reader=route_reader)
    if not decision.allowed:
        raise BoundaryWriteRefused(
            cutover_module.APPROVAL_LIFECYCLE,
            f"refused to write {what}: {decision.reason}",
            reason_code=decision.reason_code, route=decision.route)
    return decision


def guard_clerk_publication(*, what: str = "a ledger publication",
                            store=None,
                            route_reader: Callable[[str], Any] | None = None
                            ) -> WriteDecision:
    """Guard for the Clerk publication boundary.

    Bypassing the Clerk is refused rather than allowed for convenience: a
    publication that skips the boundary means the ledger contains rows no
    reconciliation pass will ever look at, which is the failure mode the Clerk
    exists to prevent.
    """
    if store is not None:
        assert_write_destination(store, cutover_module.CLERK_PUBLICATION)
    decision = guard_write(cutover_module.CLERK_PUBLICATION, what=what,
                           route_reader=route_reader)
    if not decision.allowed:
        raise BoundaryWriteRefused(
            cutover_module.CLERK_PUBLICATION,
            f"refused to write {what}: {decision.reason}",
            reason_code=decision.reason_code, route=decision.route)
    return decision


def guard_analyst_output(*, what: str = "an analyst output",
                         store=None,
                         route_reader: Callable[[str], Any] | None = None
                         ) -> WriteDecision:
    """Guard for the analyst-output boundary."""
    if store is not None:
        assert_write_destination(store, cutover_module.ANALYST_OUTPUT)
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
        current_owner="ats.graph.chief.assemble_context",
        target_owner="ats.agents.chief.assemble.projection_context_block",
        migration_task="9.3",
        done_when="Chief 实际投影读取/渲染及旧读兼容；缺投影安全回退或停止，重启引用可解析"),
    OwnerBoundary(
        slice_name="sector_cli_legacy_read",
        current_owner="ats.runtime.cli.run_sector_html",
        target_owner="ats.workflow.cutover_routing.read_route",
        migration_task="9.6",
        done_when="Sector 实际 CLI 治理投影及兼容读；当次 scope 门禁、缺输入失败、跨进程引用"),
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
        if not owner or not valid_owner_mapping(boundary):
            out.append(boundary.slice_name)
    return out


def valid_owner_mapping(boundary: OwnerBoundary, *, tasks_path=None) -> bool:
    """Validate an actual task and its responsibility, not a nonempty label."""
    import re

    from ..config import REPO_ROOT
    from .boundary_evidence import resolve_site

    expected = {"chief_projection_rendering": ("9.3", ("Chief", "投影", "旧读兼容")),
                "sector_cli_legacy_read": ("9.6", ("Sector", "CLI", "治理投影"))}
    task, required = expected.get(boundary.slice_name, ("", ()))
    if boundary.migration_task != task or not task:
        return False
    path = Path(tasks_path or REPO_ROOT / "openspec/changes/implement-phase-f-shadow-run-and-cutover/tasks.md")
    try:
        matches = re.findall(r"^- \[[ x]\] " + re.escape(task) + r"\s+(.+)$",
                             path.read_text(), re.MULTILINE)
        resolve_site(boundary.current_owner)
        resolve_site(boundary.target_owner)
    except (OSError, ValueError):
        return False
    return len(matches) == 1 and all(token in matches[0] for token in required)


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


def declare_repository_wiring(path: str | Path | None = None) -> list[cutover_module.Wiring]:
    """Declare this repository's wiring for all six boundaries.

    Called by bootstrap to record intent. Helpers are not caller enforcement.
    """
    declared = []
    for boundary, sites in DECLARED_BOUNDARY_WIRING.items():
        for call_site, authority, semantics in sites:
            declared.append(cutover_module.declare_wiring(
                boundary=boundary, call_site=call_site, authority=authority,
                semantics=semantics, path=path))
    from .boundary_evidence import CALL_POINTS, MODE_SEMANTICS

    for boundary, points in CALL_POINTS.items():
        for point in points:
            declared.append(cutover_module.declare_wiring(
                boundary=boundary, call_site=point.site, authority=point.writer,
                semantics=json.dumps({"scope": point.scope, "modes": {
                    mode: MODE_SEMANTICS[mode] for mode in point.modes},
                    "guard": point.guard, "implementation_task": point.implementation_task,
                    "consumers": point.consumers,
                    "status": "declared"}), path=path))
    return declared


def bootstrap_wired(*, actor: str = "", path: str | Path | None = None) -> None:
    """Bootstrap the plane AND declare this repository's wiring.

    Historical API name retained. This declares intent only; it cannot establish
    enforcement. See boundary_evidence for code-bound business-entry proofs.
    """
    cutover_module.bootstrap(actor=actor, path=path)
    declare_repository_wiring(path=path)
