"""Active trade route and its generation (Phase F task 2.1).

Phase F has to make "exactly one route can submit real orders" true across
processes. A capability held in memory cannot: the resident scheduler and a
one-shot CLI are separate processes, so a process that was handed a capability
before a cutover keeps it afterwards, and no amount of config validation reaches
it. The authority therefore has to be state both processes can read — and has to
carry a **monotonic generation**, because comparing route ids alone cannot tell the
first `A` from the second `A` after an A→B→A rollback, so an authorization minted
before the round trip would still look valid.

Why a generation and not a timestamp: timestamps are forgeable and can collide.
`ACTIVE_PHASE` in the architecture guards solved the same problem with a fixed
vocabulary, and this follows that precedent — an integer that only ever increases
under a transaction.

Three properties the rest of Phase F depends on:

- **Fail closed on unreadable state.** If the registry cannot be read, submission is
  refused. Degrading to "probably fine" is the one behaviour that would make the
  guarantee decorative.
- **Additive schema, no destructive migration.** The table is created if absent;
  an existing installation keeps working and simply starts recording generations.
- **Not a fingerprint path.** It lives under `execution/`, which the qualification
  policy does not bind (see `workflow.assurance_surface`), so recording a
  generation does not invalidate any consumer's evidence. The authorization
  module it does bind is touched separately (task 2.5), and before Phase F takes
  evidence (group 6).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import REPO_ROOT

DEFAULT_PATH = "var/phase_f_routes.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_route_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    route_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    environment TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS trade_route_history (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    from_route TEXT NOT NULL,
    to_route TEXT NOT NULL,
    from_generation INTEGER NOT NULL,
    to_generation INTEGER NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT ''
);
-- Freeze is a separate table, not columns on the state row: it is written BEFORE
-- the generation bump and read by processes that must refuse submissions while a
-- cutover is in flight, including processes that never saw the pre-cutover state.
CREATE TABLE IF NOT EXISTS trade_route_freeze (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    frozen INTEGER NOT NULL DEFAULT 0,
    frozen_at TEXT NOT NULL DEFAULT '',
    frozen_by TEXT NOT NULL DEFAULT '',
    freeze_reason TEXT NOT NULL DEFAULT '',
    -- The issuance counter at the moment of freezing. A switch is void if the
    -- counter moved past this, because an authorization minted after the freeze
    -- was not part of what the drain check inspected.
    issuance_at_freeze INTEGER NOT NULL DEFAULT 0,
    from_generation INTEGER NOT NULL DEFAULT 0,
    from_route TEXT NOT NULL DEFAULT '',
    switch_token TEXT NOT NULL DEFAULT ''
);
-- Monotonic counter of authorization issuance, bumped by `record_issuance`.
-- Exists so "was anything signed after we froze?" is answerable without
-- re-reading every authorization, and so it survives a process restart.
CREATE TABLE IF NOT EXISTS trade_route_issuance (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    counter INTEGER NOT NULL DEFAULT 0,
    last_at TEXT NOT NULL DEFAULT '',
    last_authorization_id TEXT NOT NULL DEFAULT ''
);
"""

# Guards the read-modify-write of a generation bump. Two processes racing a
# cutover must not both read N and both write N+1 — that would silently grant two
# live routes. The write transaction itself is separate; holding this lock only
# across the compare-and-set is enough and keeps the critical section short.
_BUMP_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_registry_path() -> str:
    return os.environ.get("ATS_ROUTE_REGISTRY_PATH", str(REPO_ROOT / DEFAULT_PATH))


class RouteRegistryError(RuntimeError):
    """The route registry is unreadable, absent where it must be, or inconsistent."""


class RouteGenerationConflict(RouteRegistryError):
    """A generation bump raced another bump and lost."""


@dataclass(frozen=True)
class RouteState:
    """The authoritative active route. `generation` only ever increases."""

    route_id: str
    generation: int
    environment: str = ""
    account: str = ""
    updated_at: str = ""
    updated_by: str = ""
    reason: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id, "generation": self.generation,
            "environment": self.environment, "account": self.account,
            "updated_at": self.updated_at, "updated_by": self.updated_by,
            "reason": self.reason,
        }

    def same_as(self, other: "RouteState | None") -> bool:
        """Identity for A→B→A purposes: a route id is NOT an identity.

        Two states with the same route id but different generations are different
        authorities, and an authorization bound to the older one must not be
        honoured after a round trip.
        """
        return (other is not None
                and self.route_id == other.route_id
                and self.generation == other.generation)


