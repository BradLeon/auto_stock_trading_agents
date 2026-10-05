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
