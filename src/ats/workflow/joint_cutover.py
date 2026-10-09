"""Durable exact-scope freeze across cutover state and schedule SQL authority.

Pending journals are consulted at real read/publication/claim entry points. WAL
databases are committed separately; a durable fence closes the gap. Recovery
uses the pinned request and each database's actual generation, never resets or
deletes history. The coordinator does not modify configuration or broker grants.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from . import cutover as plane
from . import scoped_routes as scopes

_ACTIVE = ContextVar("joint_cutover_coordinator", default=None)
SCHEMA = """
CREATE TABLE IF NOT EXISTS joint_cutover_operations(
 batch_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
 state TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS joint_cutover_events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, phase TEXT NOT NULL,
 body TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS joint_cutover_authorizations(
 reference TEXT PRIMARY KEY, request_hash TEXT NOT NULL, valid_until TEXT NOT NULL,
 actor TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS joint_cutover_revocations(
 reference TEXT PRIMARY KEY, actor TEXT NOT NULL, reason TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS joint_revoke_no_update BEFORE UPDATE ON joint_cutover_revocations
 BEGIN SELECT RAISE(ABORT,'joint revocation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS joint_revoke_no_delete BEFORE DELETE ON joint_cutover_revocations
 BEGIN SELECT RAISE(ABORT,'joint revocation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS joint_auth_no_update BEFORE UPDATE ON joint_cutover_authorizations
 BEGIN SELECT RAISE(ABORT,'joint authorization is append-only'); END;
CREATE TRIGGER IF NOT EXISTS joint_auth_no_delete BEFORE DELETE ON joint_cutover_authorizations
 BEGIN SELECT RAISE(ABORT,'joint authorization is append-only'); END;
CREATE TRIGGER IF NOT EXISTS joint_events_no_update BEFORE UPDATE ON joint_cutover_events
 BEGIN SELECT RAISE(ABORT,'joint history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS joint_events_no_delete BEFORE DELETE ON joint_cutover_events
 BEGIN SELECT RAISE(ABORT,'joint history is append-only'); END;
"""


def _read(path=None):
    conn = sqlite3.connect(
        Path(path or plane.default_cutover_db_path()).resolve().as_uri() + "?mode=ro", uri=True
    )
    conn.row_factory = sqlite3.Row
    return conn


def _write(path):
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _event(conn, batch, phase, body):
    conn.execute(
        "INSERT INTO joint_cutover_events(batch_id,phase,body,recorded_at) VALUES(?,?,?,?)",
        (batch, phase, scopes.canonical_json(body), datetime.now(UTC).isoformat()),
    )


def _operations(conn):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='joint_cutover_operations'"
    ).fetchone():
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM joint_cutover_operations")]


def assert_scope_open(identity, *, boundary=None, path=None):
    """Missing/unreadable control authority refuses; an old database has no journal."""
    try:
        with closing(_read(path)) as conn:
            for row in _operations(conn):
                if row["state"] == "committed":
                    continue
                request = json.loads(row["request_json"])
                if any(
                    m["identity"] == identity.as_row()
                    and (boundary is None or m["boundary"] == boundary)
                    for m in request["members"]
                ):
                    raise plane.CutoverError("joint batch frozen: " + row["batch_id"])
    except (sqlite3.Error, ValueError, KeyError) as exc:
        raise plane.CutoverError("joint authority unreadable") from exc


def assert_schedule_open(workflow, scope, *, path=None):
    with closing(_read(path)) as conn:
        for row in _operations(conn):
            request = json.loads(row["request_json"])
            if row["state"] != "committed" and any(
                s["workflow"] == workflow and s["scope"] == scope for s in request["schedules"]
            ):
                raise plane.CutoverError("joint schedule frozen: " + row["batch_id"])


def epoch(identity, *, path=None):
    """Fence pre-freeze manually bound workers even after the journal is released."""
    with closing(_read(path)) as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='joint_cutover_events'"
        ).fetchone():
            return 0
        rows = _operations(conn)
        batches = {
            r["batch_id"]
            for r in rows
            if any(
                m["identity"] == identity.as_row() for m in json.loads(r["request_json"])["members"]
            )
        }
        return max(
            (
                r["seq"]
                for r in conn.execute("SELECT seq,batch_id FROM joint_cutover_events")
                if r["batch_id"] in batches
            ),
            default=0,
        )


def authorize_schedule(workflow, scope):
    active = _ACTIVE.get()
    scope = json.loads(scope) if isinstance(scope, str) else scope
    if active is None or not any(
        s["workflow"] == workflow and s["scope"] == scope for s in active["schedules"]
    ):
        raise plane.CutoverError("schedule action requires exact authorized joint coordinator")


def assert_boundary_open(boundary, *, path=None):
    with closing(_read(path)) as conn:
        # No identity means no proof that this publisher belongs to an unrelated
        # scope. Bound publishers retain exact-scope availability.
        if any(r["state"] != "committed" for r in _operations(conn)):
            raise plane.CutoverError("unscoped publication cannot bypass frozen joint boundary")


def _validate(request):
    request = json.loads(scopes.canonical_json(request))
    if not request.get("batch_id") or not request.get("workflow") or not request.get("members"):
        raise plane.CutoverError("explicit joint batch/workflow/members required")
    request.setdefault("schedules", [])
    seen = set()
    for member in request["members"]:
        who = scopes.RouteIdentity(**member["identity"])
        member["identity"] = who.as_row()
        key = (member["boundary"], who.key)
        if (
            key in seen
            or member["boundary"] not in plane.SIX_BOUNDARIES
            or member["route"] not in plane.VALID_ROUTES
        ):
            raise plane.CutoverError("invalid or duplicate joint member")
        if member["boundary"] == plane.LIVE_TRADER:
            raise plane.CutoverError(
                "broker authority uses its separate authorized switch protocol"
            )
        if not isinstance(member.get("expected_generation"), int):
            raise plane.CutoverError("expected generation required")
        seen.add(key)
    for member in request["members"]:
        who = scopes.RouteIdentity(**member["identity"])
        if member["boundary"] == plane.DISPATCHER_SCHEDULE and not any(
            s["workflow"] == request["workflow"]
            and s["scope"] == {"kind": who.scope["kind"], "id": who.scope["id"]}
            for s in request["schedules"]
        ):
            raise plane.CutoverError("joint schedule requires explicit SQL owner action")
    schedules = set()
    for s in request["schedules"]:
        key = (s["workflow"], scopes.canonical_json(s["scope"]))
        if (
            key in schedules
            or s["workflow"] != request["workflow"]
            or s["from_owner"] not in {"legacy", "dispatcher", "shadow"}
            or s["to_owner"] not in {"legacy", "dispatcher", "shadow"}
            or not isinstance(s["expected_generation"], int)
        ):
            raise plane.CutoverError("invalid schedule action")
        schedules.add(key)
        if not any(
            m["boundary"] == plane.DISPATCHER_SCHEDULE
            and s["scope"]
            == {"kind": m["identity"]["scope"]["kind"], "id": m["identity"]["scope"]["id"]}
            for m in request["members"]
        ):
            raise plane.CutoverError("schedule action escapes explicit members")
        if s["to_owner"] != (
            "dispatcher"
            if all(
                m["route"] == plane.ROUTE_TARGET
                for m in request["members"]
                if m["boundary"] == plane.DISPATCHER_SCHEDULE
            )
            else "legacy"
        ):
            raise plane.CutoverError("owner and schedule route disagree")
    return request


def record_authorization(request, *, reference, valid_until, actor, reason):
    """Record an explicitly supplied deployment decision; never called by execute.

    New scope, generation or recovery authority requires a new immutable record.
    This records deployment authorization only, never a broker write grant.
    """
    request = _validate(request)
    expiry = datetime.fromisoformat(valid_until)
    if (
        not reference
        or not actor
        or not reason
        or expiry.tzinfo is None
        or expiry <= datetime.now(UTC)
    ):
        raise plane.CutoverError("explicit unexpired deployment authorization required")
    digest = hashlib.sha256(scopes.canonical_json(request).encode()).hexdigest()
    with closing(_write(plane.default_cutover_db_path())) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO joint_cutover_authorizations VALUES(?,?,?,?,?)",
            (reference, digest, expiry.isoformat(), actor, reason),
        )


def _authorization(auth, request, path):
    with closing(_read(path)) as conn:
        row = conn.execute(
            "SELECT * FROM joint_cutover_authorizations WHERE reference=?", (auth["reference"],)
        ).fetchone()
        revoked = conn.execute(
            "SELECT 1 FROM joint_cutover_revocations WHERE reference=?", (auth["reference"],)
        ).fetchone()
    digest = hashlib.sha256(scopes.canonical_json(request).encode()).hexdigest()
    if (
        revoked
        or row is None
        or row["request_hash"] != digest
        or datetime.fromisoformat(row["valid_until"]) <= datetime.now(UTC)
    ):
        raise plane.CutoverError("recorded exact joint authorization missing or expired")


def revoke_authorization(reference, *, actor, reason):
    if not actor or not reason:
        raise plane.CutoverError("revocation actor/reason required")
    with closing(_write(plane.default_cutover_db_path())) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO joint_cutover_revocations VALUES(?,?,?,?)",
            (reference, actor, reason, datetime.now(UTC).isoformat()),
        )


def _gates(request, gates, path, mode):
    from .boundary_evidence import assert_enforced

    checked = []
    for member in request["members"]:
        who = scopes.RouteIdentity(**member["identity"])
        auth = gates["authorization"](member, request["workflow"])
        if (
            auth.get("member") != member
            or auth.get("workflow") != request["workflow"]
            or auth.get("valid") is not True
            or not auth.get("reference")
        ):
            raise plane.CutoverError("deployment authorization scope/boundary invalid")
        if mode == "production":
            _authorization(auth, request, path)
        evidence = {"authorization": auth}
        if member["route"] != plane.ROUTE_DISABLED:
            if plane.read_boundary(member["boundary"], path=path).route == plane.ROUTE_DISABLED:
                raise plane.CutoverError("global emergency disabled boundary refuses joint release")
            assert_enforced(member["boundary"], identity=who, mode=mode, path=path)
            for name, required in (
                ("qualification", "eligible"),
                ("report", True),
                ("fallback", True),
            ):
                result = gates[name](who)
                if (
                    result.get("identity") != who.as_row()
                    or result.get("status" if name == "qualification" else "valid") != required
                    or (name != "qualification" and not result.get("reference"))
                ):
                    raise plane.CutoverError(name + " refuses joint scope")
                evidence[name] = result
        checked.append(evidence)
    # The executor supplies actual workflow read/publish dependencies; global
    # pairings cannot establish schema compatibility for an individual scope.
    for index, member in enumerate(request["members"]):
        who = scopes.RouteIdentity(**member["identity"])
        routes = {b: scopes.resolve_route(b, who, path=path)["route"] for b in plane.SIX_BOUNDARIES}
        for other in request["members"]:
            if other["identity"] == who.as_row():
                routes[other["boundary"]] = other["route"]
        matrix = gates["compatibility"](request["workflow"], who, routes.copy())
        if (
            matrix.get("workflow") != request["workflow"]
            or matrix.get("identity") != who.as_row()
            or matrix.get("valid") is not True
            or not matrix.get("reference")
            or not isinstance(matrix.get("edges"), list)
        ):
            raise plane.CutoverError("exact workflow/scope compatibility matrix required")
        for edge in matrix["edges"]:
            group = edge.get("boundaries", [])
            if (
                len(set(group)) != len(group)
                or len(group) < 2
                or not set(group) <= set(plane.SIX_BOUNDARIES)
                or plane.LIVE_TRADER in group
                or not isinstance(edge.get("mixed_compatible"), bool)
                or not edge.get("reference")
            ):
                raise plane.CutoverError("invalid explicit compatibility dependency")
            values = {routes[b] for b in group}
            if (
                plane.ROUTE_TARGET in values
                and plane.ROUTE_LEGACY in values
                and not edge["mixed_compatible"]
            ):
                raise plane.CutoverError(
                    "incompatible independent request; explicitly include joint peers"
                )
        checked[index]["compatibility"] = matrix
    return checked


def _schedule_state(s):
    from . import schedule_runtime as runtime

    with closing(runtime._connect()) as conn:
        row = conn.execute(
            "SELECT * FROM schedule_scope_owners WHERE workflow=? AND scope=?",
            (s["workflow"], runtime.canonical(s["scope"])),
        ).fetchone()
    if row is None:
        raise plane.CutoverError(
            "exact schedule owner missing; cannot inherit template for cutover"
        )
    return dict(row)


def execute(request, *, gates, actor, reason, mode="production", path=None, checkpoint=None):
    """Resume only the identical request, with fresh authorization and gates.

    A failure after prepare intentionally keeps its journal fence. SQL writes and
    journal markers commit together within each authority; cross-authority gaps
    remain fenced. checkpoint is a test fault injector, never an approval source.
    """
    from . import schedule_runtime as runtime

    request = _validate(request)
    from .isolation import verified_isolation_root

    if mode not in {"production", "isolated"} or (
        mode == "isolated" and verified_isolation_root() is None
    ):
        raise plane.CutoverError("isolated gates require verified physical isolation")
    if (
        not actor
        or not reason
        or set(gates) != {"authorization", "qualification", "report", "fallback", "compatibility"}
    ):
        raise plane.CutoverError("actor/reason and all gate adapters required")
    target = Path(path or plane.default_cutover_db_path()).resolve()
    if target != Path(plane.default_cutover_db_path()).resolve():
        raise plane.CutoverError("coordinator must use active control authority")
    digest = hashlib.sha256(scopes.canonical_json(request).encode()).hexdigest()
    batch = request["batch_id"]
    for m in request["members"]:
        scopes.resolve_route(m["boundary"], scopes.RouteIdentity(**m["identity"]), path=target)
    with closing(_write(target)) as conn:
        conn.executescript(SCHEMA + scopes._SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            saved = conn.execute(
                "SELECT * FROM joint_cutover_operations WHERE batch_id=?", (batch,)
            ).fetchone()
            if saved and saved["request_hash"] != digest:
                raise plane.CutoverError("joint request changed; recovery refuses")
            if saved and saved["state"] == "committed":
                return inspect(batch, path=target)
            if saved is None:
                for row in _operations(conn):
                    prior = json.loads(row["request_json"])
                    if row["state"] != "committed" and (
                        any(
                            a["identity"] == b["identity"]
                            for a in request["members"]
                            for b in prior["members"]
                        )
                        or any(
                            a["workflow"] == b["workflow"] and a["scope"] == b["scope"]
                            for a in request["schedules"]
                            for b in prior["schedules"]
                        )
                    ):
                        raise plane.CutoverError("overlapping joint operation already frozen")
                _gates(request, gates, target, mode)
                for m in request["members"]:
                    if (
                        scopes.resolve_route(
                            m["boundary"], scopes.RouteIdentity(**m["identity"]), path=target
                        )["generation"]
                        != m["expected_generation"]
                    ):
                        raise plane.CutoverError("scope generation changed before freeze")
                for s in request["schedules"]:
                    state = _schedule_state(s)
                    if (
                        state["frozen"]
                        or state["owner"] != s["from_owner"]
                        or state["generation"] != s["expected_generation"]
                    ):
                        raise plane.CutoverError("schedule owner changed before freeze")
                conn.execute(
                    "INSERT INTO joint_cutover_operations VALUES(?,?,?,?,?,?)",
                    (batch, digest, scopes.canonical_json(request), "prepared", actor, reason),
                )
                _event(
                    conn,
                    batch,
                    "prepared",
                    {
                        "request_hash": digest,
                        "actor": actor,
                        "reason": reason,
                        "schedule_authority": str(runtime.state_path().resolve()),
                    },
                )
            else:
                pinned = json.loads(
                    conn.execute(
                        "SELECT body FROM joint_cutover_events WHERE batch_id=? AND phase='prepared' ORDER BY seq LIMIT 1",
                        (batch,),
                    ).fetchone()[0]
                )
                if pinned["schedule_authority"] != str(runtime.state_path().resolve()):
                    raise plane.CutoverError("schedule authority path changed; recovery refuses")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    token = _ACTIVE.set(request)
    fault = checkpoint or (lambda phase: None)
    try:
        fault("prepared")
        # Own existing SQL freeze or recover from a crash between freeze and log.
        for s in request["schedules"]:
            state = _schedule_state(s)
            if (
                state["generation"] == s["expected_generation"] + 1
                and state["owner"] == s["to_owner"]
                and not state["frozen"]
            ):
                continue
            if state["generation"] != s["expected_generation"] or state["owner"] != s["from_owner"]:
                raise plane.CutoverError("schedule authority drift; remain frozen")
            if state["frozen"]:
                with closing(runtime._connect()) as conn:
                    row = conn.execute(
                        "SELECT payload FROM schedule_runtime_history WHERE action='freeze' AND key=? ORDER BY seq DESC LIMIT 1",
                        (state["token"],),
                    ).fetchone()
                if row is None or json.loads(row[0])["reason"] != "joint:" + batch:
                    raise plane.CutoverError("foreign freeze token; recovery refuses")
            if not state["frozen"]:
                runtime.freeze(s["workflow"], s["scope"], actor=actor, reason="joint:" + batch)
        fault("frozen")
        with closing(_write(target)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                evidence = _gates(request, gates, target, mode)
                for m, e in zip(request["members"], evidence):
                    who = scopes.RouteIdentity(**m["identity"])
                    row = conn.execute(
                        "SELECT * FROM scoped_cutover_routes WHERE boundary=? AND identity_key=?",
                        (m["boundary"], who.key),
                    ).fetchone()
                    generation = row["generation"] if row else 0
                    if generation == m["expected_generation"] + 1 and row["route"] == m["route"]:
                        continue
                    if generation != m["expected_generation"]:
                        raise plane.CutoverError("scope authority drift; remain frozen")
                    before = (
                        row["route"]
                        if row
                        else ("disabled" if m["boundary"] == plane.LIVE_TRADER else "legacy")
                    )
                    stamp = datetime.now(UTC).isoformat()
                    conn.execute(
                        "INSERT INTO scoped_cutover_routes VALUES(?,?,?,?,?,?) ON CONFLICT(boundary,identity_key) DO UPDATE SET route=excluded.route,generation=excluded.generation,updated_at=excluded.updated_at",
                        (
                            m["boundary"],
                            who.key,
                            scopes.canonical_json(who.as_row()),
                            m["route"],
                            generation + 1,
                            stamp,
                        ),
                    )
                    conn.execute(
                        "INSERT INTO scoped_cutover_history(boundary,identity_key,identity_json,from_route,to_route,generation,actor,reason,evidence_json,changed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            m["boundary"],
                            who.key,
                            scopes.canonical_json(who.as_row()),
                            before,
                            m["route"],
                            generation + 1,
                            actor,
                            "joint:" + batch,
                            scopes.canonical_json(e),
                            stamp,
                        ),
                    )
                    fault("first_route_write")
                _event(conn, batch, "routes_written", {"actor": actor})
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        fault("routes_committed")
        for s in request["schedules"]:
            state = _schedule_state(s)
            if (
                state["generation"] == s["expected_generation"] + 1
                and state["owner"] == s["to_owner"]
                and not state["frozen"]
            ):
                continue
            _gates(request, gates, target, mode)
            runtime.handover(
                state["token"],
                to_owner=s["to_owner"],
                dispositions=s.get("dispositions", {}),
                actor=actor,
                reason="joint:" + batch,
            )
        fault("owners_committed")
        with closing(_write(target)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                evidence = _gates(request, gates, target, mode)
                for m in request["members"]:
                    row = conn.execute(
                        "SELECT route,generation FROM scoped_cutover_routes WHERE boundary=? AND identity_key=?",
                        (m["boundary"], scopes.RouteIdentity(**m["identity"]).key),
                    ).fetchone()
                    if (
                        row is None
                        or row["route"] != m["route"]
                        or row["generation"] != m["expected_generation"] + 1
                    ):
                        raise plane.CutoverError("scope cannot release joint fence")
                for s in request["schedules"]:
                    state = _schedule_state(s)
                    if (
                        state["frozen"]
                        or state["generation"] != s["expected_generation"] + 1
                        or state["owner"] != s["to_owner"]
                    ):
                        raise plane.CutoverError("owner cannot release joint fence")
                conn.execute(
                    "UPDATE joint_cutover_operations SET state='committed' WHERE batch_id=?",
                    (batch,),
                )
                _event(
                    conn, batch, "committed", {"actor": actor, "reason": reason, "gates": evidence}
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    finally:
        _ACTIVE.reset(token)
    return inspect(batch, path=target)


def inspect(batch_id, *, path=None):
    with closing(_read(path)) as conn:
        row = conn.execute(
            "SELECT * FROM joint_cutover_operations WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if row is None:
            raise plane.CutoverError("joint batch missing")
        events = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM joint_cutover_events WHERE batch_id=? ORDER BY seq", (batch_id,)
            )
        ]
    return {**dict(row), "can_run": row["state"] == "committed", "events": events}
