"""Real scope resolver, SQL owner, read and publication fences; fake gate inputs."""

import copy
import json
import os
import subprocess
from datetime import UTC, datetime

import pytest

from ats.workflow import boundary_evidence as be
from ats.workflow import cutover as plane
from ats.workflow import joint_cutover as joint
from ats.workflow import runtime_reads as reads
from ats.workflow import schedule_runtime as rt
from ats.workflow import scoped_routes as sr
from ats.workflow.cutover_wiring import guard_write
from ats.workflow.isolation import isolated_run
from ats.workflow.run_contracts import TriggerContext


def gates():
    return {
        "compatibility": lambda w, i, r: {
            "workflow": w,
            "identity": i.as_row(),
            "valid": True,
            "reference": "fixture-matrix",
            "edges": [
                {
                    "boundaries": [plane.PROJECTION_READ, plane.DISPATCHER_SCHEDULE],
                    "mixed_compatible": False,
                    "reference": "fixture-format-incompatible",
                }
            ],
        },
        "authorization": lambda m, w: {
            "member": m,
            "workflow": w,
            "valid": True,
            "reference": "fixture-deployment",
        },
        "qualification": lambda i: {"identity": i.as_row(), "status": "eligible"},
        "report": lambda i: {
            "identity": i.as_row(),
            "valid": True,
            "reference": "fixture-signed-report",
        },
        "fallback": lambda i: {
            "identity": i.as_row(),
            "valid": True,
            "reference": "fixture-recovery",
        },
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    with isolated_run("joint", root=tmp_path / "iso") as environment:
        rt.initialize()
        scope = {"kind": "entity", "id": "AAPL"}
        rt.execute(
            "technical-review",
            scope,
            TriggerContext(kind="manual", trigger_id="seed"),
            lambda: {"observed": "seed"},
            owner="legacy",
        )
        point = datetime.now(UTC)
        who = reads.business_identity(
            "technical", kind="entity", scope_id="AAPL", entities=["AAPL"], as_of=point
        )
        request = {
            "batch_id": "joint-1",
            "workflow": "technical-review",
            "members": [
                {
                    "boundary": b,
                    "identity": who.as_row(),
                    "route": "target",
                    "expected_generation": 0,
                }
                for b in (plane.PROJECTION_READ, plane.DISPATCHER_SCHEDULE)
            ],
            "schedules": [
                {
                    "workflow": "technical-review",
                    "scope": scope,
                    "from_owner": "legacy",
                    "to_owner": "dispatcher",
                    "expected_generation": 1,
                    "dispositions": {},
                }
            ],
        }
        # Production evidence/eligibility adapters are deliberately separate;
        # runtime protocol and its actual SQL writes are never replaced.
        monkeypatch.setattr(be, "assert_enforced", lambda *a, **k: None)
        monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "eligible"})
        yield environment, who, request


def run(request, **kw):
    return joint.execute(
        request,
        gates=kw.pop("gates", gates()),
        actor="fixture-operator",
        reason="isolated joint drill",
        mode="isolated",
        **kw,
    )


def test_joint_moves_exact_scope_and_sql_owner_idempotently(setup, record_property):
    _, who, request = setup
    before = rt.snapshot()
    result = run(request)
    assert result["can_run"]
    assert all(sr.resolve_route(m["boundary"], who)["generation"] == 1 for m in request["members"])
    assert rt.current_owner("technical-review", request["schedules"][0]["scope"]) == "dispatcher"
    after = rt.snapshot()
    again = run(request)
    assert again == result and after == rt.snapshot()
    old = TriggerContext(kind="manual", trigger_id="seed")
    assert rt.execute(
        "technical-review",
        request["schedules"][0]["scope"],
        old,
        lambda: pytest.fail("completed trigger reran"),
        owner="dispatcher",
    )["schedule_skipped"]
    with pytest.raises(rt.ScheduleAuthorityError):
        rt.execute(
            "technical-review",
            request["schedules"][0]["scope"],
            TriggerContext(kind="manual", trigger_id="legacy-late"),
            lambda: pytest.fail("old owner ran"),
            owner="legacy",
        )
    record_property("joint_history", json.dumps(result))
    record_property("schedule_before_after", json.dumps([before, after]))


