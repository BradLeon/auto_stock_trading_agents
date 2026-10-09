"""Scope authority tests; external proof/qualification adapters are explicit fakes.

These prove the real route store and runtime resolver, not CLI wiring or production
qualification. Tests without the fake enforcement verifier must fail closed.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC
from pathlib import Path

import pytest

from ats.workflow import boundary_evidence as be
from ats.workflow import cutover as co
from ats.workflow import cutover_routing as cr
from ats.workflow import cutover_wiring as cw
from ats.workflow import scoped_routes as sr


def identity(consumer="layer", **scope):
    return sr.RouteIdentity("ai_hardware", consumer, "v1", {
        "kind": "sector", "id": "ai_hardware", "entities": ["NVDA", "AMD"],
        "time_range": {"start": "2026-10-07T00:00:00Z", "end": "2026-10-08T00:00:00Z"},
        **scope})


@pytest.fixture
def authority(tmp_path):
    from ats.workflow.isolation import isolated_run
    with isolated_run("scope-authority",root=tmp_path/"scope_authority") as environment:
        path=environment.path_for("ATS_CUTOVER_DB")
        cw.bootstrap_wired(path=path)
        yield path


def adapters(ineligible=()):
    return {"qualification": lambda i: {"identity": i.as_row(),
                "status": "ineligible" if i.consumer_id in ineligible else "eligible"},
                "report": lambda i: {"identity": i.as_row(), "valid": True, "reference": "local-report"},
                "fallback": lambda i: {"identity": i.as_row(), "valid": True, "reference": "local-drill"}}


@pytest.fixture
def fake_enforcement(monkeypatch):
    # Only route persistence tests replace this separate not-yet-wired service.
    monkeypatch.setattr(be, "assert_enforced", lambda *args, **kwargs: None)


def move(path, who, **overrides):
    kwargs = dict(actor="test-operator", reason="isolated scope drill", expected_generation=0,
                  **adapters(), path=path, mode="isolated")
    kwargs.update(overrides)
    return sr.transition_route(co.PROJECTION_READ, who, co.ROUTE_TARGET, **kwargs)


def test_bootstrap_is_declared_and_cannot_activate(authority):
    for state in co.all_boundaries(authority).values():
        assert state.declared and not state.wired and not state.as_row()["enforced"]
    with pytest.raises(co.CutoverError, match="not enforced"):
        move(authority, identity())
    assert sr.resolve_route(co.PROJECTION_READ, identity(), path=authority)["route"] == "legacy"
    assert sr.route_history(co.PROJECTION_READ, identity(), path=authority) == []


def test_old_wired_boolean_is_not_enforcement(authority):
    with sqlite3.connect(authority) as conn:
        conn.execute("UPDATE cutover_boundary_state SET wired=1")
    assert not co.read_boundary(co.PROJECTION_READ, authority).wired
    with pytest.raises(co.CutoverError, match="exact business"):
        co.assert_wired(co.PROJECTION_READ, authority)


def test_all_real_callpoints_exist_and_missing_guards_remain_visible():
    report = be.declaration_report()
    assert set(report) == set(co.SIX_BOUNDARIES)
    assert sum(len(points) for points in report.values()) == 31
    assert all(not p["enforced"] for points in report.values() for p in points)
    assert all(p["guard_present"] for p in report[co.LIVE_TRADER])
    assert all(p["guard_present"] for p in report[co.APPROVAL_LIFECYCLE])


def test_nonempty_bootstrap_proof_string_cannot_pass(authority):
    with pytest.raises(co.CutoverError, match="every applicable"):
        be.record_enforcement(co.PROJECTION_READ, identity(), mode="production", bundle={
            "identity": identity().as_row(), "mode": "production", "call_points": {}}, path=authority)


def test_fake_business_evidence_cannot_hide_missing_guard(authority):
    points = be.CALL_POINTS[co.DISPATCHER_SCHEDULE]
    bundle = {"identity": identity().as_row(), "mode": "production", "call_points": {
        p.site: {"writer": p.writer, "positive_test": "passed", "negative_test": "passed-too"}
        for p in points}}
    with pytest.raises(co.CutoverError, match="actual guard missing"):
        be.record_enforcement(co.DISPATCHER_SCHEDULE, identity(), mode="production",
                              bundle=bundle, path=authority)


@pytest.mark.parametrize("changes", [{"consumer_id": "sector"}, {"domain_id": "other"},
                                      {"contract_version": "v2"}])
def test_route_identity_includes_domain_consumer_version(changes):
    args = identity().as_row()
    args.update(changes)
    assert sr.RouteIdentity(**args).key != identity().key


@pytest.mark.parametrize("scope", [{"entities": []}, {"time_range": {}},
                                   {"time_range": {"start": "2026-10-07", "end": "2026-10-08"}},
                                   {"event_id": "earnings"}, {"kind": ""},
                                   {"time_range": {"start": "2026-10-09T00:00:00Z", "end": "2026-10-08T00:00:00Z"}}])
def test_incomplete_or_ambiguous_business_scopes_refuse(scope):
    with pytest.raises(ValueError):
        identity(**scope)


def test_products_list_is_not_business_scope():
    with pytest.raises(ValueError, match="kind/id"):
        sr.RouteIdentity("ai_hardware", "layer", "v1", {"products": ["LAYER_ANALYSIS"]})


def test_normalization_and_caller_mutation():
    original = identity().as_row()
    original["scope"]["entities"] = ["NVDA", "AMD", "NVDA"]
    original["scope"]["time_range"]["start"] = "2026-10-07T08:00:00+08:00"
    normalized = sr.RouteIdentity(**original)
    assert normalized.key == identity().key
    original["scope"]["entities"].append("MU")
    returned = normalized.scope
    returned["entities"].append("MU")
    assert normalized.key == identity().key


def test_partial_qualification_moves_only_layer_and_persists(authority, fake_enforcement):
    gates = adapters(ineligible=("sector",))
    assert move(authority, identity(), **gates)["route"] == "target"
    with pytest.raises(co.CutoverError, match="qualification refuses"):
        move(authority, identity("sector"), **gates)
    assert sr.resolve_route(co.PROJECTION_READ, identity("sector"), path=authority)["route"] == "legacy"
    assert sr.resolve_route(co.PROJECTION_READ, identity(entities=["MU"]), path=authority)["route"] == "legacy"
    assert co.read_boundary(co.PROJECTION_READ, authority).route == "legacy"
    assert co.read_boundary(co.DISPATCHER_SCHEDULE, authority).route == "legacy"
    code = """import json,sys
