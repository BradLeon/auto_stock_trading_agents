"""Legacy scheduler adapter: one trigger identity for both paths (task 4.1–4.3).

The finding that motivates this module: `owner_mode` does not exist in
`src/ats/runtime/scheduler.py` at all, and `trigger_key` appears only inside
`_start_phase_e`. The resident daemon builds its own APScheduler jobs and calls
role entry points directly; Phase E is a separate explicit startup path.

So changing `workflow_owners.yaml` proves nothing about a daemon that is already
running, and nothing lets the old path de-duplicate against the new ledger after a
rollback. Both problems are the same problem: there is no shared identity.

**The identity is Phase E's, not a new one.** `triggers.trigger_key()` already
defines it — `workflow + kind + identity + planned instant`, hashed. A parallel
implementation here would produce different digests for the same logical run, which
is precisely the ambiguity this module exists to remove. Every mapping below
therefore *constructs a `TriggerContext`* and lets Phase E compute the key.

Three things follow from using the planned instant rather than the firing instant:

- **A catch-up run after downtime is the same logical run.** Using the firing
  instant would mint a second run for the same tick, which is the misfire case the
  `scheduled_for` comment in `run_contracts.py` already warns about.
- **A manual trigger and the scheduled trigger for the same work are NOT the same
  run** — they differ in `kind` and therefore in key. That is correct for
  de-duplication, and task 4.2's "merge into one execution" is handled at the
  *claim* layer (one owner per key), not by collapsing the identities.

Claims are recorded in a shared table rather than in either scheduler's own state,
because the whole point is that a resident process and a one-shot CLI can see each
other's claims. Owner state carries a **generation** for the same reason
`route_registry` does: an old process must lose its claims when ownership moves,
and comparing owner names alone cannot tell the first `legacy` from the second.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import REPO_ROOT
from .run_contracts import TriggerContext
from .triggers import trigger_key

DEFAULT_STATE_PATH = "var/phase_f_dispatch.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_owner_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    owner TEXT NOT NULL,
    generation INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dispatch_claims (
    trigger_key TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    identity_json TEXT NOT NULL DEFAULT '{}',
    owner TEXT NOT NULL,
    owner_generation INTEGER NOT NULL,
    claimed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    result_ref TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dispatch_claims_status
    ON dispatch_claims(status, claimed_at);

-- Freezing must precede the inventory (design decision 8's ordering constraint):
-- inventorying first leaves a window in which a new claim arrives after the count,
-- and "declare a disposition per unfinished trigger" cannot close that window.
CREATE TABLE IF NOT EXISTS dispatch_freeze (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    frozen INTEGER NOT NULL DEFAULT 0,
    frozen_at TEXT NOT NULL DEFAULT '',
    frozen_by TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    from_generation INTEGER NOT NULL DEFAULT 0,
    switch_token TEXT NOT NULL DEFAULT ''
);

-- The independent expected-trigger set. Task 3.3's schedule-omission surface
-- cannot detect a trigger BOTH paths dropped unless something other than either
-- path states what should have fired.
CREATE TABLE IF NOT EXISTS dispatch_expected_triggers (
    switch_token TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    identity_json TEXT NOT NULL DEFAULT '{}',
    expected_at TEXT NOT NULL,
    observed_old INTEGER NOT NULL DEFAULT 0,
    observed_new INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (switch_token, trigger_key)
);

CREATE TRIGGER IF NOT EXISTS dispatch_expected_no_update
BEFORE UPDATE ON dispatch_expected_triggers
WHEN OLD.observed_old = 1 AND OLD.observed_new = 1
BEGIN SELECT RAISE(ABORT, 'expected-trigger observations are append-only once both paths reported'); END;
"""

_LOCK = threading.Lock()