@pytest.mark.parametrize(
    "stage", ["prepared", "first_route_write", "routes_committed", "owners_committed"]
)
def test_real_process_death_never_exposes_runnable_half_migration(setup, stage, record_property):
    environment, who, request = setup
    code = """import json,os,sys
from ats.workflow import joint_cutover as j,boundary_evidence as be
from ats.execution.broker_write_guard import prohibit_broker_writes,REASON_ISOLATED
prohibit_broker_writes(reason_code=REASON_ISOLATED)
be.assert_enforced=lambda *a,**k:None
g={'authorization':lambda m,w:{'member':m,'workflow':w,'valid':True,'reference':'fixture'},
'qualification':lambda i:{'identity':i.as_row(),'status':'eligible'},
'report':lambda i:{'identity':i.as_row(),'valid':True,'reference':'fixture'},
'fallback':lambda i:{'identity':i.as_row(),'valid':True,'reference':'fixture'},
'compatibility':lambda w,i,r:{'workflow':w,'identity':i.as_row(),'valid':True,'reference':'fixture-matrix','edges':[{'boundaries':['projection_read','dispatcher_schedule'],'mixed_compatible':False,'reference':'fixture-incompatible'}]}}
j.execute(json.loads(sys.argv[1]),gates=g,actor='child-fixture',reason='crash drill',mode='isolated',checkpoint=lambda phase:os._exit(73) if phase==sys.argv[2] else None)
"""
    child = subprocess.run(
        ["uv", "run", "--offline", "--no-sync", "python", "-c", code, json.dumps(request), stage],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    assert child.returncode == 73, child.stdout + child.stderr
    assert not joint.inspect(request["batch_id"])["can_run"]
    with pytest.raises(Exception, match="joint.*frozen"):
        reads.gate_read(who)
    with pytest.raises(plane.CutoverError, match="joint.*frozen"):
        rt.execute(
            "technical-review",
            request["schedules"][0]["scope"],
            TriggerContext(kind="manual", trigger_id="frozen"),
            lambda: pytest.fail("half migration executed"),
            owner="dispatcher",
        )
    with pytest.raises(plane.CutoverError):
        guard_write(plane.PROJECTION_READ, what="unbound publication")
    # Unrelated research is not frozen.
    other = reads.business_identity(
        "technical", kind="entity", scope_id="MSFT", entities=["MSFT"], as_of=datetime.now(UTC)
    )
    assert reads.gate_read(other).route == "legacy"
    changed = copy.deepcopy(request)
    changed["members"][0]["route"] = "disabled"
    with pytest.raises(plane.CutoverError, match="request changed"):
        run(changed)
    final = run(request)
    assert final["can_run"] and joint._schedule_state(request["schedules"][0])["generation"] == 2
    assert all(sr.resolve_route(m["boundary"], who)["generation"] == 1 for m in request["members"])
    record_property(
        "actual_crash_recovery",
        json.dumps({"phase": stage, "root": str(environment.root), "result": final}),
    )


@pytest.mark.parametrize("gate", ["authorization", "qualification", "report", "fallback"])
def test_revalidation_failure_after_first_authority_commit_stays_frozen(setup, gate):
    _, who, request = setup

    def die(phase):
        if phase == "routes_committed":
            raise RuntimeError("first authority crash")

    with pytest.raises(RuntimeError):
        run(request, checkpoint=die)
    bad = gates()
    if gate == "authorization":
        bad[gate] = lambda m, w: {
            "member": m,
            "workflow": w,
            "valid": False,
            "reference": "expired",
        }
    elif gate == "qualification":
        bad[gate] = lambda i: {"identity": i.as_row(), "status": "ineligible"}
    else:
        bad[gate] = lambda i: {"identity": i.as_row(), "valid": False, "reference": "revoked"}
    with pytest.raises(plane.CutoverError):
        run(request, gates=bad)
    assert not joint.inspect(request["batch_id"])["can_run"]
    with pytest.raises(Exception, match="joint.*frozen"):
        reads.gate_read(who)


def test_independent_incompatible_request_does_not_expand_authority(setup):
    _, who, request = setup
    request["members"] = request["members"][:1]
    request["schedules"] = []
    with pytest.raises(plane.CutoverError, match="incompatible"):
        run(request)
    assert sr.resolve_route(plane.PROJECTION_READ, who)["generation"] == 0


def test_explicit_cross_version_proof_allows_only_requested_boundary(setup):
    _, who, request = setup
    request["members"] = request["members"][:1]
    request["schedules"] = []
    checks = gates()
    checks["compatibility"] = lambda w, i, r: {
        "workflow": w,
        "identity": i.as_row(),
        "valid": True,
        "reference": "fixture-compatible-matrix",
        "edges": [
            {
                "boundaries": [plane.PROJECTION_READ, plane.DISPATCHER_SCHEDULE],
                "mixed_compatible": True,
                "reference": "fixture-cross-version-read-proof",
            }
        ],
    }
    assert run(request, gates=checks)["can_run"]
    assert sr.resolve_route(plane.PROJECTION_READ, who)["route"] == "target"
    assert sr.resolve_route(plane.DISPATCHER_SCHEDULE, who)["generation"] == 0
    assert rt.current_owner("technical-review", {"kind": "entity", "id": "AAPL"}) == "legacy"


@pytest.mark.parametrize("wrong", ["workflow", "identity", "reference", "valid"])
def test_compatibility_proof_cannot_escape_workflow_scope(setup, wrong):
    _, who, request = setup
    checks = gates()
    original = checks["compatibility"]

    def bad(w, i, r):
        matrix = original(w, i, r)
        matrix[wrong] = False if wrong == "valid" else ""
        return matrix

    checks["compatibility"] = bad
    with pytest.raises(plane.CutoverError, match="compatibility matrix"):
        run(request, gates=checks)
    assert sr.resolve_route(plane.PROJECTION_READ, who)["generation"] == 0


def test_pre_freeze_bound_worker_cannot_publish_after_commit(setup):
    _, who, request = setup
    with reads.bind_read(who):
        run(request)
        with pytest.raises(Exception, match="worker binding"):
            guard_write(plane.ANALYST_OUTPUT, what="stale manual result")


def test_missing_enforcement_and_partial_qualification_do_not_move_any_scope(setup, monkeypatch):
    _, who, request = setup

    def refuses(*a, **k):
        raise plane.CutoverError("not enforced")

    monkeypatch.setattr(be, "assert_enforced", refuses)
    with pytest.raises(plane.CutoverError, match="not enforced"):
        run(request)
    assert sr.resolve_route(plane.PROJECTION_READ, who)["generation"] == 0


def test_rollback_uses_same_explicit_joint_protocol(setup):
    _, who, request = setup
    run(request)
    restore = copy.deepcopy(request)
    restore["batch_id"] = "joint-restore"
    for m in restore["members"]:
        m.update(route="legacy", expected_generation=1)
    restore["schedules"][0].update(
        from_owner="dispatcher", to_owner="legacy", expected_generation=2
    )
    assert run(restore)["can_run"]
    assert joint._schedule_state(restore["schedules"][0])["generation"] == 3
    assert all(sr.resolve_route(m["boundary"], who)["generation"] == 2 for m in restore["members"])


def test_production_gate_requires_immutable_exact_record_not_callback_assertion(setup):
    from datetime import timedelta

    _, who, request = setup
    with pytest.raises(plane.CutoverError, match="recorded exact"):
        joint.execute(
            request, gates=gates(), actor="fixture", reason="no record", mode="production"
        )
    assert sr.resolve_route(plane.PROJECTION_READ, who)["generation"] == 0
    joint.record_authorization(
        request,
        reference="fixture-deployment",
        valid_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        actor="fixture-deployer",
        reason="isolated deployment record",
    )
    changed = copy.deepcopy(request)
    changed["batch_id"] = "different-request"
    with pytest.raises(plane.CutoverError, match="recorded exact"):
        joint.execute(
            changed, gates=gates(), actor="fixture", reason="wrong request", mode="production"
        )
    assert joint.execute(
        request, gates=gates(), actor="fixture", reason="recorded", mode="production"
    )["can_run"]
    import sqlite3

    with (
        joint._write(plane.default_cutover_db_path()) as conn,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        conn.execute("DELETE FROM joint_cutover_authorizations")


def test_isolated_label_without_physical_isolation_cannot_mutate(setup, monkeypatch):
    _, who, request = setup
    monkeypatch.setattr("ats.workflow.isolation.verified_isolation_root", lambda: None)
    with pytest.raises(plane.CutoverError, match="physical isolation"):
        run(request)
    assert sr.resolve_route(plane.PROJECTION_READ, who)["generation"] == 0


def test_one_ineligible_scope_refuses_whole_explicit_group(setup):
    _, who, request = setup
    other = reads.business_identity(
        "technical", kind="entity", scope_id="MSFT", entities=["MSFT"], as_of=datetime.now(UTC)
    )
    for m in list(request["members"]):
        request["members"].append({**m, "identity": other.as_row()})
    request["schedules"].append(
        {**request["schedules"][0], "scope": {"kind": "entity", "id": "MSFT"}}
    )
    checks = gates()
    checks["qualification"] = lambda i: {
        "identity": i.as_row(),
        "status": "eligible" if i == who else "ineligible",
    }
    with pytest.raises(plane.CutoverError, match="qualification"):
        run(request, gates=checks)
    assert sr.resolve_route(plane.PROJECTION_READ, who)["generation"] == 0
    assert rt.current_owner("technical-review", {"kind": "entity", "id": "AAPL"}) == "legacy"


def test_global_emergency_disable_blocks_joint_release(setup):
    _, who, request = setup
    plane.set_route(plane.PROJECTION_READ, "disabled", actor="fixture", reason="emergency")
    with pytest.raises(plane.CutoverError, match="emergency"):
        run(request)
    assert sr.resolve_route(plane.DISPATCHER_SCHEDULE, who)["generation"] == 0


def test_revoked_record_after_routes_commit_cannot_release_fence(setup):
    from datetime import timedelta

    _, who, request = setup
    joint.record_authorization(
        request,
        reference="fixture-deployment",
        valid_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        actor="fixture",
        reason="explicit",
    )

    def revoke(phase):
        if phase == "routes_committed":
            joint.revoke_authorization(
                "fixture-deployment", actor="fixture", reason="revoked during commit"
            )

    with pytest.raises(plane.CutoverError, match="recorded exact"):
        joint.execute(
            request,
            gates=gates(),
            actor="fixture",
            reason="revalidate",
            mode="production",
            checkpoint=revoke,
        )
    assert not joint.inspect(request["batch_id"])["can_run"]
    with pytest.raises(Exception, match="frozen"):
        reads.gate_read(who)


def test_production_primitive_cannot_bypass_workflow_matrix(setup):
    _, who, _ = setup
    with pytest.raises(plane.CutoverError, match="compatibility coordinator"):
        sr.transition_route(
            plane.PROJECTION_READ,
            who,
            "target",
            actor="fixture",
            reason="bypass",
            expected_generation=0,
        )
    assert sr.resolve_route(plane.PROJECTION_READ, who)["generation"] == 0


def test_matrix_revocation_after_routes_commit_keeps_group_frozen(setup):
    _, who, request = setup
    checks = gates()
    original = checks["compatibility"]
    revoked = False

    def matrix(w, i, r):
        result = original(w, i, r)
        result["valid"] = not revoked
        return result

    checks["compatibility"] = matrix

    def revoke(phase):
        nonlocal revoked
        if phase == "routes_committed":
            revoked = True

    with pytest.raises(plane.CutoverError, match="compatibility matrix"):
        run(request, gates=checks, checkpoint=revoke)
    assert not joint.inspect(request["batch_id"])["can_run"]
    with pytest.raises(Exception, match="frozen"):
        reads.gate_read(who)
