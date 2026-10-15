"""Consumer-wide research-read routes (Phase F simplification, 2026-10-15).

Why this exists
---------------
The exact-scope machinery (one route per consumer x entity x time window, each
backed by raw qualification events, entry evidence, compatibility proofs and a
hash-bound authorization) could not be bootstrapped: a consumer may only switch
once its new-entry output already exists in production, and the new entry can
only read once switched. The user ruled (G1-G4) that cutover follows
TARGET_WORKFLOW_DATAFLOW.md: research reads switch per consumer, and during the
migration window a failure falls back to the old route (TARGET §14.6 "keep the
old entry as a controlled rollback path").

What it does and does not do
----------------------------
- One route per consumer on the ``projection_read`` boundary only. Approval,
  Clerk publication, schedule ownership and the live trader keep their own
  protocols; writing a consumer-wide route for any other boundary is refused.
- An exact-scope route row, where one exists, still wins over the consumer-wide
  row (``scoped_routes.resolve_route``), so a single scope can still be stopped.
- A global ``disabled`` boundary still overrides everything.
- ``switch`` needs a passing data-product health check and a recorded human
  approval. ``rollback`` needs neither: going back must always be possible.
- Every action is appended to ``consumer_cutover_log``, which cannot be updated
  or deleted.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from . import cutover as plane

ROUTES = (plane.ROUTE_LEGACY, plane.ROUTE_TARGET)
BOUNDARY = plane.PROJECTION_READ
# Consumers whose research read can move as a whole. Chief/Risk/Trader/Clerk read
# for decisions and trading; they move with their own boundaries, not here.
CONSUMERS = ("layer", "information", "sector", "fundamental", "macro", "technical")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consumer_cutover_routes (
    boundary TEXT NOT NULL, domain_id TEXT NOT NULL, consumer_id TEXT NOT NULL,
    contract_version TEXT NOT NULL, route TEXT NOT NULL, generation INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(boundary, domain_id, consumer_id, contract_version)
);
CREATE TABLE IF NOT EXISTS consumer_cutover_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, boundary TEXT NOT NULL, consumer_id TEXT NOT NULL,
    action TEXT NOT NULL, from_route TEXT NOT NULL, to_route TEXT NOT NULL,
    generation INTEGER NOT NULL, actor TEXT NOT NULL, approval TEXT NOT NULL,
    reason TEXT NOT NULL, observe_until TEXT NOT NULL, stop_conditions TEXT NOT NULL,
    health_json TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS consumer_cutover_log_no_update BEFORE UPDATE ON consumer_cutover_log
BEGIN SELECT RAISE(ABORT, 'consumer cutover log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS consumer_cutover_log_no_delete BEFORE DELETE ON consumer_cutover_log
BEGIN SELECT RAISE(ABORT, 'consumer cutover log is append-only'); END;
"""


class ConsumerCutoverError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _target(path) -> Path:
    return Path(path or plane.default_cutover_db_path())


def _contract(consumer_id: str) -> dict[str, Any]:
    from .cutover_routing import _consumer_contract

    if consumer_id not in CONSUMERS:
        raise ConsumerCutoverError(
            f"{consumer_id!r} is not a research-read consumer; decision and trading roles "
            "move with their own boundaries")
    return _consumer_contract(consumer_id)


def lookup(conn: sqlite3.Connection, boundary: str, identity) -> dict[str, Any] | None:
    """Consumer-wide row for an identity, read on the caller's read-only connection."""
    if boundary != BOUNDARY:
        return None
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='consumer_cutover_routes'"
                        ).fetchone():
        return None
    row = conn.execute(
        "SELECT route, generation FROM consumer_cutover_routes WHERE boundary=? AND domain_id=? "
        "AND consumer_id=? AND contract_version=?",
        (boundary, identity.domain_id, identity.consumer_id, identity.contract_version)).fetchone()
    if row is None:
        return None
    route, generation = row[0], row[1]
    if route not in ROUTES:
        raise plane.CutoverError("invalid persisted consumer-wide route")
    return {"route": route, "generation": generation}


def _write(conn, consumer_id, *, route, action, actor, approval, reason,
           observe_until="", stop_conditions="", health=None) -> dict[str, Any]:
    contract = _contract(consumer_id)
    key = (BOUNDARY, contract["domain_id"], consumer_id, contract["contract_version"])
    current = conn.execute(
        "SELECT route, generation FROM consumer_cutover_routes WHERE boundary=? AND domain_id=? "
        "AND consumer_id=? AND contract_version=?", key).fetchone()
    before, generation = (current[0], current[1]) if current else (plane.ROUTE_LEGACY, 0)
    generation += 1
    stamp = _now()
    conn.execute(
        "INSERT INTO consumer_cutover_routes VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(boundary, domain_id, consumer_id, contract_version) DO UPDATE SET "
        "route=excluded.route, generation=excluded.generation, updated_at=excluded.updated_at",
        (*key, route, generation, stamp))
    conn.execute(
        "INSERT INTO consumer_cutover_log (boundary, consumer_id, action, from_route, to_route, "
        "generation, actor, approval, reason, observe_until, stop_conditions, health_json, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (BOUNDARY, consumer_id, action, before, route, generation, actor, approval, reason,
         observe_until, stop_conditions, json.dumps(health or {}, ensure_ascii=False, default=str), stamp))
    return {"consumer_id": consumer_id, "action": action, "from_route": before, "to_route": route,
            "generation": generation, "recorded_at": stamp}


