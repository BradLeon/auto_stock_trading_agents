"""Exact business-scope route authority. Global boundary flags are not routes.

This store is additive to the cutover control database. Qualification, report,
fallback and enforcement adapters receive the same immutable identity. The
production adapters must be supplied by the cutover executor; absence refuses.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import cutover as plane


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _stamp(value: str) -> str:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("business time window must have a timezone")
    return stamp.astimezone(UTC).isoformat()


@dataclass(frozen=True, init=False)
class RouteIdentity:
    domain_id: str
    consumer_id: str
    contract_version: str
    scope_json: str

    def __init__(self, domain_id: str, consumer_id: str, contract_version: str,
                 scope: dict[str, Any]):
        for key, value in (("domain_id", domain_id), ("consumer_id", consumer_id),
                           ("contract_version", contract_version)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} is required")
            object.__setattr__(self, key, value)
        # Copy before normalizing: callers cannot mutate a persisted identity.
        normalized = json.loads(canonical_json(scope))
        if not isinstance(normalized, dict) or not normalized.get("kind") or not normalized.get("id"):
            raise ValueError("business scope requires kind/id")
        entities = normalized.get("entities")
        if not isinstance(entities, list) or not entities or any(
                not isinstance(e, str) or not e.strip() for e in entities):
            raise ValueError("business scope requires explicit entities")
        normalized["entities"] = sorted(set(entities))
        window = normalized.get("time_range")
        if not isinstance(window, dict) or set(window) != {"start", "end"}:
            raise ValueError("business scope requires time_range start/end")
        window["start"], window["end"] = _stamp(window["start"]), _stamp(window["end"])
        if datetime.fromisoformat(window["start"]) > datetime.fromisoformat(window["end"]):
            raise ValueError("business time window is reversed")
        if bool(normalized.get("event_id")) != bool(normalized.get("event_version")):
            raise ValueError("event_id and event_version must be supplied together")
        object.__setattr__(self, "scope_json", canonical_json(normalized))

    @property
    def scope(self) -> dict[str, Any]:
        return json.loads(self.scope_json)

    def as_row(self) -> dict[str, Any]:
        return {"domain_id": self.domain_id, "consumer_id": self.consumer_id,
                "contract_version": self.contract_version, "scope": self.scope}

    @property
    def key(self) -> str:
        return hashlib.sha256(canonical_json(self.as_row()).encode()).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scoped_cutover_routes (
    boundary TEXT NOT NULL, identity_key TEXT NOT NULL, identity_json TEXT NOT NULL,
    route TEXT NOT NULL, generation INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(boundary, identity_key)
);
CREATE TABLE IF NOT EXISTS scoped_cutover_history (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, boundary TEXT NOT NULL,
    identity_key TEXT NOT NULL, identity_json TEXT NOT NULL, from_route TEXT NOT NULL,
    to_route TEXT NOT NULL, generation INTEGER NOT NULL, actor TEXT NOT NULL,
    reason TEXT NOT NULL, evidence_json TEXT NOT NULL, changed_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS scoped_history_no_update BEFORE UPDATE ON scoped_cutover_history
BEGIN SELECT RAISE(ABORT, 'scope route history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS scoped_history_no_delete BEFORE DELETE ON scoped_cutover_history
BEGIN SELECT RAISE(ABORT, 'scope route history is append-only'); END;
"""


def _connection(path):
    conn = sqlite3.connect(path or plane.default_cutover_db_path(), timeout=30,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _validate_boundary(boundary):
    if boundary not in plane.SIX_BOUNDARIES:
        raise plane.CutoverError(f"unknown boundary {boundary!r}")


def resolve_route(boundary: str, identity: RouteIdentity, *, path=None) -> dict[str, Any]:
    """Read-only resolution; an absent row never inherits a global target flag.

    An absent database is an unreadable authority, not a fresh legacy scope.
    An existing, bootstrapped old database with no scoped table has no migrated
    scopes. Never create schema while reading it.
    """
    _validate_boundary(boundary)
    target = Path(path or plane.default_cutover_db_path())
    try:
        with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            global_row = conn.execute("SELECT route FROM cutover_boundary_state WHERE boundary=?",
                                      (boundary,)).fetchone()
            if global_row is None:
                raise plane.CutoverError("boundary authority has not been bootstrapped")
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                 ("scoped_cutover_routes",)).fetchone()
            row = (conn.execute("SELECT * FROM scoped_cutover_routes WHERE boundary=? AND identity_key=?",
                                (boundary, identity.key)).fetchone() if table else None)
    except sqlite3.Error as exc:
        raise plane.CutoverError(f"scope route authority unreadable: {exc}") from exc
    default = plane.ROUTE_DISABLED if boundary == plane.LIVE_TRADER else plane.ROUTE_LEGACY
    result = dict(row) if row else {"boundary": boundary, "identity_key": identity.key,
                                   "route": default, "generation": 0}
    if row and row["identity_json"] != canonical_json(identity.as_row()):
        raise plane.CutoverError("scope identity hash collision or corrupted authority")
    if result["route"] not in plane.VALID_ROUTES:
        raise plane.CutoverError("invalid persisted scope route")
    # A disabled boundary remains an emergency stop for all of its scopes.
    if global_row["route"] == plane.ROUTE_DISABLED:
        result["route"] = plane.ROUTE_DISABLED
    result["identity"] = identity.as_row()
    return result