def _connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


def read_state(path: str | Path | None = None) -> RouteState:
    """The active route, or a fail-closed error when there is none.

    A missing row is an error rather than a default: "no route" is not the same as
    "route A", and defaulting would make a fresh install silently writable.
    """
    target = path or default_registry_path()
    with _connect(target) as conn:
        row = conn.execute(
            "SELECT * FROM trade_route_state WHERE singleton=1").fetchone()
    if row is None:
        raise RouteRegistryError(
            f"no active trade route is registered in {target}; a route must be "
            "installed before any submission can be authorised")
    return RouteState(
        route_id=row["route_id"], generation=int(row["generation"]),
        environment=row["environment"], account=row["account"],
        updated_at=row["updated_at"], updated_by=row["updated_by"],
        reason=row["reason"])


def try_read_state(path: str | Path | None = None) -> RouteState | None:
    """`read_state` without the raise. Absence is None, unreadability still raises."""
    try:
        return read_state(path)
    except RouteRegistryError:
        return None


def install_route(route_id: str, *, generation: int = 1, environment: str = "",
                  account: str = "", actor: str = "", reason: str = "",
                  path: str | Path | None = None) -> RouteState:
    """Create the initial route, or refuse if one already exists.

    Refusing to overwrite is deliberate: re-installing on every process start
    would reset the generation and defeat the whole mechanism.
    """
    if not route_id.strip():
        raise RouteRegistryError("route_id is required")
    if generation < 1:
        raise RouteRegistryError(f"generation must be >= 1, got {generation}")
    target = path or default_registry_path()
    with _connect(target) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT * FROM trade_route_state WHERE singleton=1").fetchone()
            if existing is not None:
                raise RouteRegistryError(
                    f"a trade route is already installed (route="
                    f"{existing['route_id']!r} generation={existing['generation']}); "
                    "use switch_route() to change it")
            stamp = _now()
            conn.execute(
                "INSERT INTO trade_route_state (singleton, route_id, generation,"
                " environment, account, updated_at, updated_by, reason)"
                " VALUES (1,?,?,?,?,?,?,?)",
                (route_id, generation, environment, account, stamp, actor, reason))
            conn.execute(
                "INSERT INTO trade_route_history (at, from_route, to_route,"
                " from_generation, to_generation, actor, reason)"
                " VALUES (?,?,?,?,?,?,?)",
                (stamp, "", route_id, 0, generation, actor, reason))
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return read_state(target)


def switch_route(expected_generation: int, route_id: str, *, environment: str = "",
                 account: str = "", actor: str = "", reason: str = "",
                 path: str | Path | None = None) -> RouteState:
    """Move to `route_id` at `expected_generation + 1`, or fail.

    The caller must state the generation it believes is current. That is the
    compare-and-set that makes concurrent cutovers impossible: two operators racing
    each other cannot both succeed, so a route can never be "switched twice" from
    the same base.
    """
    if not route_id.strip():
        raise RouteRegistryError("route_id is required")
    target = path or default_registry_path()
    with _BUMP_LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM trade_route_state WHERE singleton=1").fetchone()
                if row is None:
                    raise RouteRegistryError(
                        "no active trade route is registered; install one first")
                current = int(row["generation"])
                if current != int(expected_generation):
                    raise RouteGenerationConflict(
                        f"route generation moved: expected "
                        f"{expected_generation}, found {current}")
                stamp = _now()
                conn.execute(
                    "UPDATE trade_route_state SET route_id=?, generation=?,"
                    " environment=?, account=?, updated_at=?, updated_by=?,"
                    " reason=? WHERE singleton=1",
                    (route_id, current + 1, environment, account, stamp,
                     actor, reason))
                conn.execute(
                    "INSERT INTO trade_route_history (at, from_route, to_route,"
                    " from_generation, to_generation, actor, reason)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (stamp, row["route_id"], route_id, current, current + 1,
                     actor, reason))
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return read_state(target)


