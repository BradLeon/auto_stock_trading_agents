import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from test_phase_f_safe_reads import activate, isolated, scope, seed_performance  # noqa: F401

from ats.agents.chief import assemble
from ats.execution.state_api import get_internal_state
from ats.workflow import cutover as co
from ats.workflow import read_recovery as recovery
from ats.workflow import runtime_reads as rr
from ats.workflow.consumer_reads import (
    InternalFallback,
    consume_read,
    implementation_hash,
    revoke_fallback,
    trace_reads,
)
from ats.workflow.scoped_routes import RouteIdentity, resolve_route, route_history


def register(identity, tmp_path, strategy="stop", reader=None, target=None):
    proof = {"version": recovery.VERSION, "identity": identity.as_row(), "strategy": strategy,
        "dependency_hashes": recovery.dependency_hashes(identity.consumer_id),
        "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    if reader:
        target = target or identity
        accepted = tmp_path / "accepted.json"
        accepted.write_text(json.dumps({"identity": target.as_row(), "target_version": "accepted-v1", "status": "passed"}))
        proof.update(target_version="accepted-v1", target_identity=target.as_row(),
            target_identifier=reader.__module__ + "." + reader.__qualname__,
            implementation_sha256=implementation_hash(reader), accepted_report_ref=str(accepted),
            accepted_report_sha256=hashlib.sha256(accepted.read_bytes()).hexdigest())
    reference = tmp_path / (identity.key + "-recovery.json")
    reference.write_text(json.dumps(proof))
    recovery.register(identity, reference=reference, sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
                      actor="fixture", reason="pre-run accepted strategy")
    return reference


def test_actual_chief_safe_stop_cannot_be_bypassed_and_preserves_state(isolated, tmp_path, monkeypatch, record_property):  # noqa: F811
    identity = scope()
    seed_performance(isolated)
    activate(identity, tmp_path, monkeypatch)
    register(identity, tmp_path)
    before = isolated.conn.execute("SELECT count(*) FROM performance").fetchone()[0]
    history = route_history(co.PROJECTION_READ, identity)
    with pytest.raises(recovery.ReadStopped):
        assemble._track_record_block(read_scope=identity.scope, read_fallback=InternalFallback(isolated))
    assert recovery.current_policy(identity)["stopped"]
    record_property("stopped_scope", json.dumps(recovery.current_policy(identity)))
    # Even if origin qualification later changes, no implicit resurrection.
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **kw: {"status": "eligible"})
    with pytest.raises(recovery.ReadStopped):
        rr.gate_read(identity)
    assert isolated.conn.execute("SELECT count(*) FROM performance").fetchone()[0] == before
    assert route_history(co.PROJECTION_READ, identity) == history
    assert co.read_boundary(co.LIVE_TRADER).route == "disabled"
    with sqlite3.connect(co.default_cutover_db_path()) as conn, pytest.raises(
            sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM read_recovery_events")


def test_new_version_actual_state_read_is_qualified_once_returned_and_traced(isolated, tmp_path, monkeypatch, record_property):  # noqa: F811
    identity = scope()
    seed_performance(isolated)
    activate(identity, tmp_path, monkeypatch)
    target = RouteIdentity(identity.domain_id, identity.consumer_id, "accepted-contract", identity.scope)
    calls = []
    def reader():
        calls.append("new")
        return get_internal_state(isolated)
    register(identity, tmp_path, "new_version", reader, target)
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **kw:
        {"status": "eligible" if kw["contract_version"] == target.contract_version else "ineligible"})
    def old():
        pytest.fail("old business called during new-version recovery")
    with trace_reads() as rows:
        value = consume_read(identity, target_reader=old, legacy_reader=old, legacy_identifier="broken-old",
            recovery_readers={"accepted-v1": reader}, usable=lambda v: v.portfolio["net_liquidation"] == 1_000_000,
            refs=lambda v: list(v.section_as_of.values()))
    assert value.portfolio["net_liquidation"] == 1_000_000 and calls == ["new"]
    assert rows[-1]["recovery_version"] == "accepted-v1" and rows[-1]["route"] == "target"
    record_property("actual_recovery_reads", json.dumps(rows))
    record_property("actual_recovered_state", value.model_dump_json())
    assert resolve_route(co.PROJECTION_READ, identity)["route"] == "target"
    calls.clear()
    text = assemble._track_record_block(read_scope=identity.scope,
        read_fallback=InternalFallback(isolated, recovery_readers={"accepted-v1": reader}))
    assert "1,000,000" in text and calls == ["new"]


