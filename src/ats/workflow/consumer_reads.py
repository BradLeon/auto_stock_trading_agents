"""Actual governed consumption, with exact-scope, live checked fallback.

A callback supplies data, never a boolean availability claim. The result of that
read is returned to the consumer; no second read can replace the checked input.
"""
from __future__ import annotations

from ats.workflow.evaluation_clock import now as evaluation_now

import hashlib
import inspect
import json
import logging
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from . import cutover as plane
from .cutover_routing import FallbackUnsafe, RouteUnavailable, is_retired, read_route
from .scoped_routes import resolve_route, route_history

_TRACE: ContextVar[list | None] = ContextVar("consumer_read_trace", default=None)


@contextmanager
def trace_reads():
    """Collect actual reads in this task; no shared research or production writes."""
    rows = []
    token = _TRACE.set(rows)
    try:
        yield rows
    finally:
        _TRACE.reset(token)


def record_read(consumer, api, *, refs=(), status="complete", **detail):
    row = {"consumer": consumer, "api": api, "refs": list(refs), "status": status, **detail}
    logger = logging.getLogger("ats.consumer_reads")
    (logger.warning if status in {"blocked", "unavailable"} else logger.info)(
        "consumer_read %s", json.dumps(row, sort_keys=True, default=str))
    rows = _TRACE.get()
    if rows is not None:
        rows.append(row)


def implementation_hash(reader):
    source = inspect.getsourcefile(reader)
    if not source:
        raise RouteUnavailable("fallback implementation source unavailable")
    return hashlib.sha256(Path(source).read_bytes()).hexdigest()