def history(path: str | Path | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    """Generation history, newest first. Evidence that a cutover happened."""
    target = path or default_registry_path()
    with _connect(target) as conn:
        rows = conn.execute(
            "SELECT * FROM trade_route_history ORDER BY event_id DESC LIMIT ?",
            (int(limit),)).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Freeze (task 2.6, step 1 and step 4)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FreezeState:
    """Whether submissions are currently closed, and to which switch attempt."""

    frozen: bool
    issuance_at_freeze: int = 0
    from_generation: int = 0
    from_route: str = ""
    switch_token: str = ""
    frozen_at: str = ""
    frozen_by: str = ""
    freeze_reason: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "frozen": self.frozen,
            "issuance_at_freeze": self.issuance_at_freeze,
            "from_generation": self.from_generation,
            "from_route": self.from_route,
            "switch_token": self.switch_token,
            "frozen_at": self.frozen_at,
            "frozen_by": self.frozen_by,
            "freeze_reason": self.freeze_reason,
        }


def _empty_freeze() -> FreezeState:
    return FreezeState(frozen=False)


def read_freeze(path: str | Path | None = None) -> FreezeState:
    """The current freeze state. Absence of the row means "not frozen"."""
    return read_freeze_with_counter(path)[0]


def read_freeze_with_counter(path: str | Path | None = None) -> tuple[FreezeState, int]:
    """`read_freeze` plus the live issuance counter, read in one transaction.

    The two must be read together: reading the freeze and then the counter
    separately leaves a window in which an issuance can land between them and the
    caller concludes "nothing was signed after the freeze" when something was.
    """
    target = path or default_registry_path()
    with _connect(target) as conn:
        row = conn.execute(
            "SELECT * FROM trade_route_freeze WHERE singleton=1").fetchone()
        counter_row = conn.execute(
            "SELECT counter FROM trade_route_issuance WHERE singleton=1").fetchone()
        counter = int(counter_row["counter"]) if counter_row is not None else 0
        if row is None:
            return _empty_freeze(), counter
        return FreezeState(
            frozen=bool(row["frozen"]),
            issuance_at_freeze=int(row["issuance_at_freeze"]),
            from_generation=int(row["from_generation"]),
            from_route=row["from_route"],
            switch_token=row["switch_token"],
            frozen_at=row["frozen_at"],
            frozen_by=row["frozen_by"],
            freeze_reason=row["freeze_reason"],
        ), counter


def issuance_counter(path: str | Path | None = None) -> int:
    """How many authorizations have been signed on this installation."""
    target = path or default_registry_path()
    with _connect(target) as conn:
        row = conn.execute(
            "SELECT counter FROM trade_route_issuance WHERE singleton=1").fetchone()
    return int(row["counter"]) if row is not None else 0


def record_issuance(authorization_id: str, path: str | Path | None = None) -> int:
    """Count one authorization issuance and return the new counter.

    Called by the signer, before it hands the authorization out. Cheap on purpose:
    it is one upsert, because the alternative — scanning authorizations to answer
    "was anything signed after the freeze" — cannot be made atomic with the freeze.

    Idempotent on `authorization_id`: re-binding the same authorization (a resumed
    loop re-validates it) must not inflate the counter, or a cutover would be voided
    by bookkeeping rather than by a real issuance.
    """
    target = path or default_registry_path()
    with _connect(target) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO trade_route_issuance"
                " (singleton, counter, last_at, last_authorization_id)"
                " VALUES (1, 0, '', '')")
            last = conn.execute(
                "SELECT counter, last_authorization_id FROM trade_route_issuance"
                " WHERE singleton=1").fetchone()
            if last["last_authorization_id"] != authorization_id:
                conn.execute(
                    "UPDATE trade_route_issuance SET counter=counter+1, last_at=?,"
                    " last_authorization_id=? WHERE singleton=1",
                    (_now(), authorization_id))
            counter = int(conn.execute(
                "SELECT counter FROM trade_route_issuance WHERE singleton=1"
            ).fetchone()["counter"])
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return int(counter)


