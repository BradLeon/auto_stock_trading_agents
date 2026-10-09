"""Per-batch cutover manifest and dry-run (Phase F tasks 8.1–8.5).

What this is for
----------------
The cutover plan is six *boundaries*, but an operator does not switch a boundary —
they switch a **batch** of consumers, one batch at a time, each with its own
observation window and its own way back. That translation is what this module
holds, and it is deliberately separate from `cutover.py`: the control plane owns
what a boundary's route *is*, this owns which consumers move together and what it
costs to move them.

Five batch classes, because the traffic is genuinely different in each:

- `collection_publish` — the managed refresh chain. Already running in production
  on the new path, so it is **verified in place**, not switched. Treating it as a
  switchable batch is how a migration re-does work that already succeeded.
- `research_read` — the six analysis roles. Read-only; the risk is a wrong answer,
  not a wrong order.
- `schedule` — the dispatcher boundary. Switching it changes *when* things run,
  so its failure mode is double execution rather than a bad read.
- `internal_state_approval` — Chief, Risk, Trader, Clerk against internal state
  and the approval chain. The pairing matters: approval and publication are
  inseparable (see `cutover.INCOMPATIBLE`).
- `live_trader` — real orders. Starts `disabled` and stays there; it needs a
  separate, auditable authorisation that no other batch's evidence supplies.

Three rules that make the manifest more than a table
-----------------------------------------------------

**Every batch states its fallback, and a batch whose fallback is unavailable is
NOT_SWITCHABLE rather than "switch and hope".** `cutover_routing.assess_fallback`
already refuses to fall back somewhere unsafe; this refuses to *plan* a cutover
whose only way back is unsafe. A plan that admits "if this goes wrong we are stuck"
is not a plan, and finding that out during an incident is the worst time.

**Dry-run changes nothing.** It opens no write transaction and calls no setter, so
an operator can run it in a loop before deciding. That property is asserted, not
asserted-about: `dry_run` has no parameter that could move a route.

**A batch's readiness is recomputed, never remembered.** Qualification carries a
TTL and can be revoked, so a stored "ready" verdict expires silently. Every batch
is re-evaluated against live qualification at the moment it is checked (8.4), and a
batch whose qualification has drifted stops rather than proceeds on a stale yes.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..config import REPO_ROOT

DEFAULT_PATH = "var/phase_f_batches.sqlite"

# The five batch classes. Named as data so the manifest, the dry-run report and the
# runbook read the same vocabulary — a batch class spelled two ways is a class the
# operator cannot look up.
COLLECTION_PUBLISH = "collection_publish"
RESEARCH_READ = "research_read"
SCHEDULE = "schedule"
INTERNAL_STATE_APPROVAL = "internal_state_approval"
LIVE_TRADER = "live_trader"

BATCH_CLASSES: tuple[str, ...] = (
    COLLECTION_PUBLISH, RESEARCH_READ, SCHEDULE, INTERNAL_STATE_APPROVAL,
    LIVE_TRADER)

# Which cutover boundary governs each class. Two classes share a boundary
# (internal_state_approval covers approval_lifecycle AND clerk_publication)
# because they are incompatible halves of one chain and must move as one.
_BOUNDARY_BY_CLASS: dict[str, tuple[str, ...]] = {
    COLLECTION_PUBLISH: ("projection_read",),
    RESEARCH_READ: ("projection_read",),
    SCHEDULE: ("dispatcher_schedule",),
    INTERNAL_STATE_APPROVAL: ("approval_lifecycle", "clerk_publication"),
    LIVE_TRADER: ("live_trader",),
}

# Consumers per class, as scope. `collection_publish` has none: it is verified in
# place rather than switched, so there is no consumer whose read route moves.
_CONSUMERS_BY_CLASS: dict[str, tuple[str, ...]] = {
    COLLECTION_PUBLISH: (),
    RESEARCH_READ: ("layer", "information", "sector", "fundamental", "macro",
                    "technical"),
    SCHEDULE: (),
    INTERNAL_STATE_APPROVAL: ("chief", "risk", "trader", "clerk"),
    LIVE_TRADER: ("trader",),
}

# Batch outcomes. `NOT_SWITCHABLE` is distinct from `BLOCKED`: blocked means a
# gate says no right now, not-switchable means this batch has no safe way back and
# therefore must not be scheduled at all until that changes.
READY = "ready"
BLOCKED = "blocked"
NOT_SWITCHABLE = "not_switchable"
VERIFIED_IN_PLACE = "verified_in_place"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cutover_batches (
    batch_id TEXT PRIMARY KEY,
    batch_class TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    old_route TEXT NOT NULL DEFAULT '',
    new_route TEXT NOT NULL DEFAULT '',
    scope_json TEXT NOT NULL DEFAULT '{}',
    qualification_scope_json TEXT NOT NULL DEFAULT '{}',
    observation_window TEXT NOT NULL DEFAULT '',
    success_criteria TEXT NOT NULL DEFAULT '',
    stop_conditions TEXT NOT NULL DEFAULT '',
    fallback_route TEXT NOT NULL DEFAULT '',
    fallback_proof TEXT NOT NULL DEFAULT '',
    fallback_retired TEXT NOT NULL DEFAULT '',
    fallback_available TEXT NOT NULL DEFAULT '',
    fallback_drill_ref TEXT NOT NULL DEFAULT '',
    shadow_report_id TEXT NOT NULL DEFAULT '',
    required_surfaces_json TEXT NOT NULL DEFAULT '[]',
    direct_verification INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    declared_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_batches_class ON cutover_batches(batch_class);

-- Dry-run results. Append-only: a dry-run is a reading of the world at an instant,
-- and overwriting it would make "the batch was ready before we changed something"
-- unreconstructable.
CREATE TABLE IF NOT EXISTS cutover_batch_dryruns (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    ran_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    checks_json TEXT NOT NULL DEFAULT '{}',
    qualification_status TEXT NOT NULL DEFAULT '',
    report_citable INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dryruns_batch ON cutover_batch_dryruns(batch_id);
CREATE TRIGGER IF NOT EXISTS cutover_batch_dryruns_no_update
BEFORE UPDATE ON cutover_batch_dryruns
BEGIN SELECT RAISE(ABORT, 'dry-run results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS cutover_batch_dryruns_no_delete
BEFORE DELETE ON cutover_batch_dryruns
BEGIN SELECT RAISE(ABORT, 'dry-run results are append-only'); END;
"""