Gate = Callable[[RouteIdentity], dict[str, Any]]


def transition_route(boundary: str, identity: RouteIdentity, route: str, *, actor: str,
                     reason: str, expected_generation: int, qualification: Gate | None = None,
                     report: Gate | None = None, fallback: Gate | None = None,
                     mode: str = "production", path=None) -> dict[str, Any]:
    """Commit one scope and its history atomically, rechecking gates under lock.

    Joint boundaries/owner files require the later 5.15 coordinator. This primitive
    cannot move any other consumer, scope or boundary. It never issues broker grants.
    """
    from .boundary_evidence import assert_enforced

    if mode=="production":
        raise plane.CutoverError("production transition requires explicit workflow/scope compatibility coordinator")

    _validate_boundary(boundary)
    if route not in plane.VALID_ROUTES or not actor.strip() or not reason.strip():
        raise plane.CutoverError("valid route, actor and reason are required")
    target = path or plane.default_cutover_db_path()
    if mode!="production" and Path(target).resolve()==Path(plane.default_cutover_db_path()).resolve():
        from .isolation import verified_isolation_root
        if verified_isolation_root() is None:
            raise plane.CutoverError("active scope writes require verified physical isolation")
    # Verify existing authority before opening a writable connection.
    resolve_route(boundary, identity, path=target)
    from .joint_cutover import assert_scope_open
    assert_scope_open(identity,path=target)
    with closing(_connection(target)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            assert_scope_open(identity,path=target)
            current = resolve_route(boundary, identity, path=target)
            if current["generation"] != expected_generation:
                raise plane.CutoverError("scope route generation changed")
            if route == plane.ROUTE_TARGET and current["route"] == plane.ROUTE_DISABLED:
                raise plane.CutoverError("boundary disabled")
            evidence = {}
            if route == plane.ROUTE_TARGET:
                assert_enforced(boundary, identity=identity, mode=mode, path=target)
                for name, checker, required in (("qualification", qualification, "status"),
                                                 ("report", report, "valid"),
                                                 ("fallback", fallback, "valid")):
                    if checker is None:
                        raise plane.CutoverError(f"{name} checker missing")
                    verdict = checker(identity)
                    if verdict.get("identity") != identity.as_row():
                        raise plane.CutoverError(f"{name} scope mismatch")
                    if verdict.get(required) != ("eligible" if name == "qualification" else True):
                        raise plane.CutoverError(f"{name} refuses scope: {verdict}")
                    if name != "qualification" and not verdict.get("reference"):
                        raise plane.CutoverError(f"{name} reference missing")
                    evidence[name] = verdict
            elif route == plane.ROUTE_LEGACY and current["route"] == plane.ROUTE_TARGET:
                if fallback is None:
                    raise plane.CutoverError("fallback checker missing")
                verdict = fallback(identity)
                if (verdict.get("identity") != identity.as_row() or verdict.get("valid") is not True
                        or not verdict.get("reference")):
                    raise plane.CutoverError("fallback refuses scope")
                evidence["fallback"] = verdict
            generation = current["generation"] + 1
            stamp = datetime.now(UTC).isoformat()
            payload = canonical_json(identity.as_row())
            conn.execute("INSERT INTO scoped_cutover_routes VALUES (?,?,?,?,?,?) "
                         "ON CONFLICT(boundary,identity_key) DO UPDATE SET "
                         "route=excluded.route,generation=excluded.generation,updated_at=excluded.updated_at",
                         (boundary, identity.key, payload, route, generation, stamp))
            conn.execute("INSERT INTO scoped_cutover_history (boundary,identity_key,identity_json,"
                         "from_route,to_route,generation,actor,reason,evidence_json,changed_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (boundary, identity.key, payload, current["route"], route, generation,
                          actor, reason, canonical_json(evidence), stamp))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return resolve_route(boundary, identity, path=target)


def route_history(boundary: str, identity: RouteIdentity, *, path=None) -> list[dict[str, Any]]:
    resolve_route(boundary, identity, path=path)
    target = Path(path or plane.default_cutover_db_path())
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scoped_cutover_history'").fetchone():
            return []
        return [dict(row) for row in conn.execute(
            "SELECT * FROM scoped_cutover_history WHERE boundary=? AND identity_key=? ORDER BY event_id",
            (boundary, identity.key))]


def route_summary(*, path=None) -> dict[str, list[dict[str, Any]]]:
    """Diagnostic scope inventory; it cannot grant eligibility to unlisted scopes."""
    target = Path(path or plane.default_cutover_db_path())
    result = {boundary: [] for boundary in plane.SIX_BOUNDARIES}
    try:
        with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scoped_cutover_routes'").fetchone():
                return result
            for row in conn.execute("SELECT * FROM scoped_cutover_routes ORDER BY boundary,identity_key"):
                result[row["boundary"]].append({"identity": json.loads(row["identity_json"]),
                    "route": row["route"], "generation": row["generation"], "updated_at": row["updated_at"]})
    except (sqlite3.Error, ValueError, KeyError) as exc:
        raise plane.CutoverError(f"scope route authority unreadable: {exc}") from exc
    return result
