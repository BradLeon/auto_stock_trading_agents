"""Exact-scope, pre-registered read recovery; never grants execution authority."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from . import cutover as plane
from .cutover_routing import FallbackUnsafe
from .evaluation_clock import now
from .scoped_routes import RouteIdentity, canonical_json, resolve_route

STOP = "stop"
NEW_VERSION = "new_version"
LEGACY = "legacy"
VERSION = "read-recovery-v1"


class ReadStopped(FallbackUnsafe):
    """A latched stop cannot be bypassed with an entry's legacy adapter."""
    def __init__(self, reason, *, code="read_scope_stopped"):
        super().__init__(reason, reason_code=code)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS read_recovery_events (
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, identity_key TEXT NOT NULL,
 kind TEXT NOT NULL, body TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
 at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS recovery_no_update BEFORE UPDATE ON read_recovery_events
BEGIN SELECT RAISE(ABORT, 'read recovery is append-only'); END;
CREATE TRIGGER IF NOT EXISTS recovery_no_delete BEFORE DELETE ON read_recovery_events
BEGIN SELECT RAISE(ABORT, 'read recovery is append-only'); END;
"""


def _db(path, *, write=False):
    target = Path(path or plane.default_cutover_db_path()).resolve()
    conn = sqlite3.connect(target.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def current_policy(identity, *, path=None):
    resolve_route(plane.PROJECTION_READ, identity, path=path)
    with closing(_db(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='read_recovery_events'").fetchone():
            return None
        row = conn.execute("SELECT * FROM read_recovery_events WHERE identity_key=? "
                           "ORDER BY event_id DESC LIMIT 1", (identity.key,)).fetchone()
    if not row:
        return None
    result = json.loads(row["body"])
    result.update(event_id=row["event_id"], stopped=row["kind"] == "stopped")
    return result


def _append(identity, kind, body, *, actor, reason, path, expected_event=None):
    if not actor.strip() or not reason.strip():
        raise ValueError("recovery event requires actor/reason")
    resolve_route(plane.PROJECTION_READ, identity, path=path)
    with closing(_db(path, write=True)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        latest = conn.execute("SELECT event_id FROM read_recovery_events WHERE identity_key=? "
                              "ORDER BY event_id DESC LIMIT 1", (identity.key,)).fetchone()
        if expected_event is not None and (latest is None or latest[0] != expected_event):
            raise ReadStopped("recovery policy changed during consumption")
        conn.execute("INSERT INTO read_recovery_events(identity_key,kind,body,actor,reason,at) "
                     "VALUES (?,?,?,?,?,?)", (identity.key, kind, canonical_json(body), actor, reason,
                                               now(UTC).isoformat()))
        conn.commit()


def dependency_hashes(consumer):
    from .assurance_surface import fingerprint, load_surface
    surface = load_surface()
    return fingerprint([surface.manifest, *surface.paths_for(consumer),
        "src/ats/workflow/read_recovery.py", "src/ats/workflow/consumer_reads.py",
        "src/ats/workflow/cutover_routing.py", "src/ats/workflow/runtime_reads.py",
        "src/ats/workflow/cutover_wiring.py"])


def _proof(identity, policy, *, path=None):
    from .consumer_reads import _revoked
    try:
        reference = Path(policy["reference"])
        raw = reference.read_bytes()
        proof = json.loads(raw)
        if (_revoked(identity, reference, path) or hashlib.sha256(raw).hexdigest() != policy["sha256"]
                or proof["version"] != VERSION or proof["identity"] != identity.as_row()
                or proof["strategy"] not in {STOP, NEW_VERSION, LEGACY}
                or proof["dependency_hashes"] != dependency_hashes(identity.consumer_id)
                or datetime.fromisoformat(proof["valid_until"]) <= now(UTC)):
            raise ValueError("scope/proof/expiry/dependency/revocation")
        if proof["strategy"] != STOP:
            if not all(proof.get(k) for k in ("target_version", "target_identifier",
                        "implementation_sha256", "accepted_report_ref", "target_identity")):
                raise ValueError("accepted target proof incomplete")
            target = RouteIdentity(**proof["target_identity"])
            if (target.scope != identity.scope or target.consumer_id != identity.consumer_id
                    or target.domain_id != identity.domain_id):
                raise ValueError("target scope differs")
            accepted_raw = Path(proof["accepted_report_ref"]).read_bytes()
            accepted = json.loads(accepted_raw)
            if (hashlib.sha256(accepted_raw).hexdigest() != proof["accepted_report_sha256"]
                    or accepted.get("identity") != target.as_row()
                    or accepted.get("target_version") != proof["target_version"]
                    or accepted.get("status") != "passed"):
                raise ValueError("accepted target receipt missing or mismatched")
        return proof
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise ReadStopped("recovery proof unreadable, expired, withdrawn or drifted",
                          code="recovery_proof_invalid") from exc


def register(identity, *, reference, sha256, actor, reason, path=None):
    """Bind an immutable, accepted recovery proof before activation, no route change.

    A report reference is provenance, not a replacement for current qualification
    or actual readability. Report applicability/signoff is the independent 3.6 gate.
    """
    body = {"reference": str(Path(reference).resolve()), "sha256": sha256}
    _proof(identity, body, path=path)
    _append(identity, "registered", body, actor=actor, reason=reason, path=path)
    return current_policy(identity, path=path)


def stop(identity, policy, reason, *, path=None):
    body = {k: policy[k] for k in ("reference", "sha256")}
    _append(identity, "stopped", body, actor="read-recovery", reason=reason,
            path=path, expected_event=policy["event_id"])
    raise ReadStopped(reason)


def recover(identity, policy, *, readers, usable, refs, path=None):
    """One checked read, then recheck proof, qualification and policy authority."""
    from ..data.assurance import qualification
    from .consumer_reads import implementation_hash
    from .cutover_routing import is_retired

    try:
        proof = _proof(identity, policy, path=path)
        if proof["strategy"] == STOP:
            stop(identity, policy, "registered safe stop", path=path)
        reader = readers.get(proof["target_version"])
        if reader is None:
            raise ReadStopped("registered recovery version has no actual read adapter")
        if implementation_hash(reader) != proof["implementation_sha256"]:
            raise ReadStopped("recovery implementation version drift")
        if (proof["strategy"] == NEW_VERSION and proof["target_identifier"] !=
                reader.__module__ + "." + reader.__qualname__):
            raise ReadStopped("actual reader differs from accepted recovery implementation")
        target = RouteIdentity(**proof["target_identity"])

        def check_target():
            _proof(identity, policy, path=path)
            if is_retired(proof["target_identifier"]):
                raise ReadStopped("recovery target retired")
            if proof["strategy"] == NEW_VERSION:
                result = qualification(domain_id=target.domain_id, consumer_id=target.consumer_id,
                    contract_version=target.contract_version, scope=target.scope)
                if result.get("status") != "eligible":
                    raise ReadStopped("recovery version lacks current exact-scope qualification")
                return result
            return {}

        check_target()
        value = reader()
        if not usable(value) or not refs(value):
            raise ReadStopped("actual recovery read unavailable or missing refs")
        qualification_result = check_target()
        if current_policy(identity, path=path) != policy:
            raise ReadStopped("recovery policy changed during read")
        return value, {"verdict": "ok", "identity": identity.as_row(),
            "reference": policy["reference"], "proof_valid": True, "retired": False,
            "available": True, "recovery_route": "target" if proof["strategy"] == NEW_VERSION else "legacy",
            "target_version": proof["target_version"], "target_identifier": proof["target_identifier"],
            "qualification": qualification_result}
    except Exception as exc:
        if current_policy(identity, path=path) == policy:
            stop(identity, policy, str(exc), path=path)
        raise