_LOCK = threading.Lock()


class BatchError(RuntimeError):
    """A batch declaration or dry-run request is refused."""


def default_batch_db_path() -> str:
    return os.environ.get("ATS_CUTOVER_BATCH_DB",
                          str(REPO_ROOT / DEFAULT_PATH))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    target = Path(path or default_batch_db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------------------- #
# 8.1 — the manifest
# --------------------------------------------------------------------------- #

@dataclass
class CutoverBatch:
    """One batch: what moves, who owns it, how you watch it, and how you come back.

    The fallback fields are four separate strings rather than one "safe: yes"
    because they fail differently and an operator needs to know WHICH is missing.
    """

    batch_id: str
    batch_class: str
    owner: str = ""
    old_route: str = ""
    new_route: str = ""
    scope: dict[str, Any] = field(default_factory=dict)
    qualification_scope: dict[str, Any] = field(default_factory=dict)
    observation_window: str = ""
    success_criteria: str = ""
    stop_conditions: str = ""
    fallback_route: str = ""
    fallback_proof: str = ""
    fallback_retired: str = ""
    fallback_available: str = ""
    fallback_drill_ref: str = ""
    shadow_report_id: str = ""
    required_surfaces: tuple[str, ...] = ()
    direct_verification: bool = False
    created_at: str = ""
    declared_by: str = ""

    @property
    def boundaries(self) -> tuple[str, ...]:
        return _BOUNDARY_BY_CLASS.get(self.batch_class, ())

    @property
    def consumers(self) -> tuple[str, ...]:
        declared = self.scope.get("consumers")
        if isinstance(declared, (list, tuple)):
            return tuple(str(name) for name in declared)
        return _CONSUMERS_BY_CLASS.get(self.batch_class, ())

    def as_row(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id, "batch_class": self.batch_class,
            "owner": self.owner, "old_route": self.old_route,
            "new_route": self.new_route, "scope": dict(self.scope),
            "qualification_scope": dict(self.qualification_scope),
            "boundaries": list(self.boundaries),
            "consumers": list(self.consumers),
            "observation_window": self.observation_window,
            "success_criteria": self.success_criteria,
            "stop_conditions": self.stop_conditions,
            "fallback_route": self.fallback_route,
            "fallback_proof": self.fallback_proof,
            "fallback_retired": self.fallback_retired,
            "fallback_available": self.fallback_available,
            "fallback_drill_ref": self.fallback_drill_ref,
            "shadow_report_id": self.shadow_report_id,
            "required_surfaces": list(self.required_surfaces),
            "direct_verification": self.direct_verification,
            "created_at": self.created_at, "declared_by": self.declared_by,
        }


def declare_batch(batch: CutoverBatch, *, path: str | Path | None = None
                  ) -> CutoverBatch:
    """Declare or update one batch. Idempotent by `batch_id`.

    A declaration with no observation window, success criteria or stop conditions
    is refused. Those three are what make a batch reviewable after the fact, and a
    batch missing them cannot answer "was this supposed to stop?" — which is the
    question an incident asks.
    """
    if batch.batch_class not in BATCH_CLASSES:
        raise BatchError(
            f"unknown batch class {batch.batch_class!r}; the five are "
            f"{list(BATCH_CLASSES)}")
    for label, value in (("owner", batch.owner),
                         ("observation_window", batch.observation_window),
                         ("success_criteria", batch.success_criteria),
                         ("stop_conditions", batch.stop_conditions)):
        if not str(value).strip():
            raise BatchError(
                f"batch {batch.batch_id!r} must declare a {label}; a batch without "
                "it cannot be reviewed after the fact")

    # 8.3: the already-running collection path is verified, not switched. So it must
    # carry NO switch action — recording a route pair for it implies a cutover that
    # should not happen, and an operator reading the manifest would look for one.
    if batch.direct_verification:
        if batch.batch_class != COLLECTION_PUBLISH:
            raise BatchError(
                f"batch {batch.batch_id!r} is marked direct-verification but is a "
                f"{batch.batch_class!r} batch; only the already-running "
                f"{COLLECTION_PUBLISH!r} path is verified in place")
        if batch.old_route or batch.new_route:
            raise BatchError(
                f"batch {batch.batch_id!r} is verified in place and must not "
                "declare a route change; recording one implies a switch that should "
                "not happen")
    elif not (batch.old_route and batch.new_route):
        raise BatchError(
            f"batch {batch.batch_id!r} must declare both the old and the new route")

    target = path or default_batch_db_path()
    record = CutoverBatch(**{**batch.__dict__,
                             "created_at": batch.created_at or _now()})
    with _LOCK:
        with _connect(target) as conn:
            conn.execute(
                "INSERT INTO cutover_batches (batch_id, batch_class, owner,"
                " old_route, new_route, scope_json, qualification_scope_json,"
                " observation_window, success_criteria, stop_conditions,"
                " fallback_route, fallback_proof, fallback_retired,"
                " fallback_available, fallback_drill_ref, shadow_report_id,"
                " required_surfaces_json, direct_verification, created_at,"
                " declared_by)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(batch_id) DO UPDATE SET"
                " batch_class=excluded.batch_class, owner=excluded.owner,"
                " old_route=excluded.old_route, new_route=excluded.new_route,"
                " scope_json=excluded.scope_json,"
                " qualification_scope_json=excluded.qualification_scope_json,"
                " observation_window=excluded.observation_window,"
                " success_criteria=excluded.success_criteria,"
                " stop_conditions=excluded.stop_conditions,"
                " fallback_route=excluded.fallback_route,"
                " fallback_proof=excluded.fallback_proof,"
                " fallback_retired=excluded.fallback_retired,"
                " fallback_available=excluded.fallback_available,"
                " fallback_drill_ref=excluded.fallback_drill_ref,"
                " shadow_report_id=excluded.shadow_report_id,"
                " required_surfaces_json=excluded.required_surfaces_json,"
                " direct_verification=excluded.direct_verification",
                (record.batch_id, record.batch_class, record.owner,
                 record.old_route, record.new_route,
                 json.dumps(record.scope, sort_keys=True, default=str),
                 json.dumps(record.qualification_scope, sort_keys=True,
                            default=str),
                 record.observation_window, record.success_criteria,
                 record.stop_conditions, record.fallback_route,
                 record.fallback_proof, record.fallback_retired,
                 record.fallback_available, record.fallback_drill_ref,
                 record.shadow_report_id,
                 json.dumps(list(record.required_surfaces)),
                 int(record.direct_verification), record.created_at,
                 record.declared_by))
    return record


def _row_to_batch(row: sqlite3.Row) -> CutoverBatch:
    return CutoverBatch(
        batch_id=row["batch_id"], batch_class=row["batch_class"],
        owner=row["owner"], old_route=row["old_route"],
        new_route=row["new_route"],
        scope=json.loads(row["scope_json"] or "{}"),
        qualification_scope=json.loads(row["qualification_scope_json"] or "{}"),
        observation_window=row["observation_window"],
        success_criteria=row["success_criteria"],
        stop_conditions=row["stop_conditions"],
        fallback_route=row["fallback_route"],
        fallback_proof=row["fallback_proof"],
        fallback_retired=row["fallback_retired"],
        fallback_available=row["fallback_available"],
        fallback_drill_ref=row["fallback_drill_ref"],
        shadow_report_id=row["shadow_report_id"],
        required_surfaces=tuple(json.loads(row["required_surfaces_json"] or "[]")),
        direct_verification=bool(row["direct_verification"]),
        created_at=row["created_at"], declared_by=row["declared_by"])


def read_batch(batch_id: str, *, path: str | Path | None = None) -> CutoverBatch:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM cutover_batches WHERE batch_id=?",
            (batch_id,)).fetchone()
    if row is None:
        raise BatchError(
            f"no batch {batch_id!r}; run the manifest command to list what is "
            "declared — an unknown batch must not read as an empty one")
    return _row_to_batch(row)


