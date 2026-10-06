"""Cutover control plane: six boundaries, their wiring, and fail-closed start-up.

The guarantee Phase F needs is "exactly one route serves traffic per boundary",
and most of the ways to fail that are not about the routes at all — they are about
two components disagreeing about which route is active. So this module holds:

- **Six boundaries**, each with its own state, its own change history, and its own
  declared wiring. Independent switches, because a read-path cutover must not move
  the schedule boundary: coupling them would mean a read migration requires a
  schedule migration, which is exactly the bundling this is meant to avoid.
- **Declared wiring per boundary.** A boundary whose call sites are not declared is
  reported as `unwired` and cannot be used to justify a cutover. That is the check
  against a config file that switches something no code reads.
- **Mutual exclusion with fail-closed start-up.** Two boundaries active at once for
  one route, an activation without qualification, an unreadable authority — each
  refuses rather than degrading. Degrading is what makes the guarantee decorative.
- **Release-overlay-only writes.** A cutover changes the overlay and the control
  state; it never edits a fingerprint-constrained file (see
  `workflow.assurance_surface`), because doing so would invalidate the evidence the
  cutover is relying on.

The state is persisted rather than held in memory for the same reason
`route_registry` persists: the resident scheduler and a one-shot CLI are separate
processes, and a boundary whose state only one of them can see is not a boundary.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import REPO_ROOT

DEFAULT_PATH = "var/phase_f_cutover.sqlite"

# The six boundaries. Names match the spec's, and the report also carries the
# current owner so a reader can tell whether "off" means "legacy" or "nobody".
PROJECTION_READ = "projection_read"
ANALYST_OUTPUT = "analyst_output"
DISPATCHER_SCHEDULE = "dispatcher_schedule"
APPROVAL_LIFECYCLE = "approval_lifecycle"
CLERK_PUBLICATION = "clerk_publication"
LIVE_TRADER = "live_trader"

SIX_BOUNDARIES: tuple[str, ...] = (
    PROJECTION_READ, ANALYST_OUTPUT, DISPATCHER_SCHEDULE,
    APPROVAL_LIFECYCLE, CLERK_PUBLICATION, LIVE_TRADER,
)

# The route each boundary can point at. `target` is the Phase F route; the others
# are named so "switching" to one is a decision with a name rather than a boolean.
ROUTE_TARGET = "target"
ROUTE_LEGACY = "legacy"
ROUTE_DISABLED = "disabled"

VALID_ROUTES: frozenset[str] = frozenset({ROUTE_TARGET, ROUTE_LEGACY, ROUTE_DISABLED})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cutover_boundary_state (
    boundary TEXT PRIMARY KEY,
    route TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    wired INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cutover_boundary_history (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    boundary TEXT NOT NULL,
    from_route TEXT NOT NULL,
    to_route TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    changed_at TEXT NOT NULL
);
-- Declared call sites per boundary. A boundary with no rows here is `unwired`,
-- which blocks any cutover that would rely on it.
CREATE TABLE IF NOT EXISTS cutover_boundary_wiring (
    boundary TEXT NOT NULL,
    call_site TEXT NOT NULL,
    authority TEXT NOT NULL,
    semantics TEXT NOT NULL,
    declared_at TEXT NOT NULL,
    PRIMARY KEY (boundary, call_site)
);
CREATE TABLE IF NOT EXISTS cutover_activations (
    activation_id TEXT PRIMARY KEY,
    boundary TEXT NOT NULL,
    scope_hash TEXT NOT NULL,
    route TEXT NOT NULL,
    report_id TEXT NOT NULL DEFAULT '',
    activated_by TEXT NOT NULL DEFAULT '',
    activated_at TEXT NOT NULL,
    released_at TEXT NOT NULL DEFAULT '',
    release_reason TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS cutover_boundary_history_no_update
BEFORE UPDATE ON cutover_boundary_history
BEGIN SELECT RAISE(ABORT, 'cutover boundary history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS cutover_boundary_history_no_delete
BEFORE DELETE ON cutover_boundary_history
BEGIN SELECT RAISE(ABORT, 'cutover boundary history is append-only'); END;
"""

_LOCK = threading.Lock()