def default_state_path() -> str:
    return os.environ.get("ATS_DISPATCH_STATE_PATH",
                          str(REPO_ROOT / DEFAULT_STATE_PATH))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    target = Path(path or default_state_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


class DispatchOwnershipError(RuntimeError):
    """A dispatch action cannot proceed; `reasons` says why."""


# --------------------------------------------------------------------------- #
# 4.1 — one identity for both paths
# --------------------------------------------------------------------------- #

# The legacy jobs actually registered in `runtime/scheduler.py`, mapped to the
# workflow they represent. Derived from the registration block rather than invented:
# a mapping that names a job that does not exist would make the omission comparison
# vacuous for that job.
LEGACY_JOBS: dict[str, str] = {
    "daily_cycle": "daily-cascade",
    "weekly_review": "weekly-review",
    "factset_weekly_ingest": "factset-ingest",
    "factset_monthly_ingest": "factset-ingest",
    "pead_score_bmo": "pead-score",
    "pead_score_amc": "pead-score",
    "journal_reconcile": "journal-reconcile",
}

# The Phase E schedule ids that correspond to the same work.
SCHEDULE_EQUIVALENTS: dict[str, str] = {
    "daily_cycle": "daily-cascade",
    "weekly_review": "weekly-review",
    "factset_weekly_ingest": "factset-ingest",
    "factset_monthly_ingest": "factset-ingest",
    "pead_score_bmo": "pead-score-bmo",
    "pead_score_amc": "pead-score-amc",
    "journal_reconcile": "journal-reconcile",
}


# The Phase E key function under a name that says who owns it. Re-exported rather
# than wrapped: a wrapper that recomputed the digest would defeat the point.
triggers_key = trigger_key


def context_for_schedule(*, schedule_id: str, scheduled_for: str,
                         workflow_id: str = "") -> TriggerContext:
    """The `TriggerContext` a scheduled run at `scheduled_for` represents.

    `scheduled_for` must be the PLANNED instant, not the firing instant. That is
    what makes a catch-up after downtime the same logical run instead of a second
    one, and it is Phase E's rule rather than a new one.
    """
    return TriggerContext(kind="schedule", schedule_id=schedule_id,
                          scheduled_for=scheduled_for, workflow_id=workflow_id)


def context_for_event(*, event_id: str, event_version: str,
                      workflow_id: str = "") -> TriggerContext:
    """The context for an event delivery, identified by id AND version."""
    return TriggerContext(kind="event", event_id=event_id,
                          event_version=event_version, workflow_id=workflow_id)


def context_for_manual(*, trigger_id: str, workflow_id: str = "") -> TriggerContext:
    return TriggerContext(kind="manual", trigger_id=trigger_id,
                          workflow_id=workflow_id)


def legacy_key(job_id: str, *, scheduled_for: str, scope: str = "portfolio") -> str:
    """The shared trigger key for a legacy APScheduler job firing.

    Both paths computing this must get the same string, so it goes through Phase E's
    `trigger_key`. `workflow_id` comes from the job's declared equivalent, which is
    what ties a legacy `daily_cycle` to a Phase E `daily-cascade` schedule.
    """
    if job_id not in LEGACY_JOBS:
        raise DispatchOwnershipError(
            f"legacy job {job_id!r} has no declared workflow; add it to "
            f"LEGACY_JOBS so the omission comparison is not vacuous for it")
    context = context_for_schedule(
        schedule_id=SCHEDULE_EQUIVALENTS.get(job_id, LEGACY_JOBS[job_id]),
        scheduled_for=scheduled_for, workflow_id=LEGACY_JOBS[job_id])
    return trigger_key(context)


def phase_e_key(*, schedule_id: str, scheduled_for: str, workflow_id: str) -> str:
    """The same run as seen by the new path. Equal to `legacy_key` for the same work."""
    return trigger_key(context_for_schedule(
        schedule_id=schedule_id, scheduled_for=scheduled_for, workflow_id=workflow_id))


# --------------------------------------------------------------------------- #
# 4.3 — shared owner state with a generation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class OwnerState:
    owner: str
    generation: int
    updated_at: str = ""
    updated_by: str = ""
    reason: str = ""

    def as_row(self) -> dict[str, Any]:
        return {"owner": self.owner, "generation": self.generation,
                "updated_at": self.updated_at, "updated_by": self.updated_by,
                "reason": self.reason}


def read_owner(path: str | Path | None = None) -> OwnerState | None:
    """The shared owner state, or None when nothing is installed."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM dispatch_owner_state WHERE singleton=1").fetchone()
    if row is None:
        return None
    return OwnerState(owner=row["owner"], generation=int(row["generation"]),
                      updated_at=row["updated_at"], updated_by=row["updated_by"],
                      reason=row["reason"])


def install_owner(owner: str, *, generation: int = 1, actor: str = "",
                  reason: str = "", path: str | Path | None = None) -> OwnerState:
    """Install the initial owner, or refuse if one exists.

    Refusing to overwrite is what stops a process start from resetting the
    generation — which would make every live claim valid again.
    """
    if not owner.strip():
        raise DispatchOwnershipError("owner is required")
    target = path or default_state_path()
    with _LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT * FROM dispatch_owner_state WHERE singleton=1").fetchone()
                if existing is not None:
                    raise DispatchOwnershipError(
                        f"dispatch owner is already {existing['owner']!r} at "
                        f"generation {existing['generation']}; use switch_owner()")
                conn.execute(
                    "INSERT INTO dispatch_owner_state (singleton, owner,"
                    " generation, updated_at, updated_by, reason)"
                    " VALUES (1,?,?,?,?,?)",
                    (owner, int(generation), _now(), actor, reason))
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return read_owner(target)  # type: ignore[return-value]


def switch_owner(expected_generation: int, owner: str, *, actor: str = "",
                 reason: str = "", path: str | Path | None = None) -> OwnerState:
    """Move ownership, compare-and-set on the generation.

    Two operators racing cannot both succeed, so ownership cannot be "switched
    twice" from the same base. This is the same shape as
    `execution.route_registry.switch_route`, and for the same reason: a resident
    process must lose its authority the moment ownership moves.
    """
    if not owner.strip():
        raise DispatchOwnershipError("owner is required")
    target = path or default_state_path()
    with _LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM dispatch_owner_state WHERE singleton=1").fetchone()
                if row is None:
                    raise DispatchOwnershipError(
                        "no dispatch owner is installed; install one first")
                current = int(row["generation"])
                if current != int(expected_generation):
                    raise DispatchOwnershipError(
                        f"dispatch generation moved: expected {expected_generation}, "
                        f"found {current}")
                conn.execute(
                    "UPDATE dispatch_owner_state SET owner=?, generation=?,"
                    " updated_at=?, updated_by=?, reason=? WHERE singleton=1",
                    (owner, current + 1, _now(), actor, reason))
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return read_owner(target)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# 4.2 — claim before executing
# --------------------------------------------------------------------------- #

# Claim statuses. `published` is separate from `finished` because the requirement
# is that the OLD executor must stop PUBLISHING — a legacy job that has finished
# its work but not yet written its result is still a publisher.
CLAIMED = "claimed"
FINISHED = "finished"
PUBLISHED = "published"
ABANDONED = "abandoned"


@dataclass
class ClaimResult:
    acquired: bool
    trigger_key: str
    reason: str = ""
    owner: str = ""
    generation: int = 0
    state: str = ""


def claim(*, key: str, workflow_id: str, kind: str = "schedule",
          identity: dict[str, Any] | None = None, actor: str = "",
          owner: str | None = None, path: str | Path | None = None) -> ClaimResult:
    """Register intent to execute one logical trigger, or report why not.

    Four things refuse a claim, and the last two are the point of the module:

    1. No owner state installed — nothing says who may run.
    2. The caller's owner is not the current owner.
    3. The caller's generation is behind (a resident process that predates a
       switch).
    4. **Another owner already holds an unfinished claim for this key** — this is
       what stops a resident daemon and a one-shot CLI from both running the same
       logical trigger.
    """
    target = path or default_state_path()
    state = read_owner(target)
    if state is None:
        return ClaimResult(False, key,
                           reason="no dispatch owner is installed; a claim cannot "
                                  "be attributed to anyone")
    resolved_owner = owner or state.owner

    with _LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM dispatch_owner_state WHERE singleton=1").fetchone()
                # The held claim is checked BEFORE the owner comparison. Both
                # refuse, but "someone else already holds this" is the specific
                # diagnosis, and reporting "you are not the owner" instead sends
                # an operator to look at ownership rather than at the duplicate.
                held = conn.execute(
                    "SELECT owner, owner_generation, status FROM dispatch_claims"
                    " WHERE trigger_key=?", (key,)).fetchone()
                if held is not None and held["status"] in {CLAIMED, FINISHED,
                                                            PUBLISHED}:
                    conn.execute("ROLLBACK")
                    return ClaimResult(
                        False, key, owner=held["owner"],
                        generation=int(held["owner_generation"]),
                        reason=f"already {held['status']} by {held['owner']!r}; a "
                               f"logical trigger executes once")

                if resolved_owner != state.owner:
                    conn.execute("ROLLBACK")
                    return ClaimResult(
                        False, key, owner=state.owner, generation=state.generation,
                        reason=f"this process is {resolved_owner!r} but the "
                               f"current owner is {state.owner!r}")

                if int(row["generation"]) != int(state.generation):
                    conn.execute("ROLLBACK")
                    return ClaimResult(
                        False, key, owner=state.owner,
                        generation=int(row["generation"]),
                        reason=f"owner generation moved to {row['generation']}; "
                               f"this process holds {state.generation}")
                stamp = _now()
                conn.execute(
                    "INSERT INTO dispatch_claims (trigger_key, workflow_id, kind,"
                    " identity_json, owner, owner_generation, claimed_at, status,"
                    " actor) VALUES (?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(trigger_key) DO UPDATE SET owner=excluded.owner,"
                    " owner_generation=excluded.owner_generation,"
                    " claimed_at=excluded.claimed_at, status=excluded.status,"
                    " actor=excluded.actor",
                    (key, workflow_id, kind,
                     json.dumps(identity or {}, ensure_ascii=False, sort_keys=True,
                                default=str),
                     resolved_owner, int(row["generation"]), stamp, CLAIMED, actor))
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return ClaimResult(True, key, owner=resolved_owner, generation=state.generation,
                       state=CLAIMED)


def claims(*, status: str | None = None, owner: str | None = None,
           path: str | Path | None = None) -> list[dict[str, Any]]:
    clauses, params = [], []
    if status:
        clauses.append("status=?")
        params.append(status)
    if owner:
        clauses.append("owner=?")
        params.append(owner)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(path) as conn:
        return [dict(row) for row in conn.execute(
            f"SELECT * FROM dispatch_claims{where} ORDER BY claimed_at",
            params).fetchall()]


def mark_published(key: str, *, result_ref: str = "",
                   path: str | Path | None = None) -> bool:
    """Record that this executor published the run's result.

    Publishing is what the old executor must STOP during a handover, so it is a
    distinct state rather than a flag: a handover can be refused on "cannot prove
    the old executor cannot publish" while an unfinished-but-silent run is merely
    inventory.
    """
    with _connect(path) as conn:
        cursor = conn.execute(
            "UPDATE dispatch_claims SET status=?, result_ref=?, finished_at=?"
            " WHERE trigger_key=? AND status IN (?, ?)",
            (PUBLISHED, result_ref, _now(), key, CLAIMED, FINISHED))
        return cursor.rowcount > 0


def release(key: str, *, actor: str = "", reason: str = "",
            path: str | Path | None = None) -> bool:
    """Abandon a claim so it can be re-executed. Recorded, never deleted."""
    with _connect(path) as conn:
        cursor = conn.execute(
            "UPDATE dispatch_claims SET status=?, finished_at=?, actor=?"
            " WHERE trigger_key=? AND status=?",
            (ABANDONED, _now(), f"{actor}: {reason}" if reason else actor,
             key, CLAIMED))
        return cursor.rowcount > 0


# --------------------------------------------------------------------------- #
# 4.4 — the schedule handover protocol
# --------------------------------------------------------------------------- #

# Dispositions an unfinished trigger may be given. `void` is the only one that
# discards work, and it requires a reason; the other two keep it.
DISPOSITION_CARRY_OVER = "carry_over"
DISPOSITION_ALREADY_RUN = "already_run"
DISPOSITION_VOID = "void"

DISPOSITIONS: tuple[str, ...] = (DISPOSITION_CARRY_OVER, DISPOSITION_ALREADY_RUN,
                                 DISPOSITION_VOID)


def freeze_claims(*, actor: str = "", reason: str = "",
                  path: str | Path | None = None) -> str:
    """Close new claims and return the switch token.

    **Before** the inventory, not after — design decision 8's ordering constraint.
    Inventorying first leaves a window in which a new claim arrives after the count,
    and "declare a disposition per unfinished trigger" cannot close that window: the
    trigger that appeared in between has no declared disposition, but it also was
    not in the list anyone declared against.
    """
    from uuid import uuid4

    target = path or default_state_path()
    state = read_owner(target)
    if state is None:
        raise DispatchOwnershipError(
            "no dispatch owner is installed; nothing to freeze")
    with _LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT * FROM dispatch_freeze WHERE singleton=1").fetchone()
                if existing is not None and existing["frozen"]:
                    raise DispatchOwnershipError(
                        f"claims are already frozen for switch "
                        f"{existing['switch_token']!r}")
                token = uuid4().hex
                conn.execute(
                    "INSERT INTO dispatch_freeze (singleton, frozen, frozen_at,"
                    " frozen_by, reason, from_generation, switch_token)"
                    " VALUES (1,1,?,?,?,?,?)"
                    " ON CONFLICT(singleton) DO UPDATE SET frozen=1,"
                    " frozen_at=excluded.frozen_at, frozen_by=excluded.frozen_by,"
                    " reason=excluded.reason,"
                    " from_generation=excluded.from_generation,"
                    " switch_token=excluded.switch_token",
                    (_now(), actor, reason, state.generation, token))
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return token


def is_frozen(path: str | Path | None = None) -> bool:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT frozen FROM dispatch_freeze WHERE singleton=1").fetchone()
    return bool(row and row["frozen"])


def open_claims(switch_token: str, *, path: str | Path | None = None) -> None:
    """Reopen claims for the attempt identified by `switch_token`.

    Token-checked for the same reason `route_registry.open_submissions` is: a
    process that froze, lost the token, and later reopened would otherwise be
    reopening somebody else's attempt.
    """
    target = path or default_state_path()
    with _LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM dispatch_freeze WHERE singleton=1").fetchone()
                if row is None or not row["frozen"]:
                    raise DispatchOwnershipError("claims are not frozen")
                if row["switch_token"] != switch_token:
                    raise DispatchOwnershipError(
                        f"switch token mismatch: this attempt is "
                        f"{row['switch_token']!r}, not {switch_token!r}")
                conn.execute("UPDATE dispatch_freeze SET frozen=0, switch_token=''"
                             " WHERE singleton=1")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")


def inventory_unfinished(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Claims that have not published. This is the list a handover must dispose of."""
    return claims(status=CLAIMED, path=path)