def freeze_submissions(*, actor: str = "", reason: str = "",
                       path: str | Path | None = None) -> FreezeState:
    """Close submissions and return the freeze state (step 1 of the protocol).

    Records the issuance counter at the moment of freezing, which is what makes
    step 2's check meaningful: if the counter has moved by the time the drain
    completes, an authorization exists that the drain never inspected.
    """
    target = path or default_registry_path()
    with _BUMP_LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                state = conn.execute(
                    "SELECT * FROM trade_route_state WHERE singleton=1").fetchone()
                if state is None:
                    raise RouteRegistryError(
                        "no active trade route is registered; nothing to freeze")
                existing = conn.execute(
                    "SELECT * FROM trade_route_freeze WHERE singleton=1").fetchone()
                if existing is not None and existing["frozen"]:
                    raise RouteRegistryError(
                        f"submissions are already frozen by "
                        f"{existing['frozen_by']!r} for switch "
                        f"{existing['switch_token']!r}; finish or abort it first")
                conn.execute("INSERT OR IGNORE INTO trade_route_issuance"
                             " (singleton, counter) VALUES (1, 0)")
                counter = int(conn.execute(
                    "SELECT counter FROM trade_route_issuance WHERE singleton=1"
                ).fetchone()["counter"])
                stamp = _now()
                token = uuid4().hex
                conn.execute(
                    "INSERT INTO trade_route_freeze (singleton, frozen, frozen_at,"
                    " frozen_by, freeze_reason, issuance_at_freeze, from_generation,"
                    " from_route, switch_token)"
                    " VALUES (1,1,?,?,?,?,?,?,?)"
                    " ON CONFLICT(singleton) DO UPDATE SET frozen=1, frozen_at=excluded.frozen_at,"
                    " frozen_by=excluded.frozen_by, freeze_reason=excluded.freeze_reason,"
                    " issuance_at_freeze=excluded.issuance_at_freeze,"
                    " from_generation=excluded.from_generation,"
                    " from_route=excluded.from_route,"
                    " switch_token=excluded.switch_token",
                    (stamp, actor, reason, counter, int(state["generation"]),
                     state["route_id"], token))
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return FreezeState(
        frozen=True, issuance_at_freeze=counter, from_generation=int(state["generation"]),
        from_route=state["route_id"], switch_token=token, frozen_at=stamp,
        frozen_by=actor, freeze_reason=reason)


def open_submissions(switch_token: str, *, path: str | Path | None = None) -> FreezeState:
    """Reopen submissions for the switch identified by `switch_token` (step 4).

    Token-checked: a process that froze, lost the token, and later reopened
    something it no longer owns must not be able to reopen the *current* attempt.
    An unknown token is an error rather than a silent success.
    """
    target = path or default_registry_path()
    with _BUMP_LOCK:
        with _connect(target) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM trade_route_freeze WHERE singleton=1").fetchone()
                if row is None or not row["frozen"]:
                    raise RouteRegistryError("submissions are not frozen")
                if row["switch_token"] != switch_token:
                    raise RouteRegistryError(
                        f"switch token mismatch: this attempt is "
                        f"{row['switch_token']!r}, not {switch_token!r}")
                conn.execute(
                    "UPDATE trade_route_freeze SET frozen=0, switch_token=''"
                    " WHERE singleton=1")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
    return FreezeState(frozen=False)


def abort_freeze(switch_token: str, *, actor: str = "", reason: str = "",
                 path: str | Path | None = None) -> FreezeState:
    """Void the attempt and reopen under the SAME generation (steps 2/3 failure).

    Deliberately does not bump the generation: an aborted attempt changed nothing,
    so authorizations signed before it remain valid and no re-approval is needed.
    That is the difference between "aborted" and "rolled back".
    """
    state = open_submissions(switch_token, path=path)
    target = path or default_registry_path()
    with _connect(target) as conn:
        conn.execute(
            "UPDATE trade_route_freeze SET frozen_by=?, freeze_reason=? "
            "WHERE singleton=1", (actor, f"aborted: {reason}" if reason else "aborted"))
    return state