def default_cutover_db_path() -> str:
    return os.environ.get("ATS_CUTOVER_DB",
                          str(REPO_ROOT / DEFAULT_PATH))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    target = Path(path or default_cutover_db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


class CutoverError(RuntimeError):
    """A cutover action is refused. `reasons` says why, in operator terms."""


@dataclass(frozen=True)
class BoundaryState:
    """One boundary's current route, and whether anything is declared to read it."""

    boundary: str
    route: str
    updated_at: str = ""
    updated_by: str = ""
    reason: str = ""
    wired: bool = False

    @property
    def is_target(self) -> bool:
        return self.route == ROUTE_TARGET

    def as_row(self) -> dict[str, Any]:
        return {"boundary": self.boundary, "route": self.route,
                "updated_at": self.updated_at, "updated_by": self.updated_by,
                "reason": self.reason, "wired": self.wired}


# --------------------------------------------------------------------------- #
# 5.1 — six independent boundaries
# --------------------------------------------------------------------------- #

def bootstrap(*, actor: str = "", path: str | Path | None = None) -> dict[str, str]:
    """Create the six boundaries at their current (pre-cutover) routes.

    Defaults are `legacy` except the live trader, which starts `disabled`: no
    boundary may start pointed at the Phase F route, because a fresh installation
    that boots into the new route has not been qualified for it.
    """
    target = path or default_cutover_db_path()
    created: dict[str, str] = {}
    with _LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for boundary in SIX_BOUNDARIES:
                    row = conn.execute(
                        "SELECT route FROM cutover_boundary_state WHERE boundary=?",
                        (boundary,)).fetchone()
                    if row is not None:
                        created[boundary] = row["route"]
                        continue
                    route = ROUTE_DISABLED if boundary == LIVE_TRADER else ROUTE_LEGACY
                    conn.execute(
                        "INSERT INTO cutover_boundary_state (boundary, route,"
                        " updated_at, updated_by, reason, wired)"
                        " VALUES (?,?,?,?,?,0)", (boundary, route, _now(), actor,
                                                  "bootstrap"))
                    conn.execute(
                        "INSERT INTO cutover_boundary_history (boundary, from_route,"
                        " to_route, actor, reason, changed_at)"
                        " VALUES (?,?,?,?,?,?)", (boundary, "", route, actor,
                                                  "bootstrap", _now()))
                    created[boundary] = route
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return created


def read_boundary(boundary: str, path: str | Path | None = None) -> BoundaryState:
    if boundary not in SIX_BOUNDARIES:
        raise CutoverError(
            f"unknown boundary {boundary!r}; the six are {list(SIX_BOUNDARIES)}")
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM cutover_boundary_state WHERE boundary=?",
            (boundary,)).fetchone()
    if row is None:
        raise CutoverError(
            f"boundary {boundary!r} has no state; run bootstrap() first — an absent "
            f"boundary must not read as 'legacy'")
    return BoundaryState(boundary=boundary, route=row["route"],
                         updated_at=row["updated_at"], updated_by=row["updated_by"],
                         reason=row["reason"], wired=bool(row["wired"]))


def all_boundaries(path: str | Path | None = None) -> dict[str, BoundaryState]:
    """Every boundary's state. One unreadable boundary fails the whole read.

    Reading them individually would let a caller treat "I could not read the
    schedule boundary" as "the schedule boundary is off".
    """
    return {b: read_boundary(b, path) for b in SIX_BOUNDARIES}


def set_route(boundary: str, route: str, *, actor: str = "", reason: str = "",
              path: str | Path | None = None) -> BoundaryState:
    """Move ONE boundary. Others are untouched by construction.

    `reason` is required: a boundary change with no stated reason leaves the history
    readable but not explainable, and the history is the audit trail.
    """
    if route not in VALID_ROUTES:
        raise CutoverError(
            f"invalid route {route!r} for {boundary}; valid routes are "
            f"{sorted(VALID_ROUTES)}")
    if not reason.strip():
        raise CutoverError(
            f"a boundary change must state a reason; {boundary} -> {route} without "
            f"one is not auditable")
    target = path or default_cutover_db_path()
    current = read_boundary(boundary, target)
    with _LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE cutover_boundary_state SET route=?, updated_at=?,"
                    " updated_by=?, reason=? WHERE boundary=?",
                    (route, _now(), actor, reason, boundary))
                conn.execute(
                    "INSERT INTO cutover_boundary_history (boundary, from_route,"
                    " to_route, actor, reason, changed_at) VALUES (?,?,?,?,?,?)",
                    (boundary, current.route, route, actor, reason, _now()))
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return read_boundary(boundary, target)