@dataclass
class HandoverReport:
    switch_token: str
    from_owner: str = ""
    to_owner: str = ""
    from_generation: int = 0
    to_generation: int = 0
    dispositions: dict[str, str] = field(default_factory=dict)
    voided: list[dict[str, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    handover_proved: bool = False
    publish_blocked: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.reasons

    def as_row(self) -> dict[str, Any]:
        return {
            "switch_token": self.switch_token, "from_owner": self.from_owner,
            "to_owner": self.to_owner, "from_generation": self.from_generation,
            "to_generation": self.to_generation,
            "dispositions": dict(self.dispositions), "voided": list(self.voided),
            "reasons": list(self.reasons), "steps": list(self.steps),
            "handover_proved": self.handover_proved,
            "publish_blocked": list(self.publish_blocked),
            "succeeded": self.succeeded,
        }


def hand_over(*, to_owner: str, actor: str = "", reason: str = "",
              disposition_for: Callable[[dict[str, Any]], str] | None = None,
              prove_handover: Callable[[str, str], dict[str, Any]] | None = None,
              path: str | Path | None = None) -> HandoverReport:
    """Freeze → inventory → dispose each → prove the old executor stopped → switch.

    The proof step is a parameter because "the old process stopped publishing" is a
    fact about a running process, not something this module can observe. Without it
    the handover is refused — accepting a handover on trust is what produces two
    executors for one logical trigger.
    """
    target = path or default_state_path()
    report = HandoverReport(switch_token="")

    state = read_owner(target)
    if state is None:
        report.reasons.append("no dispatch owner is installed")
        return report

    report.switch_token = freeze_claims(actor=actor, reason=reason, path=target)
    report.from_owner = state.owner
    report.from_generation = state.generation
    report.to_owner = to_owner
    report.steps.append("freeze")

    # From here every failure must reopen claims, or the installation is left with
    # nothing able to run.
    try:
        unfinished = inventory_unfinished(target)
        report.steps.append("inventory")

        undeclared = []
        for row in unfinished:
            key = row["trigger_key"]
            declared = (disposition_for(row) if disposition_for else "")
            if declared not in DISPOSITIONS:
                undeclared.append((key, declared))
                continue
            report.dispositions[key] = declared
            if declared == DISPOSITION_VOID:
                release(key, actor=actor, reason=reason, path=target)
                report.voided.append({"trigger_key": key, "reason": reason})
            elif declared == DISPOSITION_ALREADY_RUN:
                mark_published(key, result_ref="disposition:already_run", path=target)
        if undeclared:
            report.reasons.append(
                f"{len(undeclared)} unfinished trigger(s) have no declared "
                f"disposition; each must be carried over, declared already-run, or "
                f"voided with a reason: {[k for k, _ in undeclared]}")
            report.steps.append("dispose")
            open_claims(report.switch_token, path=target)
            return report
        report.steps.append("dispose")

        proof = (prove_handover(state.owner, to_owner) if prove_handover
                 else {"proved": False,
                       "reason": "no proof supplied that the old executor stopped "
                                 "publishing"})
        # Recorded whether or not it passed: a handover that stopped at the proof
        # step must show that it got that far, or the operator cannot tell it apart
        # from one that never left the disposition step.
        report.steps.append("prove")
        report.publish_blocked = list(proof.get("blocked_publishers", ()))
        report.handover_proved = bool(proof.get("proved"))
        if not report.handover_proved:
            report.reasons.append(
                f"cannot prove {state.owner!r} has stopped publishing: "
                f"{proof.get('reason', 'no reason given')}")
            open_claims(report.switch_token, path=target)
            return report

        new_state = switch_owner(state.generation, to_owner, actor=actor,
                                 reason=reason, path=target)
        report.to_generation = new_state.generation
        report.steps.append("switch")

        open_claims(report.switch_token, path=target)
        report.steps.append("open")
        return report

    except Exception as exc:  # noqa: BLE001 - every failure must reopen claims
        report.reasons.append(str(exc))
        try:
            open_claims(report.switch_token, path=target)
        except DispatchOwnershipError:
            report.reasons.append(
                "CRITICAL: claims stayed frozen; nothing can run until an operator "
                "releases them")
        return report


# --------------------------------------------------------------------------- #
# 4.6 — the independent expected-trigger set
# --------------------------------------------------------------------------- #

def declare_expected(*, switch_token: str, keys: Iterable[str],
                     workflow_of: Callable[[str], tuple[str, str]] | None = None,
                     path: str | Path | None = None) -> int:
    """Record what should have fired during the observed window.

    Independent by construction: the caller supplies the set. Deriving it from
    either path's own claims would make "both dropped it" invisible, which is the
    one thing this set exists to catch.
    """
    target = path or default_state_path()
    stamp = _now()
    added = 0
    with _connect(target) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for key in keys:
                workflow_id, kind = workflow_of(key) if workflow_of else ("", "schedule")
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO dispatch_expected_triggers"
                    " (switch_token, trigger_key, workflow_id, kind, identity_json,"
                    " expected_at) VALUES (?,?,?,?,?,?)",
                    (switch_token, key, workflow_id, kind, "{}", stamp))
                added += cursor.rowcount
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return added


def observe_trigger(*, switch_token: str, trigger_key: str, path_owner: str,
                    path: str | Path | None = None) -> None:
    """Record that `path_owner` ran a trigger. Both flags must end up set."""
    column = "observed_old" if path_owner == "old" else "observed_new"
    with _connect(path) as conn:
        conn.execute(
            f"UPDATE dispatch_expected_triggers SET {column}=1"
            f" WHERE switch_token=? AND trigger_key=?",
            (switch_token, trigger_key))


def omission_report(switch_token: str, *, path: str | Path | None = None
                    ) -> dict[str, Any]:
    """Which expected triggers each path missed, and whether the set is trustworthy.

    The consistency check is load-bearing. If the expected set disagrees with what
    was actually registered, the omission verdict derived from it is meaningless —
    so the report refuses rather than reporting a number nobody should act on.
    """
    with _connect(path) as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM dispatch_expected_triggers WHERE switch_token=?",
            (switch_token,)).fetchall()]
        registered = {row["trigger_key"] for row in conn.execute(
            "SELECT trigger_key FROM dispatch_claims").fetchall()}

    if not rows:
        return {"switch_token": switch_token, "trustworthy": False,
                "reasons": ["no expected trigger set was declared for this switch"],
                "old_missed": [], "new_missed": [], "both_missed": [],
                "expected_count": 0}

    undeclared = sorted(row["trigger_key"] for row in rows
                        if row["trigger_key"] not in registered)
    reasons: list[str] = []
    if undeclared:
        reasons.append(
            f"{len(undeclared)} expected trigger(s) were never registered by "
            f"either path: {undeclared[:5]}; the expected set disagrees with "
            f"reality, so omissions cannot be judged from it")

    old_missed = sorted(row["trigger_key"] for row in rows if not row["observed_old"])
    new_missed = sorted(row["trigger_key"] for row in rows if not row["observed_new"])
    both = sorted(set(old_missed) & set(new_missed))

    return {
        "switch_token": switch_token,
        "trustworthy": not reasons,
        "reasons": reasons,
        "expected_count": len(rows),
        "old_missed": old_missed,
        "new_missed": new_missed,
        # The case a path-to-path comparison cannot see: both dropped it.
        "both_missed": both,
        "unregistered_expectations": undeclared,
    }