def list_batches(*, batch_class: str | None = None,
                 path: str | Path | None = None) -> list[CutoverBatch]:
    with _connect(path) as conn:
        if batch_class:
            rows = conn.execute(
                "SELECT * FROM cutover_batches WHERE batch_class=? ORDER BY batch_id",
                (batch_class,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cutover_batches ORDER BY batch_class, batch_id"
            ).fetchall()
    return [_row_to_batch(row) for row in rows]


# --------------------------------------------------------------------------- #
# 8.2 — the dry-run
# --------------------------------------------------------------------------- #

@dataclass
class DryRunResult:
    """One batch's readiness, re-evaluated at this instant.

    `checks` is a dict rather than a list because a caller usually wants to ask
    about ONE check ("is the fallback drilled?") without re-deriving the rest.
    """

    batch_id: str
    outcome: str
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    qualification_status: str = ""
    report_citable: bool = False

    @property
    def switchable(self) -> bool:
        """Only `ready` and `verified_in_place` allow progress.

        `verified_in_place` counts because that batch's whole point is that it
        needs no switch — blocking it would send an operator looking for a switch
        action that must not exist.
        """
        return self.outcome in {READY, VERIFIED_IN_PLACE}

    def as_row(self) -> dict[str, Any]:
        return {"batch_id": self.batch_id, "outcome": self.outcome,
                "switchable": self.switchable, "reasons": list(self.reasons),
                "checks": dict(self.checks),
                "qualification_status": self.qualification_status,
                "report_citable": self.report_citable}


def _flag(value: str) -> bool | None:
    """Read a tri-state flag. `None` means "not stated", which is NOT satisfied.

    Only the declared boolean words count. A free-form value — a drill reference,
    a note — must read as `None` rather than being guessed at from its presence:
    "there is something here" is not "this has been verified".
    """
    text = str(value or "").strip().lower()
    if text in ("true", "yes", "valid", "available", "drilled"):
        return True
    if text in ("false", "no", "missing", "unavailable", "not_drilled"):
        return False
    return None


def _drill_recorded(value: str) -> bool:
    """A drill counts as recorded when a non-empty reference names it.

    Separate from `_flag` on purpose: `fallback_drill_ref` holds a reference such
    as `drill-2026-10-01`, and asking a boolean parser about it produced a verdict
    that depended on whether the reference happened to start with a magic word.
    """
    return bool(str(value or "").strip())


def dry_run_batch(batch: CutoverBatch, *,
                  qualification_reader: Callable[[str, dict[str, Any]], dict[str, Any]]
                  | None = None,
                  report_checker: Callable[[CutoverBatch], tuple[bool, list[str]]]
                  | None = None,
                  path: str | Path | None = None,
                  cutover_path: str | Path | None = None,
                  record: bool = True) -> DryRunResult:
    """Is this batch switchable right now? **Changes nothing.**

    Read-only by construction: there is no parameter that could move a route, and
    the only write is the append-only dry-run record — which is a reading of the
    world, not a change to it. An operator runs this in a loop before deciding, so
    "read-only" has to be structural rather than a promise.

    8.2's order is the operator's order: can I get back, then is the evidence
    there, then does the gate still say yes. Fallback comes first because a batch
    with no safe way back is not "blocked pending evidence" — it must not be
    scheduled at all, and reporting it as merely blocked understates the problem.

    `cutover_path` is separate from `path` because the two are DIFFERENT files: the
    manifest is the plan, the control plane is the authority. Reading boundaries
    out of the manifest would report every one of them as absent and block every
    batch for a reason that lives in the wrong database.
    """
    from . import cutover as plane
    from . import cutover_routing as routing

    result = DryRunResult(batch_id=batch.batch_id, outcome=READY)
    reasons = result.reasons

    # --- 8.3: the already-running path is verified, not switched ---------------
    if batch.direct_verification:
        result.outcome = VERIFIED_IN_PLACE
        result.checks["already_in_production"] = True
        result.qualification_status = "not_required_for_in_place_verification"
        if record:
            _record_dry_run(result, path=path)
        return result

    # --- 1. can this batch be undone? ---------------------------------------
    verdict = routing.assess_fallback(
        batch.fallback_route, consumer_id=",".join(batch.consumers),
        proof_valid=_flag(batch.fallback_proof),
        retired=True if _flag(batch.fallback_retired) else
        (False if _flag(batch.fallback_retired) is False else None),
        available=_flag(batch.fallback_available))
    result.checks["fallback_safe"] = verdict["verdict"] == routing.FALLBACK_OK
    drilled = _drill_recorded(batch.fallback_drill_ref)
    result.checks["fallback_drill_recorded"] = drilled

    if verdict["verdict"] != routing.FALLBACK_OK:
        result.outcome = NOT_SWITCHABLE
        reasons.append(
            f"no safe way back: {verdict['reason']}. A batch whose only fallback is "
            "unavailable must not be scheduled at all — this is stronger than a "
            "missing gate, because switching is not the problem, returning is.")
    elif not drilled:
        result.outcome = NOT_SWITCHABLE
        reasons.append(
            "the fallback has never been drilled; an untested way back is not a way "
            "back. Record the drill reference before scheduling this batch.")

    # --- 2. the evidence this batch cites ----------------------------------
    from .shadow_reports import require_batch_report

    citable, problems = require_batch_report(batch, report_checker)
    result.report_citable = citable
    result.checks["shadow_report_citable"] = citable
    if not citable:
        reasons.extend(f"cited shadow report: {problem}" for problem in problems)

    # --- 3. live qualification (8.4: re-evaluated, never remembered) ---------
    if qualification_reader is not None and batch.consumers:
        # One consumer's answer is the batch's answer for a read scope: they move
        # together, so a single ineligible member holds the batch. Reporting per
        # consumer instead would suggest the others could go alone.
        statuses: list[str] = []
        for consumer_id in batch.consumers:
            verdict_q = qualification_reader(consumer_id, batch.qualification_scope)
            statuses.append(str(verdict_q.get("status", "")))
        eligible = [s for s in statuses if s == "eligible"]
        result.qualification_status = (
            "eligible" if len(eligible) == len(statuses)
            else f"{len(eligible)}/{len(statuses)} eligible")
        result.checks["qualified"] = len(eligible) == len(statuses)
        if len(eligible) != len(statuses):
            blocked_names = [c for c, s in zip(batch.consumers, statuses)
                             if s != "eligible"]
            reasons.append(
                f"qualification: {', '.join(blocked_names)} not eligible. "
                "Qualification carries a TTL and can be revoked, so it is "
                "re-read here rather than remembered from an earlier run.")
    else:
        result.checks["qualified"] = False

    # --- 4. the boundary itself must be movable ----------------------------
    try:
        states = plane.all_boundaries(cutover_path)
        conflicts = plane.check_compatibility(states)
        result.checks["boundaries_readable"] = True
        result.checks["cross_boundary_compatible"] = conflicts.compatible
        if not conflicts.compatible:
            reasons.append(
                "the current boundary combination is already half-migrated; resolve "
                "it before starting another batch")
        # `wiring_of` returns the declared call SITES. Empty means unwired —
        # asking "is the boundary absent from its own wiring list?" would read
        # every wired boundary as broken.
        unwired = [b for b in batch.boundaries
                   if not plane.wiring_of(b, cutover_path)]
        if unwired:
            reasons.append(
                f"boundaries with no declared wiring: {', '.join(unwired)}; an "
                "unwired boundary cannot justify a cutover because nothing reads it")
    except (plane.CutoverError, sqlite3.Error) as exc:
        result.checks["boundaries_readable"] = False
        reasons.append(
            f"the cutover authority could not be read: {type(exc).__name__}: {exc}. "
            "Treated as unreadable rather than as 'no conflict' — that is how two "
            "active routes get shipped.")

    if reasons and result.outcome == READY:
        result.outcome = BLOCKED

    if record:
        _record_dry_run(result, path=path)
    return result


def _record_dry_run(result: DryRunResult, *, path: str | Path | None = None) -> None:
    with _LOCK:
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO cutover_batch_dryruns (batch_id, ran_at, outcome,"
                " reasons_json, checks_json, qualification_status, report_citable)"
                " VALUES (?,?,?,?,?,?,?)",
                (result.batch_id, _now(), result.outcome,
                 json.dumps(result.reasons, ensure_ascii=False),
                 json.dumps(result.checks, sort_keys=True),
                 result.qualification_status, int(result.report_citable)))


def dry_run(*, batch_ids: Sequence[str] | None = None,
            qualification_reader: Callable[[str, dict[str, Any]], dict[str, Any]]
            | None = None,
            report_checker: Callable[[CutoverBatch], tuple[bool, list[str]]]
            | None = None,
            path: str | Path | None = None,
            cutover_path: str | Path | None = None) -> list[DryRunResult]:
    """Dry-run the manifest. Never changes a route.

    A recorded run is appended for every batch checked, including the ones that
    are not ready: "we looked and it was blocked" is the reading an operator needs
    when the blocker is later resolved, and it is what distinguishes a re-check
    from a first look.
    """
    batches = ([read_batch(b, path=path) for b in batch_ids] if batch_ids
               else list_batches(path=path))
    return [dry_run_batch(batch, qualification_reader=qualification_reader,
                          report_checker=report_checker, path=path,
                          cutover_path=cutover_path)
            for batch in batches]


# --------------------------------------------------------------------------- #
# 8.4 — drift: stop or fall back
# --------------------------------------------------------------------------- #

@dataclass
class DriftResponse:
    """What to do about a batch whose qualification moved since it was ready."""

    batch_id: str
    action: str
    reason: str
    qualification_status: str = ""

    def as_row(self) -> dict[str, Any]:
        return {"batch_id": self.batch_id, "action": self.action,
                "reason": self.reason,
                "qualification_status": self.qualification_status}


# Actions. `stop` and `fall_back` are different: stopping halts the batch and keeps
# it pending, falling back returns traffic to the old route.
STOP = "stop"
FALL_BACK = "fall_back"
HOLD = "hold"

# Statuses that mean the evidence was WITHDRAWN, as opposed to degraded. The
# distinction decides the action, so it is named rather than inferred:
#   - withdrawn  → stop. Falling back is itself gated on the qualification that
#                  just disappeared, so a rollback is advice nobody can execute.
#   - degraded   → a fall-back is possible IF traffic is being served and the
#                  fallback is proven; otherwise it holds.
#   - unknown    → stop, and say the gate's answer could not be read.
REVOKED_STATUSES: frozenset[str] = frozenset({
    "ineligible", "revoked", "ineligible_with_gaps", "manifest_drift",
    "dependency_drift", "evidence_missing",
})

DEGRADED_STATUSES: frozenset[str] = frozenset({
    "degraded", "partially_eligible", "stale", "expiring",
})


def respond_to_drift(batch: CutoverBatch, *, qualification_status: str,
                      serving_traffic: bool | None = None,
                      path: str | Path | None = None) -> DriftResponse:
    """Decide what a qualification change means for one batch (8.4).

    The asymmetry is the point. **A withdrawn qualification always stops; it never
    falls back.** Once a consumer is ineligible, reading qualification is what
    authorises traffic at all — so "fall back" would itself need the qualification
    that just disappeared. Falling back on a withdrawal is how a system keeps
    serving from the route whose evidence was taken away.

    A fall-back is only reachable from a DEGRADED verdict, and even then it needs
    two things this module will not assume:

    - `serving_traffic` stated rather than guessed. Guessing "it must be serving, we
      cut it over" produces rollback advice for a batch that never switched, which
      sends the operator somewhere else entirely.
    - a fallback that is proven and available. Otherwise the scope stops, because
      returning traffic to an unusable route is worse than not returning it.
    """
    if qualification_status == "eligible":
        return DriftResponse(batch.batch_id, HOLD, "qualification still holds",
                             qualification_status)

    # Withdrawn: stop. See the docstring — a rollback could not be executed.
    if qualification_status in REVOKED_STATUSES:
        return DriftResponse(
            batch.batch_id, STOP,
            f"qualification is {qualification_status} — the evidence was withdrawn. "
            "The batch stops rather than falls back, because falling back is itself "
            "gated on the qualification that is now absent.",
            qualification_status)

    if qualification_status not in DEGRADED_STATUSES:
        return DriftResponse(
            batch.batch_id, STOP,
            f"qualification is {qualification_status!r} — neither eligible, degraded, "
            "nor a recognised withdrawal, so the gate's own answer cannot be read. "
            "Treated as unreadable rather than as a pass.",
            qualification_status)

    # Degraded from here on: a fall-back may be right, but only if there is traffic
    # to bring back and somewhere safe to bring it back to.
    if serving_traffic is None:
        return DriftResponse(
            batch.batch_id, HOLD,
            "qualification is degraded and whether this batch is serving traffic is "
            "unknown; state it before recommending a rollback — advising one for a "
            "batch that never switched sends the operator somewhere else",
            qualification_status)

    if not serving_traffic:
        return DriftResponse(
            batch.batch_id, HOLD,
            "qualification is degraded but this batch is not serving traffic, so "
            "there is nothing to roll back; the batch becomes pending",
            qualification_status)

    from . import cutover_routing as routing

    verdict = routing.assess_fallback(
        batch.fallback_route, consumer_id=",".join(batch.consumers),
        proof_valid=_flag(batch.fallback_proof),
        retired=True if _flag(batch.fallback_retired) else None,
        available=_flag(batch.fallback_available))
    if verdict["verdict"] != routing.FALLBACK_OK:
        return DriftResponse(
            batch.batch_id, STOP,
            f"qualification changed while serving, and the fallback is unusable "
            f"({verdict['reason']}); the scope stops rather than falling back into "
            "something that is itself unsafe",
            qualification_status)
    return DriftResponse(batch.batch_id, FALL_BACK,
                         "qualification changed while serving; the fallback is "
                         "available and proven, so traffic returns to the old route",
                         qualification_status)


# --------------------------------------------------------------------------- #
# 8.5 — the batch report
# --------------------------------------------------------------------------- #

def summarize(results: Sequence[DryRunResult]) -> dict[str, Any]:
    """Per-batch counts. Never a single verdict — that hides which batch is why."""
    by_outcome: dict[str, int] = {}
    for result in results:
        by_outcome[result.outcome] = by_outcome.get(result.outcome, 0) + 1
    return {
        "total": len(results),
        "by_outcome": dict(sorted(by_outcome.items())),
        "switchable": [r.batch_id for r in results if r.switchable],
        "blocked": [r.batch_id for r in results if not r.switchable],
    }


def render_manifest(batches: Iterable[CutoverBatch]) -> str:
    """Operator-facing manifest. Every column a switch decision needs."""
    lines = ["# 逐批切流清单（F.0.6）", "",
             "| 批次 | 类别 | owner | 边界 | 观察窗口 | 停止条件 | 回退演练 | 报告 |",
             "|---|---|---|---|---|---|---|---|"]
    for batch in sorted(batches, key=lambda b: (b.batch_class, b.batch_id)):
        lines.append(
            f"| `{batch.batch_id}` | {batch.batch_class} | {batch.owner or '—'} | "
            f"{', '.join(batch.boundaries) or '—'} | "
            f"{batch.observation_window or '—'} | {batch.stop_conditions or '—'} | "
            f"{batch.fallback_drill_ref or '**未演练**'} | "
            f"{batch.shadow_report_id or '—'} |")
    lines += ["", "**回退未演练的批次判为 `not_switchable`，不是 `blocked`**——"
              "切换不是问题，能退回来才是。"]
    return "\n".join(lines)


def render_report(results: Sequence[DryRunResult], *,
                  batches: Iterable[CutoverBatch] | None = None) -> str:
    """The eligible/ineligible report, with each blocker's own text."""
    by_id = {b.batch_id: b for b in (batches or [])}
    summary = summarize(results)
    lines = ["# 逐批 dry-run 报告（F.0.6）", "",
             f"共 {summary['total']} 批 · "
             + " · ".join(f"{k} {v}" for k, v in summary["by_outcome"].items()),
             "",
             "**dry-run 不改变任何路由**：它只读资格与影子报告，把结论追加落账。", ""]
    lines += ["| 批次 | 类别 | 结论 | 资格 | 可切换 |", "|---|---|---|---|---|"]
    for result in sorted(results, key=lambda r: r.batch_id):
        batch = by_id.get(result.batch_id)
        lines.append(
            f"| `{result.batch_id}` | {batch.batch_class if batch else '—'} | "
            f"{result.outcome} | {result.qualification_status or '—'} | "
            f"{'是' if result.switchable else '否'} |")
    blocked = [r for r in results if not r.switchable]
    if blocked:
        lines += ["", "## 阻塞原因", ""]
        for result in sorted(blocked, key=lambda r: r.batch_id):
            lines.append(f"### `{result.batch_id}`（{result.outcome}）")
            for reason in result.reasons or ["（未记录原因）"]:
                lines.append(f"- {reason}")
    lines += ["", "**缺项只阻断受影响范围**：不得据此放行其他批次，也不得为使其可切"
              "而降低证据标准。**完整自动交易仍要求全部必需输入与审批链同时满足**。"]
    return "\n".join(lines)