def boundary_history(boundary: str, path: str | Path | None = None
                     ) -> list[dict[str, Any]]:
    """Every change, newest first. Who moved it, when, and why."""
    with _connect(path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM cutover_boundary_history WHERE boundary=?"
            " ORDER BY event_id DESC", (boundary,)).fetchall()]


# --------------------------------------------------------------------------- #
# 5.2 — declared wiring
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Wiring:
    """One declared call site for a boundary."""

    boundary: str
    call_site: str
    authority: str
    semantics: str

    def as_row(self) -> dict[str, Any]:
        return {"boundary": self.boundary, "call_site": self.call_site,
                "authority": self.authority, "semantics": self.semantics}


def declare_wiring(*, boundary: str, call_site: str, authority: str,
                   semantics: str, path: str | Path | None = None) -> Wiring:
    """Declare that `call_site` is where `boundary` is enforced.

    `authority` is who owns the decision — the component whose code must change for
    the boundary to mean something different. A boundary with no authority is a
    setting nobody reads.
    """
    if boundary not in SIX_BOUNDARIES:
        raise CutoverError(f"unknown boundary {boundary!r}")
    for label, value in (("call_site", call_site), ("authority", authority),
                         ("semantics", semantics)):
        if not str(value).strip():
            raise CutoverError(
                f"{boundary} wiring must declare a {label}; an undeclared one makes "
                f"the boundary unverifiable")
    target = path or default_cutover_db_path()
    with _LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO cutover_boundary_wiring (boundary, call_site,"
                    " authority, semantics, declared_at) VALUES (?,?,?,?,?)"
                    " ON CONFLICT(boundary, call_site) DO UPDATE SET"
                    " authority=excluded.authority, semantics=excluded.semantics",
                    (boundary, call_site, authority, semantics, _now()))
                conn.execute(
                    "UPDATE cutover_boundary_state SET wired=1 WHERE boundary=?",
                    (boundary,))
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return Wiring(boundary, call_site, authority, semantics)


def wiring_of(boundary: str, path: str | Path | None = None) -> list[Wiring]:
    with _connect(path) as conn:
        return [Wiring(row["boundary"], row["call_site"], row["authority"],
                       row["semantics"])
                for row in conn.execute(
                    "SELECT * FROM cutover_boundary_wiring WHERE boundary=?"
                    " ORDER BY call_site", (boundary,)).fetchall()]


def unwired_boundaries(path: str | Path | None = None) -> list[str]:
    """Boundaries with no declared call site.

    Reported rather than defaulted. A boundary that nothing reads still records a
    route, so a config that points it at the target looks like a cutover and does
    nothing — which is the failure this check exists to make visible.
    """
    return [b for b in SIX_BOUNDARIES if not wiring_of(b, path)]


def assert_wired(boundary: str, path: str | Path | None = None) -> list[Wiring]:
    """Refuse to rely on an unwired boundary.

    Used by activation and by the CLI pre-check. The message says what to do rather
    than only what is wrong.
    """
    sites = wiring_of(boundary, path)
    if not sites:
        raise CutoverError(
            f"boundary {boundary!r} has no declared wiring; declare at least one call "
            f"site (see PHASE_F_CUTOVER_RUNBOOK) — an unwired boundary cannot "
            f"justify a cutover because nothing reads it")
    return sites


# --------------------------------------------------------------------------- #
# 5.4 — cross-boundary compatibility
# --------------------------------------------------------------------------- #

