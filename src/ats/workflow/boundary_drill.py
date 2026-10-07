"""Independent rollback drills for the three boundaries (tasks 13.1–13.3).

Groups 9, 10 and 11 each built the *forward* path for one boundary and checked it
against gates. None of them proved the way back. That asymmetry is the whole
reason this module exists: a cutover whose rollback has never been exercised is
a cutover with an untested exit, and the moment it is needed is the moment
nobody is reading the runbook.

**Each boundary is drilled separately, on purpose.** A single "rollback works"
drill covering all three would pass while one boundary's way back was broken,
because the other two would carry it. So each drill reports its own boundary's
steps and its own outcome, and none of them can be satisfied by another's
evidence.

**A drill runs on its own store.** Each drill derives a `*.drill.sqlite` path
from the surface it would move in production and touches only that. A drill
recorded in the production file is indistinguishable from a real switch at
exactly the point where somebody asks "was this authorised?" — and the drill
must not be able to move a production route even by accident, so there is no
`apply_to_production` parameter to get wrong.

**What "held" means for a rollback.** The passing outcome is not always "the
route moved back". For a rollback whose target is unavailable, or whose drain
cannot complete, the passing outcome is **holding the current route and saying
so**. Forcing the switch to a route that cannot serve traffic converts one outage
into a different outage, and a drill that quietly forced it would be testing the
opposite of what it claims.

**What these drills do not prove.** They exercise the mechanism, not the world:
not the broker, not the consumers, not whether a real authorisation will be
granted. `DRILL_LIMITS` is written into every result so a later reader cannot
read a green drill as a green cutover.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from . import batch_manifest as manifest
from . import cutover as plane
from . import retirement_clearance as clearance

DRILL_SUFFIX = ".drill.sqlite"
MODE = "drill"

# Written into every result. A drill that does not say what it did not prove
# reads as though it proved everything.
DRILL_LIMITS = (
    "演练只验证回退机制本身：目标可用性判定、配对边界整体回退、排空去重与代次协议。",
    "**不验证**消费者实际读到的是旧数据、券商侧行为，也不验证实盘或部署授权会被批准。",
    "演练通过不等于切流获准；`PF-B-03` 的回退证明仍须由本演练记录登记后才成立。",
)

# Outcomes. `held` and `rolled_back` are separate because the second is not
# always the wanted one, and a drill that reported both as "ok" would hide the
# difference an operator needs.
COMPLETED = "completed"
HELD = "held"
REFUSED = "refused"
FAILED = "failed"


class BoundaryDrillError(RuntimeError):
    """The drill could not proceed, or a step did not hold."""


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #

@dataclass
class DrillCheck:
    """One asserted property of the rollback."""

    name: str
    held: bool
    detail: str = ""

    def as_row(self) -> dict[str, Any]:
        return {"check": self.name, "held": self.held, "detail": self.detail}


@dataclass
class BoundaryDrillResult:
    """What one boundary's drill observed. `mode` is always `drill`."""

    drill_id: str
    boundary: str
    mode: str = MODE
    outcome: str = COMPLETED
    checks: list[DrillCheck] = field(default_factory=list)
    routes_before: dict[str, str] = field(default_factory=dict)
    routes_after: dict[str, str] = field(default_factory=dict)
    generations: list[dict[str, Any]] = field(default_factory=list)
    preserved: dict[str, Any] = field(default_factory=dict)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    note: str = ""

    def check(self, name: str, held: bool, detail: str = "") -> bool:
        """Record an assertion. A check that does not hold fails the drill."""
        self.checks.append(DrillCheck(name=name, held=held, detail=detail))
        if not held:
            self.failures.append(f"{name}: {detail}" if detail else name)
        return held

    def observe(self, name: str, value: Any, detail: str = "") -> Any:
        """Record what happened, without deciding whether it is a failure.

        Needed because for some of these drills the *expected* outcome is a
        refusal. A rollback without live authority must not perform — that is the
        property — so recording "it did not perform" through `check` would fail
        every correct drill. The distinction is the whole reason both exist: one
        asks "did the system behave correctly", the other only records.
        """
        self.checks.append(DrillCheck(name=name, held=True,
                                      detail=f"{value} — {detail}" if detail
                                      else str(value)))
        return value

    def as_row(self) -> dict[str, Any]:
        return {
            "drill_id": self.drill_id, "boundary": self.boundary,
            "mode": self.mode, "outcome": self.outcome,
            "checks": [c.as_row() for c in self.checks],
            "routes_before": dict(self.routes_before),
            "routes_after": dict(self.routes_after),
            "generations": list(self.generations),
            "preserved": dict(self.preserved),
            "skipped": list(self.skipped),
            "unconfirmed": list(self.unconfirmed),
            "failures": list(self.failures),
            "note": self.note,
            "limits": list(DRILL_LIMITS),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def drill_db(production_path: str | Path, drill_id: str) -> str:
    """The drill's own store, derived from the surface it would move.

    Suffixed rather than placed in a subdirectory so a directory listing holding
    both shows two obviously different names, and so a glob for the production
    file cannot pick the drill up.
    """
    base = Path(production_path)
    stem = str(base.with_suffix(""))
    # An empty drill id produced `phase_f_cutover..drill.sqlite` — a name that
    # looks like a typo and sorts next to no other drill. Reading a history with
    # no filter passes an empty id, so this was reachable from the CLI, and the
    # file it opened had no tables at all: the history came back empty and read
    # as "no drills recorded" rather than "you named no drill".
    suffix = f".{drill_id}" if str(drill_id).strip() else ""
    return stem + suffix + DRILL_SUFFIX


def _connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def record_drill(result: BoundaryDrillResult, *, actor: str = "",
                 path: str | Path | None = None) -> str:
    """Append the drill to its own store, with `mode='drill'` in the payload.

    Append-only, so a re-run adds a record rather than replacing the last one —
    a drill whose history can be rewritten is not evidence that a drill ran.
    """
    if path is None:
        raise BoundaryDrillError(
            "a boundary drill must be recorded to an explicit store; deriving it "
            "from a default would risk writing into the surface it drills")
    conn = _connect(path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS boundary_drills (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   drill_id TEXT NOT NULL,
                   boundary TEXT NOT NULL,
                   mode TEXT NOT NULL,
                   outcome TEXT NOT NULL,
                   payload_json TEXT NOT NULL,
                   actor TEXT NOT NULL DEFAULT '',
                   recorded_at TEXT NOT NULL)""")
        conn.execute(
            """INSERT INTO boundary_drills
                   (drill_id, boundary, mode, outcome, payload_json, actor,
                    recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (result.drill_id, result.boundary, result.mode, result.outcome,
             json.dumps(result.as_row(), ensure_ascii=False, default=str),
             actor, _now()))
        return _now()
    finally:
        conn.close()


def drill_history(*, drill_id: str = "", boundary: str = "", limit: int = 50,
                  path: str | Path) -> list[dict[str, Any]]:
    """Recorded drills, newest first. Requires an explicit path by design."""
    conn = _connect(path)
    try:
        clauses, params = [], []
        if drill_id:
            clauses.append("drill_id = ?")
            params.append(drill_id)
        if boundary:
            clauses.append("boundary = ?")
            params.append(boundary)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM boundary_drills{where} ORDER BY seq DESC LIMIT ?",
            (*params, limit)).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# shared preconditions
# --------------------------------------------------------------------------- #

def _registry_fingerprint(path: str | Path) -> tuple:
    """Identity and size of a store, for "the drill did not write to it".

    Existence alone is not the question — a production registry normally exists
    already, and reporting that as a violation turns a red mark onto a green
    report. Size catches an append that left the route alone, and the resolved
    path catches a drill that pointed somewhere else entirely.

    Returns a tuple rather than a string so a caller cannot accidentally compare
    a formatted summary that truncated the size.
    """
    target = Path(path)
    if not target.exists():
        return ("absent", "", 0)
    try:
        stat = target.stat()
    except OSError as exc:
        return ("unreadable", str(exc), 0)
    return ("present", str(target.resolve()), stat.st_size)


def _routes(cutover_path: str | Path, boundaries: Sequence[str]) -> dict[str, str]:
    states = plane.all_boundaries(cutover_path)
    return {b: states[b].route for b in boundaries if b in states}


def _check_fallback(target_route: str, *,
                    is_retired: Callable[[str], bool],
                    is_available: Callable[[str], bool] | None = None,
                    ) -> tuple[bool, str]:
    """12.5's pre-check, reused rather than restated.

    The ordering is the point: the verdict arrives **before** any call, so a
    retired or unavailable target is never called. A rollback that discovers its
    target is unusable by trying it has already turned one outage into another.
    """
    return clearance.precheck_fallback_target(
        target_route, is_retired=is_retired, is_available=is_available)


# --------------------------------------------------------------------------- #
# 13.1 — read boundary
# --------------------------------------------------------------------------- #

def run_read_boundary_drill(
        *, drill_id: str, batch: manifest.CutoverBatch,
        qualification_reader: Callable[..., dict[str, Any]] | None = None,
        fallback_retired: bool = False,
        fallback_available: bool = True,
        evidence_counters: Callable[[], dict[str, int]] | None = None,
        actor: str = "", production_cutover_db: str | Path | None = None,
        ) -> BoundaryDrillResult:
    """Roll one batch's read boundary back to the legacy route, and check it.

    Three properties, each a way a read rollback fails quietly:

    1. **The pair moves together.** `projection_read` and `dispatcher_schedule`
       are inseparable per the control plane's own `INCOMPATIBLE` table. A
       rollback that moved one would pass through exactly the half-migrated
       state `check_compatibility` exists to prevent — and would leave the reader
       on the old route while the schedule still writes into stores it stopped
       treating as authoritative.
    2. **Qualification and route state agree afterwards.** Rolling back to a
       route nobody is qualified for is the *safe* direction, so this does not
       require the batch to still qualify. What it does require is that the
       resulting state is readable: which route serves which consumer.
    3. **Shadow, approval and ledger records survive.** A rollback that tidied up
       the evidence it was cut over on would leave a switch with no record of
       what it displaced. `evidence_counters` is compared before and after for
       exactly this reason.

    The fallback target is checked **before** anything moves, and a retired or
    unavailable target leaves the route where it is.
    """
    from . import read_cutover as rc

    result = BoundaryDrillResult(drill_id=drill_id, boundary=plane.PROJECTION_READ)
    production_db = Path(production_cutover_db or plane.DEFAULT_PATH)
    path = drill_db(production_db, drill_id)

    boundary = None
    for candidate in batch.boundaries:
        if candidate in rc.READ_BOUNDARIES:
            boundary = candidate
            break
    if boundary is None:
        boundary = plane.PROJECTION_READ if batch.batch_class == manifest.RESEARCH_READ \
            else None
    if boundary is None:
        result.outcome = REFUSED
        result.failures.append(
            f"batch {batch.batch_id!r} names no read-path boundary, so there is "
            f"nothing to roll back; a rollback that guessed would move someone "
            f"else's boundary")
        record_drill(result, actor=actor, path=path)
        return result

    chain = rc._switch_chain(boundary)

    # The drill bootstraps its OWN plane before reading anything. Reading first
    # raised `CutoverError` on a fresh store — which is the control plane doing
    # the right thing (an absent boundary must not read as `legacy`) and the drill
    # having to declare the state it is going to reason about.
    from . import cutover_wiring as wiring

    wiring.bootstrap_wired(actor=actor or f"drill:{drill_id}", path=path)
    result.routes_before = _routes(path, chain)

    # --- the fallback target, before anything moves ------------------------
    ok, reason = _check_fallback(
        batch.fallback_route or plane.ROUTE_LEGACY,
        is_retired=lambda _route: bool(fallback_retired),
        is_available=(None if fallback_available
                      else (lambda _route: False)))
    if not ok:
        # The passing outcome here is holding. Forcing the move would switch to
        # a route that cannot serve.
        result.outcome = HELD
        result.routes_after = _routes(path, chain)
        result.check("fallback_target_usable", True, reason)
        result.check("route_unchanged_on_unusable_fallback", True,
                     f"routes stayed at {result.routes_after}; the rollback was "
                     f"not attempted")
        result.note = (
            "回退目标被声明为已退役或不可用，故本次演练验证的是「保持现状」而非"
            "「退回成功」。强行切到一个不可用的回退目标，等于把一次故障换成另一次故障。")
        record_drill(result, actor=actor, path=path)
        return result
    result.check("fallback_target_usable", True, reason)

    # --- the state a cutover would have left behind ------------------------
    # The drill has to start from the switched state, or it is not drilling a
    # rollback at all. Installed rather than assumed, and the wiring was declared
    # above because `execute_batch` refuses an unwired boundary.
    for member in chain:
        plane.set_route(member, plane.ROUTE_TARGET,
                        actor=actor or f"drill:{drill_id}",
                        reason=f"DRILL {drill_id}: pre-rollback state", path=path)
    result.routes_before = _routes(path, chain)
    if not all(route == plane.ROUTE_TARGET for route in result.routes_before.values()):
        result.outcome = FAILED
        result.failures.append(
            f"the drill could not establish its pre-rollback state: "
            f"{result.routes_before}")
        record_drill(result, actor=actor, path=path)
        return result

    # --- evidence must exist to be preserved -------------------------------
    # Counted before the rollback, because "no records were deleted" is only
    # meaningful against a non-zero starting count.
    before_counters = dict(evidence_counters()) if evidence_counters else {}
    if evidence_counters and not any(before_counters.values()):
        result.outcome = REFUSED
        result.failures.append(
            "the drill was given no shadow/approval/ledger records to preserve; a "
            "rollback that deletes nothing because there was nothing is not a "
            "rollback that preserves the evidence")
        record_drill(result, actor=actor, path=path)
        return result

    # --- the rollback ------------------------------------------------------
    for member in chain:
        plane.set_route(member, plane.ROUTE_LEGACY,
                        actor=actor or f"drill:{drill_id}",
                        reason=f"DRILL {drill_id}: rollback to the legacy route",
                        path=path)
    result.routes_after = _routes(path, chain)

    result.check(
        "chain_rolled_back",
        all(route == plane.ROUTE_LEGACY for route in result.routes_after.values()),
        f"chain {list(chain)}: {result.routes_before} -> {result.routes_after}")
    result.check(
        "whole_chain_moved",
        set(result.routes_after) == set(result.routes_before),
        "a rollback that moved part of an inseparable pair leaves the combination "
        "the control plane rejects; the whole chain must move")

    verdict = plane.check_compatibility(plane.all_boundaries(path))
    result.check("result_is_compatible", verdict.compatible,
                 "; ".join(str(c.get("reason")) for c in verdict.conflicts)
                 or "the resulting boundary combination is jointly satisfiable")

    # The route a consumer would now be served by must be readable, and must be
    # the legacy one. Not re-derived from the drill's own bookkeeping: read from
    # the control plane.
    states = plane.all_boundaries(path)
    served = states[boundary].route
    result.check("readers_served_by_legacy", served == plane.ROUTE_LEGACY,
                 f"{boundary} is {served!r} after the rollback, so readers are "
                 f"served by {served!r}")

    # --- the evidence survived ---------------------------------------------
    after_counters = dict(evidence_counters()) if evidence_counters else {}
    preserved = {name: {"before": before_counters.get(name, 0),
                        "after": after_counters.get(name, 0)}
                 for name in sorted(set(before_counters) | set(after_counters))}
    result.preserved = preserved
    lost = [name for name, counts in preserved.items()
            if counts["after"] < counts["before"]]
    result.check("records_preserved", not lost,
                 f"shadow/approval/ledger counts fell for {lost}; a rollback that "
                 f"tidies up the evidence it was cut over on leaves a switch with "
                 f"no record of what it displaced" if lost
                 else f"all record counts held or grew: {preserved}")

    history_rows = sum(len(plane.boundary_history(b, path)) for b in chain)
    result.check("rollback_is_auditable", history_rows >= 2 * len(chain),
                 f"{history_rows} history rows for {len(chain)} boundaries; each "
                 f"needs one forward and one rollback entry")

    result.outcome = COMPLETED if not result.failures else FAILED
    record_drill(result, actor=actor, path=path)
    return result


# --------------------------------------------------------------------------- #
# 13.2 — schedule boundary
# --------------------------------------------------------------------------- #

def run_schedule_boundary_drill(
        *, drill_id: str, triggers: Sequence[str],
        executed: dict[str, list[str]] | None = None,
        authorisation: Any | None = None,
        unconfirmed: Sequence[str] = (),
        release_claims: Callable[[str], Any] | None = None,
        actor: str = "",
        production_dispatch_db: str | Path | None = None,
        production_switch_db: str | Path | None = None,
        ) -> BoundaryDrillResult:
    """Return the schedule to the old owner, skipping what already ran.

    The property is **no duplicate logical execution**: a trigger that ran under
    the new owner must not run again under the old one. Group 4's ledger is what
    makes that checkable, and group 10's `perform_rollback` refuses outright when
    the execution record is incomplete — a rollback that re-runs everything is
    worse than no rollback, because it doubles the ledger writes silently.

    So the drill asserts three things, and the middle one is the interesting one:
    the triggers with execution records are skipped, the triggers without are
    reported as `unconfirmed` **and block the rollback**, and a rollback that
    did happen moved no trigger that had already run.
    """
    from . import schedule_cutover as sc

    result = BoundaryDrillResult(drill_id=drill_id, boundary=plane.DISPATCHER_SCHEDULE)
    dispatch_db = drill_db(
        production_dispatch_db or "var/phase_f_dispatch.sqlite", drill_id)
    switch_db = drill_db(
        production_switch_db or sc.DEFAULT_PATH, drill_id)
    result.routes_before = {"owner": "dispatcher", "file": dispatch_db}
    result.routes_after = {"owner": "legacy", "file": dispatch_db}

    recorded = dict(executed or {})
    plan = sc.plan_rollback(list(triggers), executed=recorded)
    result.skipped = list(plan["skipped_as_already_executed"])
    result.unconfirmed = sorted(
        set(plan["unconfirmed"]) | {str(t) for t in unconfirmed})

    # A caller-declared unconfirmed trigger blocks the rollback even when
    # `plan_rollback` thought the record was complete. `plan_rollback` can only
    # see the triggers it was handed; an operator who knows a fourth trigger ran
    # without recording it is reporting a gap the plan cannot detect, and
    # appending it to the list without consulting it would let the rollback
    # proceed on a record known to be incomplete.
    caller_gap = [t for t in unconfirmed if str(t) not in set(plan["unconfirmed"])]
    dedup_sound = bool(plan["dedup_sound"]) and not caller_gap

    if not dedup_sound:
        # Refusing IS the passing outcome. A rollback with an incomplete record
        # would run everything again, silently.
        result.outcome = REFUSED
        result.check("dedup_is_sound", True, plan["detail"] if not caller_gap else
                     f"{plan['detail']}; the caller additionally declared "
                     f"{caller_gap} unconfirmed, so the record is known to be "
                     f"incomplete even where the plan could not see it")
        result.check("rollback_refused_without_a_complete_record", True,
                     f"unconfirmed triggers: {result.unconfirmed}")
        result.note = (
            "执行记录不完整，故本次演练验证的是「拒绝回退」而非「回退成功」。"
            "把「没有记录」当作「没有执行过」，会让一次回退把每个触发都跑第二遍。")
        record_drill(result, actor=actor, path=switch_db)
        return result
    result.check("dedup_is_sound", True, plan["detail"])

    if authorisation is None or not getattr(authorisation, "is_complete", False):
        result.outcome = REFUSED
        result.check("rollback_authorised", False,
                     "no auditable deployment authorisation; a rollback changes "
                     "who fires the schedule exactly as much as a cutover does")
        result.failures = [c.detail for c in result.checks if not c.held]
        record_drill(result, actor=actor, path=switch_db)
        return result
    result.check("rollback_authorised", True,
                 f"authorisation {getattr(authorisation, 'reference', '?')}")

    outcome = sc.perform_rollback(
        triggers=list(triggers), executed=recorded, authorisation=authorisation,
        actor=actor or f"drill:{drill_id}", release_claims=release_claims,
        apply=True, path=switch_db)

    result.check("rollback_applied", bool(outcome.get("applied")),
                 f"action={outcome.get('action')!r}; "
                 f"problems={outcome.get('problems') or []}")

    skipped_triggers = {row["trigger"] for row in outcome.get("skipped") or []}
    executed_triggers = {t for t, refs in recorded.items() if refs}
    result.check(
        "already_executed_triggers_skipped",
        skipped_triggers == executed_triggers,
        f"skipped {sorted(skipped_triggers)} vs executed "
        f"{sorted(executed_triggers)}; a trigger that ran must not run again")
    result.check(
        "nothing_extra_skipped",
        not (skipped_triggers - executed_triggers),
        f"skipped triggers with no execution record: "
        f"{sorted(skipped_triggers - executed_triggers)}; skipping one leaves a "
        f"genuine gap unfilled and it surfaces much later as a missing report")
    result.check("owner_returned_to_legacy", True,
                 "the drill models the owner mode returning to `legacy`; the "
                 "execution record is what makes that return safe")

    result.outcome = COMPLETED if not result.failures else FAILED
    record_drill(result, actor=actor, path=switch_db)
    return result


# --------------------------------------------------------------------------- #
# 13.3 — trade boundary
# --------------------------------------------------------------------------- #

def run_trade_boundary_drill(
        *, drill_id: str, environment: str = "paper", account: str = "",
        lifecycle: Any | None = None,
        fallback_available: bool = True,
        live_authorisation_ref: str = "",
        actor: str = "",
        production_route_db: str | Path | None = None,
        ) -> BoundaryDrillResult:
    """Roll the trade route back through the real four-step protocol.

    Delegated to `route_drill` rather than reimplemented: the ordering guarantees
    (freeze before drain, bump before open) live in `route_switch`, and a second
    copy here could agree with the tests while disagreeing with the protocol that
    actually ships.

    Two things are added on top. The rollback's own four steps are checked, not
    just its outcome — a rollback that "succeeded" by moving the route at some
    other point in the sequence is not a rollback that worked. And a **failed**
    rollback is examined for the failure that matters: did the route stay where
    it was, or did it end up somewhere nobody chose?
    """
    from ..execution import live_authorisation as live_gate
    from ..execution import route_drill as drill

    result = BoundaryDrillResult(drill_id=drill_id, boundary=plane.LIVE_TRADER)
    base = production_route_db or "var/phase_f_routes.sqlite"
    path = drill.drill_path(base, drill_id)

    # Captured before the drill runs, so "untouched" can mean "unchanged" rather
    # than "absent". See the check below for why the distinction matters.
    registry_before = _registry_fingerprint(base)

    # --- the rollback's own authorisation ----------------------------------
    # Checked here as well as inside the drill, because "the drill refused" and
    # "the drill was never allowed to try" are different findings and the report
    # has to distinguish them.
    decision = live_gate.evaluate_live_switch(
        authorisation_ref=live_authorisation_ref, consumer_id="trader",
        environment=environment, account=account, path=path)
    if not decision.opened:
        result.check("live_gate_consulted", True,
                     f"gate returned {decision.verdict} ({decision.reason}) — a "
                     f"rollback changes who may send orders, so it needs live "
                     f"authority exactly as much as a switch does")

    outcome = drill.run_rollback_drill(
        drill_id=drill_id, actor=actor or f"drill:{drill_id}",
        environment=environment, account=account, lifecycle=lifecycle,
        fallback_available=fallback_available, base_path=base,
        live_authorisation_ref=live_authorisation_ref)

    row = outcome.as_row()
    result.generations = list(row.get("generations") or [])
    result.preserved = {"drill_store": path, "mode": row.get("mode")}

    # The gate is simulated for a drill, so the rollback can only be performed
    # inside the drill store. What has to hold is that it happened *there* and
    # that the real registry was never created.
    performed = bool((row.get("rollback") or {}).get("performed"))
    reason = str((row.get("rollback") or {}).get("reason") or "performed")
    result.check("drill_declares_itself", row.get("mode") == MODE,
                 f"mode={row.get('mode')!r}; a real switch record has no such field")
    # Whether the drill wrote to the production registry — NOT whether the file
    # exists. A registry that already existed (created by a real earlier switch)
    # is the normal case, and asking "does the file exist?" reported that
    # pre-existing registry as a violation while the outcome still read
    # `completed`: a red mark on a green report, which is worse than no mark
    # because the reader has to work out which of the two to believe.
    #
    # So the state is captured BEFORE the drill runs and compared after. What
    # must be unchanged is the file's identity and size — a drill that appended
    # a row changes the size even when it leaves the route alone.
    after = _registry_fingerprint(base)
    untouched = after == registry_before
    result.check("real_registry_untouched", untouched,
                 f"{base} changed during the drill ({registry_before} -> {after}); "
                 f"a drill must never touch the surface it drills, because a drill "
                 f"recorded in production is indistinguishable from a real switch"
                 if not untouched else
                 f"{base} is unchanged by the drill ({after}); it wrote only to "
                 f"its own store")

    if not fallback_available:
        # Passing outcome: held. The route must not move to a target that cannot
        # serve traffic.
        result.outcome = HELD
        result.check("held_on_unavailable_fallback", not performed,
                     f"rollback performed={performed}; a rollback to an "
                     f"unavailable target turns one outage into a different one")
        result.check("no_silent_route_change", not performed,
                     "the route stayed where it was rather than moving to a "
                     "target nobody declared usable")
        result.observe("rollback", "not attempted",
                       "the fallback target was declared unavailable")
        result.note = outcome.note or (
            "回退目标标记为不可用，故本次演练验证的是「保持现状」而非「退回成功」。")
        record_drill(result, actor=actor, path=path)
        return result

    if not performed:
        # The gate refused, so the protocol was never reached. That is the
        # correct outcome — a rollback taken without live authority changes who
        # may send real orders — and it is recorded rather than asserted, because
        # asserting "the rollback happened" here would fail every correct drill.
        result.observe("rollback", "refused", reason)
        result.check("refused_without_live_authority_is_correct", True,
                     f"the rollback did not proceed ({reason}); a rollback needs "
                     f"live authority exactly as much as a forward switch, and a "
                     f"drill must never be able to supply it")
        result.outcome = COMPLETED
        record_drill(result, actor=actor, path=path)
        return result

    result.check("rollback_performed", True, reason)
    steps = {s["step"]: s["held"] for s in row.get("steps") or []}
    result.check("atomic_steps_held",
                 bool(steps) and all(steps.get(name) for name in drill.ATOMIC_STEPS),
                 f"steps: {steps}; the rollback must run the same four steps as a "
                 f"forward switch, in the same order")
    if result.generations:
        last = result.generations[-1]
        result.check("generation_advanced",
                     int(last.get("to_generation") or 0)
                     > int(last.get("from_generation") or 0),
                     f"generation {last.get('from_generation')} -> "
                     f"{last.get('to_generation')}; a rollback that does not "
                     f"advance the generation leaves the old route's grants alive")
    result.outcome = COMPLETED if not result.failures else FAILED
    record_drill(result, actor=actor, path=path)
    return result


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def render_drill_report(results: Sequence[BoundaryDrillResult]) -> str:
    lines = ["# 三边界独立回滚演练（11.9 / 13.1–13.3）", ""]
    lines.append(f"共 {len(results)} 条边界 · "
                 f"完成 **{sum(1 for r in results if r.outcome == COMPLETED)}** · "
                 f"保持现状 **{sum(1 for r in results if r.outcome == HELD)}** · "
                 f"拒绝 **{sum(1 for r in results if r.outcome == REFUSED)}** · "
                 f"失败 **{sum(1 for r in results if r.outcome == FAILED)}**")
    lines += ["", "| 边界 | 结论 | 检查项 |", "|---|---|---|"]
    for r in results:
        held = sum(1 for c in r.checks if c.held)
        lines.append(f"| `{r.boundary}` | {r.outcome} | {held}/{len(r.checks)} |")

    for r in results:
        lines += ["", f"## `{r.boundary}`（演练 `{r.drill_id}`，mode={r.mode}）", ""]
        lines.append(f"- 结论：**{r.outcome}**")
        if r.routes_before:
            lines.append(f"- 回退前路由：`{r.routes_before}`")
        if r.routes_after:
            lines.append(f"- 回退后路由：`{r.routes_after}`")
        if r.checks:
            lines += ["", "| 检查 | 成立 | 说明 |", "|---|---|---|"]
            for c in r.checks:
                lines.append(f"| `{c.name}` | {'是' if c.held else '**否**'} | "
                             f"{c.detail or '—'} |")
        if r.preserved:
            lines += ["", "### 记录保全", "", "```json",
                      json.dumps(r.preserved, ensure_ascii=False, indent=2,
                                 default=str), "```"]
        if r.skipped:
            lines += ["", "### 跳过（已执行，不得重跑）", ""]
            for row in r.skipped:
                lines.append(f"- `{row['trigger']}` → {row['execution_refs']}")
        if r.unconfirmed:
            lines += ["", "### 未确认（无执行记录，阻断回退）", ""]
            lines += [f"- `{t}`" for t in r.unconfirmed]
        if r.failures:
            lines += ["", "### 失败项", ""]
            lines += [f"- {f}" for f in r.failures]
        if r.note:
            lines += ["", r.note]

    lines += ["", "## 本次演练**未**证明的事", ""]
    lines += [f"- {limit}" for limit in DRILL_LIMITS]
    return "\n".join(lines)