def _transaction(path):
    target = _target(path)
    if not target.exists():
        raise plane.CutoverError("cutover authority has not been bootstrapped")
    conn = sqlite3.connect(target, isolation_level=None)
    conn.executescript(_SCHEMA)
    conn.execute("BEGIN IMMEDIATE")
    return conn


def switch(consumer_id: str, *, actor: str, approval: str, observe_until: str,
           stop_conditions: str, health: dict[str, Any] | None = None,
           health_check: Callable[[str], dict[str, Any]] | None = None, path=None) -> dict[str, Any]:
    """Move one consumer's research read to the new route.

    Refuses without a recorded human approval, an explicit observation end and
    stop conditions, or a passing data-product health check.
    """
    for label, value in (("actor", actor), ("approval", approval), ("observe_until", observe_until),
                         ("stop_conditions", stop_conditions)):
        if not str(value).strip():
            raise ConsumerCutoverError(f"switch requires {label}")
    end = datetime.fromisoformat(observe_until)
    if end.tzinfo is None or end <= datetime.now(UTC):
        raise ConsumerCutoverError("observation must end in the future and carry a timezone")
    if health is None:
        from .consumer_health import check

        health = (health_check or check)(consumer_id)
    if health.get("status") != "ok":
        raise ConsumerCutoverError(f"health check did not pass for {consumer_id}: {health.get('problems')}")
    with closing(_transaction(path)) as conn:
        try:
            result = _write(conn, consumer_id, route=plane.ROUTE_TARGET, action="switch", actor=actor,
                            approval=approval, reason="approved consumer-wide research-read switch",
                            observe_until=observe_until, stop_conditions=stop_conditions, health=health)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return result


def rollback(consumer_id: str, *, actor: str, reason: str, health: dict[str, Any] | None = None,
             path=None) -> dict[str, Any]:
    """Return one consumer to the old route. Always allowed; it is the way back."""
    if not str(actor).strip() or not str(reason).strip():
        raise ConsumerCutoverError("rollback requires actor and reason")
    with closing(_transaction(path)) as conn:
        try:
            result = _write(conn, consumer_id, route=plane.ROUTE_LEGACY, action="rollback", actor=actor,
                            approval="", reason=reason, health=health)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return result


def record_health(consumer_id: str, health: dict[str, Any], *, actor: str, path=None) -> None:
    """Append a health observation without changing the route."""
    _contract(consumer_id)
    with closing(_transaction(path)) as conn:
        try:
            row = conn.execute("SELECT route, generation FROM consumer_cutover_routes WHERE boundary=? "
                               "AND consumer_id=?", (BOUNDARY, consumer_id)).fetchone()
            route, generation = (row[0], row[1]) if row else (plane.ROUTE_LEGACY, 0)
            conn.execute(
                "INSERT INTO consumer_cutover_log (boundary, consumer_id, action, from_route, to_route, "
                "generation, actor, approval, reason, observe_until, stop_conditions, health_json, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (BOUNDARY, consumer_id, "health", route, route, generation, actor, "",
                 "daily data-product health check", "", "",
                 json.dumps(health, ensure_ascii=False, default=str), _now()))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def status(*, path=None) -> list[dict[str, Any]]:
    target = _target(path)
    if not target.exists():
        return []
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='consumer_cutover_routes'").fetchone():
            return []
        return [dict(r) for r in conn.execute("SELECT * FROM consumer_cutover_routes ORDER BY consumer_id")]


def log(*, consumer_id: str | None = None, path=None) -> list[dict[str, Any]]:
    target = _target(path)
    if not target.exists():
        return []
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='consumer_cutover_log'").fetchone():
            return []
        sql = "SELECT * FROM consumer_cutover_log" + (" WHERE consumer_id=?" if consumer_id else "") + " ORDER BY seq"
        return [dict(r) for r in conn.execute(sql, (consumer_id,) if consumer_id else ())]


def daily_check(*, health_check: Callable[[str], dict[str, Any]] | None = None,
                notify: Callable[[str, str], None] | None = None, path=None) -> list[dict[str, Any]]:
    """For every consumer on the new route: check health; on failure roll back and alert."""
    from .consumer_health import check

    results = []
    for row in status(path=path):
        if row["route"] != plane.ROUTE_TARGET:
            continue
        consumer = row["consumer_id"]
        try:
            health = (health_check or check)(consumer)
        except Exception as exc:  # noqa: BLE001 - an unreadable check is a failed check
            health = {"status": "failed", "problems": [f"health check raised {type(exc).__name__}: {exc}"]}
        if health.get("status") == "ok":
            record_health(consumer, health, actor="daily-health", path=path)
            results.append({"consumer_id": consumer, "action": "health_ok"})
            continue
        outcome = rollback(consumer, actor="daily-health",
                           reason="automatic rollback: daily data-product health check failed",
                           health=health, path=path)
        results.append({**outcome, "consumer_id": consumer, "action": "rolled_back"})
        if notify is not None:
            try:
                notify(f"Phase F 自动切回旧路径：{consumer}",
                       "每日数据产品健康检查未通过，已把研究读取切回旧路径。\n问题：" +
                       "；".join(str(p) for p in health.get("problems", []))[:1500])
            except Exception:  # noqa: BLE001 - the rollback already happened; alerting must not undo it
                pass
    return results