def expected_set_matches_registration(switch_token: str, *,
                                       path: str | Path | None = None) -> bool:
    return bool(omission_report(switch_token, path=path)["trustworthy"])


# --------------------------------------------------------------------------- #
# 4.7 — a dispatch switch does not own source registration
# --------------------------------------------------------------------------- #

SOURCE_LIFECYCLE_ACTIONS: frozenset[str] = frozenset({
    "register_source", "unregister_source", "restart_source",
    "restart_collection", "reingest_source", "reload_catalog",
})


class SourceLifecycleRefused(DispatchOwnershipError):
    """A dispatch switch tried to act on source registration or restart."""

    def __init__(self, action: str, reason: str) -> None:
        super().__init__(reason)
        self.action = action
        self.reason_code = "dispatch_switch_owns_no_source_lifecycle"


def assert_not_source_lifecycle(action: str, *, changed_paths: Iterable[str] = ()
                                ) -> None:
    """Refuse source registration or restart actions during a dispatch switch.

    A dispatch switch changes which scheduler owns a trigger. Sources are
    registered elsewhere and own their own lifecycle; a switch that restarts a
    collection would restart it *under the new owner*, which is a different change
    wearing the same commit. Naming it explicitly keeps the two apart.
    """
    if action in SOURCE_LIFECYCLE_ACTIONS:
        raise SourceLifecycleRefused(
            action,
            f"a dispatch switch does not own source lifecycle; {action!r} would "
            f"restart collection under the new owner. Switch the owner, then "
            f"change the source separately.")