# Combinations that cannot hold at the same time, and WHY. Stated as data so the
# pre-check reports the reason rather than "incompatible".
#
# The first pair is the one bundling would produce: migrating reads while the
# schedule still runs the old pipeline means the old pipeline writes into tables
# the new reader no longer treats as authoritative. The second is worse: analysis
# output switched while approvals and the clerk stay on the legacy path means
# decisions are taken on one representation and recorded on another.
INCOMPATIBLE: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({PROJECTION_READ, DISPATCHER_SCHEDULE}),
     "the projection read route and the schedule route must move together: the old "
     "schedule writes into stores the new reader no longer treats as authoritative"),
    (frozenset({ANALYST_OUTPUT, APPROVAL_LIFECYCLE}),
     "analyst output and approval lifecycle must move together: a decision taken on "
     "one representation cannot be recorded against another"),
    (frozenset({APPROVAL_LIFECYCLE, CLERK_PUBLICATION}),
     "approval lifecycle and clerk publication must move together: an approval "
     "recorded on one path and published on another is an approval nobody can audit"),
)


@dataclass
class CompatibilityVerdict:
    compatible: bool
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return {"compatible": self.compatible, "conflicts": list(self.conflicts)}


def check_compatibility(states: dict[str, BoundaryState] | None = None, *,
                        path: str | Path | None = None) -> CompatibilityVerdict:
    """Are the current routes jointly satisfiable?

    Only boundaries pointed at `target` participate. Two boundaries both on
    `legacy` is the pre-cutover state and is fine; a conflict is specifically about
    half-migrated combinations.
    """
    resolved = states if states is not None else all_boundaries(path)
    on_target = {b for b, s in resolved.items() if s.route == ROUTE_TARGET}
    conflicts = []
    for group, reason in INCOMPATIBLE:
        # Only a conflict when the pair is SPLIT: one on target, one not.
        members = group & set(SIX_BOUNDARIES)
        if not members:
            continue
        migrated = members & on_target
        if migrated and migrated != members:
            conflicts.append({
                "boundary_group": sorted(members),
                "on_target": sorted(migrated),
                "still_legacy": sorted(members - migrated),
                "reason": reason,
            })
    return CompatibilityVerdict(not conflicts, conflicts)


def assert_compatible(states: dict[str, BoundaryState] | None = None, *,
                      path: str | Path | None = None) -> CompatibilityVerdict:
    verdict = check_compatibility(states, path=path)
    if not verdict.compatible:
        lines = []
        for conflict in verdict.conflicts:
            lines.append(
                f"  {conflict['boundary_group']}: {conflict['on_target']} on target "
                f"vs {conflict['still_legacy']} not — {conflict['reason']}")
        raise CutoverError(
            "incompatible boundary combination; refusing to pick one side and carry "
            "on:\n" + "\n".join(lines))
    return verdict


# --------------------------------------------------------------------------- #
# 5.9 / 5.10 — activation gate and fail-closed pre-flight
# --------------------------------------------------------------------------- #

@dataclass
class ActivationRequest:
    """A request to point one boundary's scope at the target route."""

    boundary: str
    scope: dict[str, Any]
    consumer_id: str
    report_id: str = ""
    actor: str = ""

    def scope_hash(self) -> str:
        import hashlib

        return hashlib.sha256(
            json.dumps(self.scope or {}, sort_keys=True, default=str).encode()
        ).hexdigest()


@dataclass
class PreFlight:
    """What a pre-flight found. Never mutates anything."""

    ok: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    states: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": list(self.problems),
                "warnings": list(self.warnings), "checked": list(self.checked),
                "states": dict(self.states)}


def _scope_hash(scope: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(scope or {}, sort_keys=True, default=str).encode()).hexdigest()


