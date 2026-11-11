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
from .cutover_routing import FallbackUnsafe, ReadUnavailable, is_retired, read_route
from .scoped_routes import resolve_route, route_history

from .read_scope import _AUDITS, _TRACE, read_projection, record_read, trace_reads  # noqa: E402,F401


@contextmanager
def audit_read_entry(context):
    """Capture real entry reads; append production receipts after business work.

    Isolated traces remain in their actual runner and cannot certify production
    consumer zero. No receipt is created by a static inventory or qualification.
    """
    rows = _TRACE.get()
    token = None
    if rows is None:
        rows = []
        token = _TRACE.set(rows)
    audit_rows = []
    audit_token = _AUDITS.set((*_AUDITS.get(), audit_rows))
    started = datetime.now(UTC).isoformat()
    status = 'failed'
    try:
        yield
        status = 'passed'
    finally:
        from .isolation import verified_isolation_root
        selected = [row for row in audit_rows if row.get('business_identity') == context.identity.as_row()]
        try:
            if context.invocation:
                from .invocation_bindings import append_receipt
                append_receipt(context, selected, started_at=started, status=status)
            if context.mode == 'production' and verified_isolation_root() is None:
                target = Path(plane.default_cutover_db_path()).resolve()
                with closing(sqlite3.connect(target.as_uri()+'?mode=rw',uri=True)) as conn:
                    conn.executescript("""
                        CREATE TABLE IF NOT EXISTS production_consumer_receipts(
                          seq INTEGER PRIMARY KEY AUTOINCREMENT, identity_json TEXT NOT NULL,
                          route TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
                          ended_at TEXT NOT NULL, reads_json TEXT NOT NULL);
                        CREATE TRIGGER IF NOT EXISTS consumer_receipt_no_update BEFORE UPDATE ON production_consumer_receipts
                        BEGIN SELECT RAISE(ABORT,'consumer receipts are append-only'); END;
                        CREATE TRIGGER IF NOT EXISTS consumer_receipt_no_delete BEFORE DELETE ON production_consumer_receipts
                        BEGIN SELECT RAISE(ABORT,'consumer receipts are append-only'); END;
                    """)
                    conn.execute('INSERT INTO production_consumer_receipts(identity_json,route,status,started_at,ended_at,reads_json) VALUES(?,?,?,?,?,?)',
                        (json.dumps(context.identity.as_row(),sort_keys=True),context.route,status,
                         started,datetime.now(UTC).isoformat(),json.dumps(selected,sort_keys=True,default=str)))
                    conn.commit()
        finally:
            _AUDITS.reset(audit_token)
            if token is not None:
                _TRACE.reset(token)


def implementation_hash(reader):
    source = inspect.getsourcefile(reader)
    if not source:
        raise ReadUnavailable("fallback implementation source unavailable")
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
            raise ReadUnavailable("business read unavailable")
        current = resolve_route(plane.PROJECTION_READ, identity, path=path)
        if (current["generation"] != authority["generation"] or
                current["route"] != authority["route"]):
            raise ReadUnavailable("read route changed during consumption")
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
            raise ReadUnavailable("state read escapes checked fallback")
        return fallback.value
    if business_scope is not None or context:
        identity = business_scope or context.identity
        if identity.consumer_id != consumer or (context and context.identity != identity):
            raise ReadUnavailable("state consumer escapes business request")
        if symbol and symbol not in identity.scope["entities"]:
            raise ReadUnavailable("state entity escapes business request")
        value = consume_read(identity, target_reader=reader, legacy_reader=reader,
                             legacy_identifier="store.direct_trade_reads",
                             usable=lambda state: state is not None, refs=refs,
                             target_identifier="ats.execution.state_api.get_internal_state",
                             recovery_readers=recovery_readers)
    else:
        value = reader()
        record_read(consumer, "ats.execution.state_api.get_internal_state", refs=refs(value))
    return value