@pytest.mark.parametrize("failure", ["unqualified", "revoked", "unreadable", "missing_adapter", "expired", "tampered", "retired"])
def test_new_version_failure_latches_stop_without_old_fallback(isolated, tmp_path, monkeypatch, failure):  # noqa: F811
    identity = scope()
    seed_performance(isolated)
    activate(identity, tmp_path, monkeypatch)
    target = RouteIdentity(identity.domain_id, identity.consumer_id, "accepted-contract", identity.scope)
    def reader():
        if failure == "unreadable":
            raise RuntimeError("new version failed")
        if failure == "revoked":
            revoke_fallback(identity, reference, actor="fixture", reason="withdraw during read")
        return get_internal_state(isolated)
    reference = register(identity, tmp_path, "new_version", reader, target)
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **kw:
        {"status": "eligible" if failure != "unqualified" and kw["contract_version"] == target.contract_version
                   else "ineligible"})
    if failure == "expired":
        monkeypatch.setattr(recovery, "now", lambda tz: datetime.now(tz) + timedelta(hours=2))
    if failure == "tampered":
        reference.write_text("{}")  # Also proves immutable proof tampering refuses.
    if failure == "retired":
        monkeypatch.setattr("ats.workflow.cutover_routing.is_retired", lambda *a, **kw: True)
    def forbidden():
        pytest.fail("unsafe recovery touched current/old reader")
    with pytest.raises(recovery.ReadStopped):
        consume_read(identity, target_reader=forbidden, legacy_reader=forbidden, legacy_identifier="old",
            recovery_readers={} if failure == "missing_adapter" else {"accepted-v1": reader},
            usable=lambda v: True, refs=lambda v: list(v.section_as_of.values()))
    assert recovery.current_policy(identity)["stopped"]


def test_stop_does_not_block_unrelated_scope_and_fences_bound_publication(isolated, tmp_path, monkeypatch):  # noqa: F811
    from ats.workflow.cutover_wiring import guard_approval_write
    identity = scope()
    activate(identity, tmp_path, monkeypatch)
    register(identity, tmp_path)
    # Start a worker while qualified; stop is triggered while that worker lives.
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **kw: {"status": "eligible"})
    with rr.bind_read(identity):
        monkeypatch.setattr("ats.data.assurance.qualification", lambda **kw: {"status": "ineligible"})
        with pytest.raises(recovery.ReadStopped):
            rr.gate_read(identity)
        with pytest.raises(recovery.ReadStopped):
            guard_approval_write(what="dependent approval")
    unrelated = scope("risk")
    assert recovery.current_policy(unrelated) is None
    assert rr.gate_read(unrelated).route == "legacy"


def test_strategy_change_does_not_revive_old_bound_worker(isolated, tmp_path, monkeypatch):  # noqa: F811
    from ats.workflow.cutover_wiring import guard_approval_write
    identity = scope()
    activate(identity, tmp_path, monkeypatch)
    register(identity, tmp_path)
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **kw: {"status": "eligible"})
    with rr.bind_read(identity):
        register(identity, tmp_path)  # Explicitly changes the recovery generation.
        with pytest.raises(recovery.ReadStopped, match="old bound worker"):
            guard_approval_write(what="late approval")
    with rr.bind_read(identity):
        assert guard_approval_write(what="current worker").allowed


def test_unreadable_authority_never_defaults_to_no_recovery_policy(tmp_path):
    with pytest.raises(co.CutoverError, match="authority unreadable"):
        recovery.current_policy(scope(), path=tmp_path / "absent.sqlite")