def revoke_fallback(identity, reference, *, actor, reason, path=None):
    """Append a withdrawal in control state; do not edit the proof or route."""
    if not actor.strip() or not reason.strip():
        raise ValueError("fallback revocation requires actor/reason")
    resolve_route(plane.PROJECTION_READ, identity, path=path)
    target = Path(path or plane.default_cutover_db_path())
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=rw", uri=True)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fallback_revocations (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT, identity_key TEXT NOT NULL,
              reference TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS fallback_revocations_no_update BEFORE UPDATE ON fallback_revocations
            BEGIN SELECT RAISE(ABORT, 'fallback revocations are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS fallback_revocations_no_delete BEFORE DELETE ON fallback_revocations
            BEGIN SELECT RAISE(ABORT, 'fallback revocations are append-only'); END;
        """)
        conn.execute("INSERT INTO fallback_revocations (identity_key,reference,actor,reason,at) VALUES (?,?,?,?,?)",
                     (identity.key, str(Path(reference).resolve()), actor, reason, evaluation_now(UTC).isoformat()))
        conn.commit()


def _revoked(identity, reference, path):
    target = Path(path or plane.default_cutover_db_path())
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='fallback_revocations'").fetchone():
            return False
        return conn.execute("SELECT 1 FROM fallback_revocations WHERE identity_key=? AND reference=? LIMIT 1",
                            (identity.key, str(Path(reference).resolve()))).fetchone() is not None


def consume_read(identity, *, target_reader, legacy_reader, legacy_identifier,
                 usable, refs, path=None, target_identifier=None, recovery_readers=None):
    """Read at a business consumption point. Failed fallback never calls target.

    Current route history's fallback evidence names an immutable JSON proof
    (reference/sha256). That document binds identity, implementation identifier,
    source hash and valid_until. It is evidence, not an authority to trade.
    """
    cached = []
    recovery_detail = {}
    authority = resolve_route(plane.PROJECTION_READ, identity, path=path)
    from .read_recovery import current_policy

    initial_policy = current_policy(identity, path=path)

    def fallback():
        from .read_recovery import current_policy, recover

        policy = current_policy(identity, path=path)
        if policy:
            readers = dict(recovery_readers or {})
            readers.setdefault("legacy", legacy_reader)
            value, verdict = recover(identity, policy, readers=readers, usable=usable,
                                     refs=refs, path=path)
            cached.append(value)
            recovery_detail.update(verdict)
            return verdict
        def refuse(code, reason):
            raise FallbackUnsafe(reason, reason_code=code,
                                 detail={"identity": identity.as_row()})
        history = route_history(plane.PROJECTION_READ, identity, path=path)
        if not history:
            refuse("fallback_proof_missing", "scope fallback proof missing")
        evidence = json.loads(history[-1]["evidence_json"]).get("fallback", {})
        if (evidence.get("identity") != identity.as_row() or evidence.get("valid") is not True
                or not evidence.get("reference") or not evidence.get("sha256")):
            refuse("fallback_proof_missing", "scope fallback proof missing or mismatched")
        if _revoked(identity, evidence["reference"], path):
            refuse("fallback_proof_revoked", "scope fallback proof withdrawn")
        try:
            body = Path(evidence["reference"]).read_bytes()
            proof = json.loads(body)
            from .assurance_surface import fingerprint, load_surface

            surface = load_surface()
            dependencies = fingerprint([surface.manifest, *surface.paths_for(identity.consumer_id)])
            valid = (hashlib.sha256(body).hexdigest() == evidence["sha256"]
                     and proof["identity"] == identity.as_row()
                     and proof["legacy_identifier"] == legacy_identifier
                     and proof["implementation_sha256"] == implementation_hash(legacy_reader)
                     and proof["dependency_hashes"] == dependencies
                     and datetime.fromisoformat(proof["valid_until"]) > evaluation_now(UTC))
        except (OSError, ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            refuse("fallback_proof_invalid", "scope fallback proof expired, unreadable or drifted")
        try:
            retired = is_retired(legacy_identifier)
        except Exception as exc:
            refuse("fallback_retirement_unreadable", f"retirement authority unreadable: {exc}")
        if retired:
            refuse("fallback_target_retired", "fallback implementation retired")
        try:
            value = legacy_reader()
            if not usable(value) or not refs(value):
                refuse("fallback_target_unavailable", "actual legacy read is unusable")
        except FallbackUnsafe:
            raise
        except Exception as exc:
            refuse("fallback_target_unavailable", f"actual legacy read failed: {exc}")
        if _revoked(identity, evidence["reference"], path):
            refuse("fallback_proof_revoked", "scope fallback proof withdrawn during read")
        if is_retired(legacy_identifier):
            refuse("fallback_target_retired", "fallback implementation retired during read")
        cached.append(value)
        return {"verdict": "ok", "identity": identity.as_row(),
                "reference": evidence["reference"], "proof_valid": True,
                "retired": False, "available": True}

    try:
        decision = read_route(consumer_id=identity.consumer_id, identity=identity,
                              fallback_check=fallback, path=path)
        value = cached[0] if cached else (target_reader() if decision.route == "target"
                                        else legacy_reader())
        if not usable(value):
            raise RouteUnavailable("business read unavailable")
        current = resolve_route(plane.PROJECTION_READ, identity, path=path)
        if (current["generation"] != authority["generation"] or
                current["route"] != authority["route"]):
            raise RouteUnavailable("read route changed during consumption")
        from .read_recovery import ReadStopped, _proof, current_policy

        policy = current_policy(identity, path=path)
        if policy and policy["stopped"]:
            raise ReadStopped("scope stopped during consumption")
        if policy != initial_policy:
            raise ReadStopped("recovery strategy changed during consumption")
        if recovery_detail:
            _proof(identity, policy, path=path)
        record_read(identity.consumer_id, recovery_detail.get("target_identifier") or
                    (legacy_identifier if decision.route == "legacy" else
                     target_identifier or target_reader.__module__ + "." + target_reader.__qualname__),
                    refs=refs(value), route=decision.route, identity=identity.as_row(),
                    fallback=bool(cached), recovery_version=recovery_detail.get("target_version", ""))
        return value
    except Exception as exc:
        reason = getattr(exc, "reason_code", "read_failed")
        record_read(identity.consumer_id, legacy_identifier, status="unavailable" if reason in {
            "fallback_proof_invalid", "fallback_proof_revoked", "fallback_target_unavailable"} else "blocked",
                    identity=identity.as_row(), reason_code=reason)
        raise


def read_projection(store, *, consumer, role, scope, at=None, projection_id="", content_hash="",
                    require_usable=True):
    """Read only allowed, published, reusable opinions; preserve concrete refs."""
    import yaml

    from ..agent.task_projection import PAYLOAD_SCHEMA_BY_ROLE, reuse_decision
    from ..agent.task_projection import content_hash as hash_projection
    from ..agents.chief.assemble import _envelope_from_row
    from ..config import REPO_ROOT

    owners = {"information_brief": "information", "layer_analysis": "layer",
              "sector_allocation": "sector", "fundamental_expectation_update": "fundamental",
              "fundamental_event_review": "fundamental", "macro_review": "macro",
              "technical_review": "technical"}
    manifest = yaml.safe_load((REPO_ROOT / "config/data/target_dataflow_coverage.yaml").read_text())
    contract = next(row for row in manifest["consumers"] if row["id"] == consumer)
    if owners.get(role) != consumer and owners.get(role) not in contract["allowed_projection_inputs"]:
        record_read(consumer, "task_projection", status="blocked", producer=owners.get(role),
                    role=role, scope=scope.model_dump(mode="json"), reason_code="undeclared_projection_input")
        raise PermissionError("undeclared_projection_input")
    from .runtime_reads import current_read_context, gate_read

    context = current_read_context()
    if context:
        if context.identity.consumer_id != consumer:
            raise RouteUnavailable("projection consumer escapes business request")
        if scope.kind == "entity" and scope.id not in context.identity.scope["entities"]:
            raise RouteUnavailable("projection entity escapes business request")
        gate_read(context.identity)
        at = at or context.cutoff
    row = store.get_task_projection(projection_id) if projection_id else None
    if not projection_id:
        rows = store.task_projection_envelopes(agent_role=role, scope_kind=scope.kind,
                                               scope_id=scope.id, limit=1)
        row = rows[0] if rows else None
    if row and (row.get("agent_role") != role or row.get("scope_kind") != scope.kind
                or row.get("scope_id") != scope.id or
                (content_hash and row.get("content_hash") != content_hash)):
        raise RouteUnavailable("projection reference scope/hash mismatch")
    if row:
        envelope = _envelope_from_row(row, scope)
        schema = PAYLOAD_SCHEMA_BY_ROLE[role]
        validated_payload = schema.model_validate(envelope.payload)
        if (envelope.schema_name != schema.role_schema_name() or envelope.schema_version != "v1"
                or envelope.content_hash != hash_projection(
                    role=role, scope=scope, as_of=envelope.as_of,
                    schema_name=envelope.schema_name, schema_version=envelope.schema_version,
                    payload=validated_payload, input_refs=envelope.input_refs,
                    data_vintage_refs=envelope.data_vintage_refs)):
            record_read(consumer, "task_projection", status="blocked", refs=[envelope.projection_id])
            raise RouteUnavailable("projection schema/content integrity mismatch")
        ok, reason = reuse_decision(_envelope_from_row(row, scope), scope=scope, at=at)
        if not ok:
            record_read(consumer, "task_projection", status=reason, refs=[row["projection_id"]])
            if require_usable:
                return None
    record_read(consumer, "task_projection", status="complete" if row else "missing",
                producer=owners.get(role), role=role, scope=scope.model_dump(mode="json"),
                refs=[row["projection_id"]] if row else [],
                content_hash=row.get("content_hash", "") if row else "")
    return row


@dataclass(frozen=True)
class InternalFallback:
    """Explicit legacy state adapter for business entries, never a global flag."""
    store: object
    symbol: str = ""
    value: object = None
    recovery_readers: object = None
    route: str = "legacy"

    def prepare(self, identity):
        value = read_internal(self.store, consumer=identity.consumer_id,
                              symbol=self.symbol, business_scope=identity,
                              recovery_readers=self.recovery_readers)
        from .read_recovery import NEW_VERSION, _proof, current_policy

        policy = current_policy(identity)
        route = "target" if policy and _proof(identity, policy)["strategy"] == NEW_VERSION else "legacy"
        return replace(self, value=value, route=route)


def read_internal(store, *, consumer, symbol="", business_scope=None, recovery_readers=None):
    """Read the governed state with its real section timestamps/completeness."""
    from ..execution.state_api import get_internal_state
    from .runtime_reads import current_read_context

    if consumer not in {"chief", "risk"}:
        raise PermissionError("undeclared_internal_state_input")
    reader = lambda: get_internal_state(store, symbol=symbol)
    context = current_read_context()
    refs = lambda value: [f"internal-state:{k}:{v}" for k, v in value.section_as_of.items() if v]
    if context and context.fallback_input is not None and business_scope is None:
        fallback = context.fallback_input
        if (context.identity.consumer_id != consumer or fallback.store is not store
                or fallback.symbol != symbol or business_scope not in (None, context.identity)):
            raise RouteUnavailable("state read escapes checked fallback")
        return fallback.value
    if business_scope is not None or context:
        identity = business_scope or context.identity
        if identity.consumer_id != consumer or (context and context.identity != identity):
            raise RouteUnavailable("state consumer escapes business request")
        if symbol and symbol not in identity.scope["entities"]:
            raise RouteUnavailable("state entity escapes business request")
        value = consume_read(identity, target_reader=reader, legacy_reader=reader,
                             legacy_identifier="store.direct_trade_reads",
                             usable=lambda state: state is not None, refs=refs,
                             target_identifier="ats.execution.state_api.get_internal_state",
                             recovery_readers=recovery_readers)
    else:
        value = reader()
        record_read(consumer, "ats.execution.state_api.get_internal_state", refs=refs(value))
    return value