def preflight(*, boundary: str | None = None,
              request: ActivationRequest | None = None,
              path: str | Path | None = None) -> PreFlight:
    """Check everything a cutover needs, changing nothing.

    Read-only by construction: it opens no transaction that writes, so running it
    cannot move a route. That matters because the CLI exposes it and an operator
    will run it repeatedly before deciding.
    """
    result = PreFlight(ok=True)
    target = path or default_cutover_db_path()

    try:
        states = all_boundaries(target)
    except (CutoverError, sqlite3.Error) as exc:
        # 5.10: an unreadable authority stops everything. Treating it as "no
        # conflict" is how two active routes get shipped.
        result.ok = False
        result.problems.append(
            f"the cutover authority could not be read: {type(exc).__name__}: {exc}")
        return result

    result.states = {b: s.as_row() for b, s in states.items()}
    result.checked.append("authority_readable")

    verdict = check_compatibility(states)
    if not verdict.compatible:
        result.ok = False
        for conflict in verdict.conflicts:
            result.problems.append(
                f"incompatible combination {conflict['boundary_group']}: "
                f"{conflict['reason']}")
    result.checked.append("cross_boundary_compatibility")

    for name in unwired_boundaries(target):
        message = (f"boundary {name!r} has no declared wiring; it cannot justify a "
                   f"cutover because nothing reads it")
        if boundary == name or boundary is None:
            result.ok = False
            result.problems.append(message)
        else:
            result.warnings.append(message)
    result.checked.append("wiring_declared")

    if request is not None:
        if request.boundary not in SIX_BOUNDARIES:
            result.ok = False
            result.problems.append(f"unknown boundary {request.boundary!r}")
        else:
            try:
                assert_wired(request.boundary, target)
            except CutoverError as exc:
                result.ok = False
                result.problems.append(str(exc))
            # 5.9: the gate constrains THIS activation only. It does not retroactively
            # deny a legacy route that is already serving — that would strand
            # traffic on a route nobody qualified either.
            existing = read_activation(request.boundary,
                                       _scope_hash(request.scope), target)
            if existing is not None and existing["released_at"]:
                result.warnings.append(
                    f"scope was released at {existing['released_at']} "
                    f"({existing['release_reason']}); re-activating requires a fresh "
                    f"report")
        result.checked.append("activation_request")

    # 5.9: a disabled live trader does not stop research services. Reported as a
    # warning rather than a problem, because the two are independent by design.
    if states[LIVE_TRADER].route != ROUTE_TARGET:
        result.warnings.append(
            f"the live trader is {states[LIVE_TRADER].route!r}; research and analysis "
            f"boundaries are unaffected by it and may still be switched")
    result.checked.append("live_trader_independence")

    return result


def read_activation(boundary: str, scope_hash: str,
                    path: str | Path | None = None) -> dict[str, Any] | None:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM cutover_activations WHERE boundary=? AND scope_hash=?"
            " ORDER BY activated_at DESC LIMIT 1", (boundary, scope_hash)).fetchone()
    return dict(row) if row is not None else None


def record_activation(*, request: ActivationRequest, route: str = ROUTE_TARGET,
                      path: str | Path | None = None) -> dict[str, Any]:
    """Record an activation. The caller must have run the gate first.

    Recorded rather than only asserted, so "which scopes are on the new route" is
    answerable after the fact rather than only inferable from current state.
    """
    from uuid import uuid4

    if route != ROUTE_TARGET:
        raise CutoverError(
            f"only the target route is activated through this path; {route!r} is a "
            f"boundary state, not an activation")
    target = path or default_cutover_db_path()
    assert_wired(request.boundary, target)
    activation_id = f"activation-{uuid4().hex[:32]}"
    with _LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO cutover_activations (activation_id, boundary,"
                    " scope_hash, route, report_id, activated_by, activated_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (activation_id, request.boundary, request.scope_hash(), route,
                     request.report_id, request.actor, _now()))
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return {"activation_id": activation_id, "boundary": request.boundary,
            "scope_hash": request.scope_hash(), "route": route}


def release_activation(*, boundary: str, scope_hash: str, actor: str = "",
                       reason: str = "", path: str | Path | None = None) -> bool:
    """Release one scope's activation. The row stays; the release is recorded."""
    if not reason.strip():
        raise CutoverError("releasing an activation must state a reason")
    with _connect(path) as conn:
        cursor = conn.execute(
            "UPDATE cutover_activations SET released_at=?, release_reason=?"
            " WHERE boundary=? AND scope_hash=? AND released_at=''",
            (_now(), reason, boundary, scope_hash))
        return cursor.rowcount > 0


def active_scopes(boundary: str, path: str | Path | None = None
                  ) -> list[dict[str, Any]]:
    with _connect(path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM cutover_activations WHERE boundary=? AND released_at=''"
            " ORDER BY activated_at", (boundary,)).fetchall()]