from ats.workflow.scoped_routes import RouteIdentity,resolve_route
i=RouteIdentity(**json.loads(sys.argv[2]))
print(json.dumps(resolve_route('projection_read',i,path=sys.argv[1])))
"""
    child = subprocess.run([sys.executable, "-c", code, str(authority), json.dumps(identity().as_row())],
                           check=True, capture_output=True, text=True, env=os.environ.copy())
    assert json.loads(child.stdout)["route"] == "target"
    assert len(sr.route_history(co.PROJECTION_READ, identity(), path=authority)) == 1


@pytest.mark.parametrize("name", ["qualification", "report", "fallback"])
def test_gates_receive_and_must_return_same_exact_scope(authority, fake_enforcement, name):
    foreign = identity(entities=["MU"])
    fake = lambda i: {"identity": foreign.as_row(), "status": "eligible", "valid": True, "reference": "x"}
    with pytest.raises(co.CutoverError, match="scope mismatch"):
        move(authority, identity(), **{name: fake})
    assert not sr.route_history(co.PROJECTION_READ, identity(), path=authority)


@pytest.mark.parametrize("name", ["qualification", "report", "fallback"])
def test_missing_gate_refuses(authority, fake_enforcement, name):
    with pytest.raises(co.CutoverError, match="checker missing"):
        move(authority, identity(), **{name: None})


def test_stale_generation_cannot_overwrite_new_route(authority, fake_enforcement):
    move(authority, identity())
    with pytest.raises(co.CutoverError, match="generation changed"):
        move(authority, identity())
    assert len(sr.route_history(co.PROJECTION_READ, identity(), path=authority)) == 1


def test_target_scope_rollback_requires_proof_and_keeps_history(authority, fake_enforcement):
    move(authority, identity())
    args = {"actor": "operator", "reason": "scope fallback", "expected_generation": 1, "path": authority,"mode":"isolated"}
    with pytest.raises(co.CutoverError, match="fallback checker missing"):
        sr.transition_route(co.PROJECTION_READ, identity(), "legacy", **args)
    sr.transition_route(co.PROJECTION_READ, identity(), "legacy", fallback=adapters()["fallback"], **args)
    history = sr.route_history(co.PROJECTION_READ, identity(), path=authority)
    assert [(r["from_route"], r["to_route"]) for r in history] == [("legacy", "target"), ("target", "legacy")]
    with sqlite3.connect(authority) as conn, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM scoped_cutover_history")


def test_failure_of_history_write_rolls_back_state(authority, fake_enforcement):
    # Install actual SQLite fault, after schema initialization with no target activation.
    sr.transition_route(co.PROJECTION_READ, identity(), "legacy", actor="test", reason="initialize",
                        expected_generation=0, path=authority,mode="isolated")
    with sqlite3.connect(authority) as conn:
        conn.execute("CREATE TRIGGER fail_history BEFORE INSERT ON scoped_cutover_history "
                     "BEGIN SELECT RAISE(ABORT, 'fault injection'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fault injection"):
        move(authority, identity(), expected_generation=1)
    assert sr.resolve_route(co.PROJECTION_READ, identity(), path=authority)["route"] == "legacy"


def test_global_target_cannot_promote_absent_scopes(authority):
    co.set_route(co.PROJECTION_READ, "target", actor="test", reason="old flag", path=authority)
    assert sr.resolve_route(co.PROJECTION_READ, identity(), path=authority)["route"] == "legacy"
    with pytest.raises(cr.RouteUnavailable, match="global target flag"):
        cr.read_route(consumer_id="layer", target_boundary_active=True, path=authority)


def test_missing_or_corrupt_authority_is_not_created(tmp_path):
    target = tmp_path / "missing.sqlite"
    with pytest.raises(co.CutoverError, match="unreadable"):
        sr.resolve_route(co.PROJECTION_READ, identity(), path=target)
    assert not target.exists()
    target.write_text("broken")
    with pytest.raises(co.CutoverError, match="unreadable"):
        sr.resolve_route(co.PROJECTION_READ, identity(), path=target)


def test_disable_stops_persisted_target_scope(authority, fake_enforcement):
    move(authority, identity())
    co.set_route(co.PROJECTION_READ, "disabled", actor="test", reason="stop", path=authority)
    with pytest.raises(cr.RouteUnavailable, match="disabled"):
        cr.read_route(consumer_id="layer", identity=identity(), path=authority)


def test_runtime_resolver_uses_scope_and_rechecks_qualification(authority, fake_enforcement, monkeypatch):
    move(authority, identity())
    seen = []
    def qualification(**kwargs):
        seen.append(kwargs)
        return {"status": "eligible"}
    monkeypatch.setattr("ats.data.assurance.qualification", qualification)
    monkeypatch.setattr(cr, "_consumer_contract", lambda _: {"domain_id": "ai_hardware", "contract_version": "v1"})
    assert cr.read_route(consumer_id="layer", identity=identity(), path=authority).route == "target"
    assert seen[0]["scope"] == identity().scope
    assert cr.read_route(consumer_id="sector", identity=identity("sector"), path=authority).route == "legacy"
    assert len(seen) == 1
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **_: {"status": "ineligible"})
    with pytest.raises(cr.RouteUnavailable, match="fallback proof business scope mismatch"):
        cr.read_route(consumer_id="layer", identity=identity(), path=authority,
                      fallback_check=lambda: {"verdict": "ok"})
    decision = cr.read_route(consumer_id="layer", identity=identity(), path=authority,
                            fallback_check=lambda: {"verdict": "ok", "identity": identity().as_row(),
                                "reference": "synthetic-drill", "proof_valid": True,
                                "retired": False, "available": True})
    assert decision.route == "legacy"


def test_owner_mapping_validates_actual_task_and_responsibility(tmp_path):
    for boundary in cw.OWNER_BOUNDARIES:
        assert cw.valid_owner_mapping(boundary)
        assert not cw.valid_owner_mapping(replace(boundary, migration_task="F.6.2"))
        assert not cw.valid_owner_mapping(replace(boundary, target_owner="nonempty.placeholder"))
    changed = tmp_path / "tasks.md"
    changed.write_text("- [ ] 9.3 Chief 其他工作\n- [ ] 9.6 Sector CLI 其他工作\n")
    assert not cw.valid_owner_mapping(cw.OWNER_BOUNDARIES[0], tasks_path=changed)


def test_old_formal_executor_refuses_bootstrap_only(authority, tmp_path):
    from datetime import datetime, timedelta

    from ats.workflow import batch_manifest as bm
    from ats.workflow import read_cutover as rc
    batch = bm.CutoverBatch(batch_id="isolated-batch", batch_class=bm.RESEARCH_READ,
        owner="test", scope={"consumers": ["layer"]}, old_route="legacy", new_route="target",
        observation_window="1 day", success_criteria="match", stop_conditions="failure",
        fallback_route="legacy", fallback_proof="valid", fallback_drill_ref="local-drill")
    auth = rc.DeploymentAuthorization(reference="isolated-auth", authorised_by="test", issued_by="test",
        scope=("layer",), valid_until=(datetime.now(UTC) + timedelta(days=1)).isoformat())
    result = rc.execute_batch(batch, authorisation=auth, apply=True, path=tmp_path / "batches.sqlite",
                              cutover_path=authority, qualification_reader=lambda *_: {"status": "eligible"})
    assert result.outcome == rc.BATCH_REFUSED and not result.changed
    assert result.scopes[0].outcome == rc.BOUNDARY_UNWIRED


def _cancel_transport(tmp_path, monkeypatch, account):
    from types import SimpleNamespace

    from phase_f_broker_harness import FakeIB, broker_for

    from ats.execution import broker_write_guard as guard
    from ats.execution import route_registry as rr
    monkeypatch.setenv("ATS_ROUTE_REGISTRY_PATH", str(tmp_path / "broker.sqlite"))
    guard.reset_for_tests()
    rr.install_route("A", environment="paper", account="DU1")
    guard.grant_write("A", 1, environment="paper", account="DU1")
    ib = FakeIB()
    order = SimpleNamespace(account=account, orderId=7)
    ib.openTrades = lambda: [SimpleNamespace(order=order, contract=SimpleNamespace(symbol="AMD"))]
    ib.cancelOrder = lambda order: ib.accepted.append(order.orderId)
    return guard, ib, broker_for(ib)


def test_business_cancel_positive(tmp_path, monkeypatch, record_property):
    from phase_f_broker_harness import record_boundary_proof
    guard, ib, broker = _cancel_transport(tmp_path, monkeypatch, "DU1")
    try:
        assert broker.cancel_all() == ["7"]
        assert ib.accepted == [7]
        record_boundary_proof(record_property, "ats.broker.ibkr.IBKRBroker.cancel_all", "positive")
    finally:
        guard.reset_for_tests()


def test_business_cancel_wrong_account_negative(tmp_path, monkeypatch, record_property):
    from phase_f_broker_harness import record_boundary_proof
    guard, ib, broker = _cancel_transport(tmp_path, monkeypatch, "DU2")
    try:
        with pytest.raises(guard.BrokerWriteProhibited):
            broker.cancel_all()
        assert not ib.accepted
        record_boundary_proof(record_property, "ats.broker.ibkr.IBKRBroker.cancel_all", "negative")
    finally:
        guard.reset_for_tests()


@pytest.fixture(scope="module")
def real_execution_bundle(tmp_path_factory):
    # Run the actual selected business-entry tests. This is local fake transport
    # evidence for isolated mode, never a production cutover qualification.
    folder = tmp_path_factory.mktemp("entry-proof")
    junit = folder / "business.junit.xml"
    tests = {
        "_submit": ("test_phase_f_broker_enforcement.test_valid_session_sets_explicit_order_account_and_preserves_reads",
                    "test_phase_f_broker_enforcement.test_freeze_between_batch_check_and_actual_write_is_rechecked"),
        "cancel_all": ("test_phase_f_scoped_routes.test_business_cancel_positive",
                       "test_phase_f_scoped_routes.test_business_cancel_wrong_account_negative"),
    }
    selectors = [f"tests/{name.split('.')[0]}.py::{name.split('.')[1]}"
                 for pair in tests.values() for name in pair]
    subprocess.run([sys.executable, "-m", "pytest", *selectors, "-q", "-o", "junit_family=xunit1", f"--junitxml={junit}"],
                   check=True, capture_output=True, text=True)
    from ats.config import REPO_ROOT
    dependencies = ["src/ats/broker/ibkr.py", "src/ats/execution/broker_write_guard.py",
        "src/ats/execution/route_registry.py", "src/ats/execution/route_arbitration.py",
        "src/ats/workflow/boundary_evidence.py", "src/ats/workflow/scoped_routes.py",
        "tests/test_phase_f_broker_enforcement.py", "tests/test_phase_f_scoped_routes.py",
        "tests/phase_f_broker_harness.py"]
    hashes = {str(REPO_ROOT / name): hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest()
              for name in dependencies}
    return {"identity": identity("trader").as_row(), "mode": "isolated", "call_points": {
        p.site: {"writer": p.writer, "entry_exercised": p.site, "negative_side_effect_count": 0,
                 "positive_test": "tests." + tests[p.site.split(".")[-1]][0],
                 "negative_test": "tests." + tests[p.site.split(".")[-1]][1],
                 "junit_path": str(junit), "junit_sha256": hashlib.sha256(junit.read_bytes()).hexdigest(),
                 "source_hashes": hashes} for p in be.CALL_POINTS[co.LIVE_TRADER]}}


def test_real_business_proof_is_exact_scope_mode_and_code_bound(authority, real_execution_bundle):
    who = identity("trader")
    be.record_enforcement(co.LIVE_TRADER, who, mode="isolated", bundle=real_execution_bundle, path=authority)
    be.assert_enforced(co.LIVE_TRADER, identity=who, mode="isolated", path=authority)
    for other, mode in [(identity("trader", entities=["MU"]), "isolated"), (who, "production")]:
        with pytest.raises(co.CutoverError, match="not enforced"):
            be.assert_enforced(co.LIVE_TRADER, identity=other, mode=mode, path=authority)


@pytest.mark.parametrize("fault", ["missing_junit", "bad_hash", "missing_source", "side_effect", "missing_negative"])
def test_incomplete_or_false_entry_proof_refuses(authority, real_execution_bundle, fault):
    bundle = json.loads(json.dumps(real_execution_bundle))
    proof = next(iter(bundle["call_points"].values()))
    if fault == "missing_junit":
        proof["junit_path"] = "/missing/junit.xml"
    elif fault == "bad_hash":
        proof["junit_sha256"] = "old"
    elif fault == "missing_source":
        proof["source_hashes"] = {}
    elif fault == "side_effect":
        proof["negative_side_effect_count"] = 1
    else:
        proof["negative_test"] = "test_phase_f_scoped_routes.no_such_test"
    with pytest.raises(co.CutoverError, match="invalid enforcement"):
        be.record_enforcement(co.LIVE_TRADER, identity("trader"), mode="isolated", bundle=bundle, path=authority)


def test_enforcement_artifact_drift_closes_previously_verified_scope(authority, real_execution_bundle):
    be.record_enforcement(co.LIVE_TRADER, identity("trader"), mode="isolated",
                          bundle=real_execution_bundle, path=authority)
    artifact = Path(next(iter(real_execution_bundle["call_points"].values()))["junit_path"])
    original = artifact.read_bytes()
    try:
        artifact.write_bytes(original + b"\n")
        with pytest.raises(co.CutoverError, match="not enforced"):
            be.assert_enforced(co.LIVE_TRADER, identity=identity("trader"), mode="isolated", path=authority)
    finally:
        artifact.write_bytes(original)


def test_real_cli_cannot_switch_bootstrap_declarations(authority, capsys):
    from ats.runtime.cli import main
    assert main(["cutover", "set-route", "--boundary", "projection_read", "--route", "target",
                 "--actor", "test", "--reason", "attempt", "--db", str(authority)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["changed"] is False and "shadow report ID is required" in result["problem"]
    assert co.read_boundary(co.PROJECTION_READ, authority).route == "legacy"


def test_event_and_time_changes_are_independent_scopes():
    base = identity(event_id="earnings", event_version="1")
    assert base.key != identity(event_id="earnings", event_version="2").key
    assert base.key != identity(event_id="other", event_version="1").key
    assert identity().key != identity(time_range={
        "start": "2026-10-06T00:00:00Z", "end": "2026-10-08T00:00:00Z"}).key


def test_enforcement_source_dependency_drift_closes_scope(authority, real_execution_bundle, tmp_path):
    bundle = json.loads(json.dumps(real_execution_bundle))
    dependency = tmp_path / "policy.txt"
    dependency.write_text("v1")
    for proof in bundle["call_points"].values():
        proof["source_hashes"][str(dependency)] = hashlib.sha256(dependency.read_bytes()).hexdigest()
    be.record_enforcement(co.LIVE_TRADER, identity("trader"), mode="isolated", bundle=bundle, path=authority)
    dependency.write_text("v2")
    with pytest.raises(co.CutoverError, match="not enforced"):
        be.assert_enforced(co.LIVE_TRADER, identity=identity("trader"), mode="isolated", path=authority)


def test_two_processes_cannot_commit_same_generation(authority):
    code = '''import json,sys
from ats.workflow import scoped_routes as sr,boundary_evidence as be
from ats.workflow.cutover import CutoverError
from ats.execution.broker_write_guard import prohibit_broker_writes,REASON_ISOLATED
prohibit_broker_writes(reason_code=REASON_ISOLATED)
be.assert_enforced=lambda *a,**k: None  # separate evidence adapter explicitly simulated
i=sr.RouteIdentity(**json.loads(sys.argv[2]))
g=lambda i:{'identity':i.as_row(),'status':'eligible','valid':True,'reference':'isolated'}
try:
    sr.transition_route('projection_read',i,'target',actor='child',reason='isolated race',
        expected_generation=0,qualification=g,report=g,fallback=g,path=sys.argv[1],mode='isolated')
    print('committed')
except CutoverError:
    print('refused')
'''
    args = ["uv","run","--offline","--no-sync","python", "-c", code, str(authority), json.dumps(identity().as_row())]
    children = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(2)]
    results = [child.communicate(timeout=30) for child in children]
    assert all(child.returncode == 0 for child in children), results
    assert sorted(out.strip() for out, _ in results) == ["committed", "refused"]
    assert len(sr.route_history(co.PROJECTION_READ, identity(), path=authority)) == 1


def test_fake_mode_label_cannot_reuse_isolated_test_as_production(authority, real_execution_bundle):
    bundle = json.loads(json.dumps(real_execution_bundle))
    bundle["mode"] = "production"
    with pytest.raises(co.CutoverError, match="executed test entry/scope/mode"):
        be.record_enforcement(co.LIVE_TRADER, identity("trader"), mode="production", bundle=bundle, path=authority)


def test_scope_inventory_is_visible_in_real_cli(authority, fake_enforcement, capsys):
    from ats.runtime.cli import main
    move(authority, identity())
    assert main(["cutover", "state", "--db", str(authority)]) == 0
    result = json.loads(capsys.readouterr().out)
    scopes = result[co.PROJECTION_READ]["scoped_routes"]
    assert len(scopes) == 1 and scopes[0]["identity"] == identity().as_row()
    assert result[co.PROJECTION_READ]["route"] == "legacy"
    assert all(not result[b]["scoped_routes"] for b in co.SIX_BOUNDARIES if b != co.PROJECTION_READ)
