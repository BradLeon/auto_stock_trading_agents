"""Actual new Technical runs, independent replay, immutable reports and gates."""
import copy
import json
import os
import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_phase_f_paired_business import input_fixture, isolated_run, request

from ats.workflow import acceptance_reports as reports, acceptance_matrix as mx, shadow_replay as replay
from ats.workflow import acceptance_assertions as assertions, paired_business as paired
from ats.workflow.acceptance_disposition import dispose
from ats.workflow.isolation import verified_isolation_root
from ats.workflow.business_replay_inputs import Tape, implementation_hashes
from ats.workflow.scoped_routes import RouteIdentity
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.agent.task_projection import ProjectionScope


@pytest.fixture
def actual(business_inputs,isolated,monkeypatch):
    root = verified_isolation_root()
    req = request()
    seed = paired._snapshot()
    tape = Tape()
    provisional = SimpleNamespace(contents={"ruleset_version":{"request":req},"history_state":{"seed":seed}},
        packet=replay.si.ShadowInputPacket("capture","technical","research_read",scope=req["scope"]))
    # Capture only the new business entry. Old execution/results are absent.
    from datetime import datetime
    paired.dispatcher_business(read_input=None,logical_time=datetime.fromisoformat(req["logical_time"]),
        value=provisional,root=root/"capture-new",business_run_id="capture-new",tape=tape)
    scope = {**req["scope"],"consumers":["technical"],"entities":["COHR"],"time_range":{"start":req["logical_time"],"end":req["logical_time"]}}
    identity = RouteIdentity("research","technical","v1",scope)
    def matrix(run_id,criteria=None):
        plan = build_plan(requested_tasks=req["tasks"],scope=ProjectionScope(**req["scope"]),
            trigger=TriggerContext(**req["trigger"]),as_of=req["logical_time"],run_id=run_id,
            config_dir=business_inputs.root,task_inputs=req["task_inputs"])
        return mx.freeze(plan,workflow_id="technical-review",identity=identity,model_execution=True,criteria=criteria)
    contents = {"persistent_refs":[],"projection_hash":{"seed":seed},
        "model_config":{"rows":tape.rows,"dependency_hashes":implementation_hashes()},
        "market_runtime":{"inputs":[r for r in tape.rows.values() if "market_data" in r["request"]["api"]]}}
    for row in tape.reads:
        contents.setdefault(row["surface"],[]).append(row["input"])
    value = replay.freeze_inputs(run_id="frozen",consumer_id="technical",batch_class="research_read",
        scope=scope,logical_eval_time=req["logical_time"],contents=contents,reads=tape.reads)
    input_store = root/"inputs.sqlite"
    replay.save_inputs(value,path=input_store)
    from ats.data.runtime import market_data
    monkeypatch.setattr(market_data,"fetch_close_history_many",lambda *a,**k:pytest.fail("replay consulted source"))
    def run(run_id,criteria=None):
        replay.ReplayReads(value).invoke(reports.dispatch_entry,run_id=run_id,path=input_store,
            matrix=matrix(run_id,criteria),value=value,root=root/run_id)
    run("new")
    run("replay")
    path = root/"reports.sqlite"
    proof = {"input_store":str(input_store),"new_run_id":"new","replay_run_id":"replay"}
    body = reports.record(report_id="good",input_hash=value.input_hash(),proof=proof,actor="fixture",path=path)
    batch = SimpleNamespace(shadow_report_id="good",scope=scope,batch_class="research_read",required_surfaces=())
    return SimpleNamespace(root=root,path=path,proof=proof,value=value,body=body,batch=batch,run=run,matrix=matrix)


def sign(actual,report_id="good"):
    return reports.signoff(report_id=report_id,action="signed_off",actor="test reviewer",authority="fixture-only",
        reason="independent requirement assertions checked",path=actual.path)


def test_actual_new_and_replay_pass_without_any_old_entry(actual,record_property):
    assert all(e["assertions"]["passed"] for e in actual.body["executions"])
    assert not reports.check_batch_report(actual.batch,path=actual.path)[0]
    sign(actual)
    from ats.workflow.shadow_reports import check_batch_report
    assert check_batch_report(actual.batch,path=actual.path) == (True,[])
    record_property("actual_new_entry_report",json.dumps(reports.read("good",path=actual.path)))


@pytest.mark.parametrize("fault",["scope","class","consumer","required","revoked","missing_run","store_missing","fingerprint","config"])
def test_formal_gate_refuses_inapplicable_or_missing_actual_proof(actual,fault,monkeypatch):
    sign(actual)
    batch = copy.copy(actual.batch)
    if fault == "scope": batch.scope = {"kind":"entity","id":"other"}
    elif fault == "class": batch.batch_class = "live_trader"
    elif fault == "consumer": batch.consumer_ids = ["technical","fundamental"]
    elif fault == "required": batch.required_surfaces = ("risk_verdict",)
    elif fault == "revoked":
        reports.signoff(report_id="good",action="revoked",actor="fixture",authority="fixture-only",reason="withdrawn",path=actual.path)
    elif fault == "missing_run":
        # Corruption is a missing-evidence refusal, never an empty bootstrap.
        with sqlite3.connect(actual.proof["input_store"]) as conn:
            conn.execute("DROP TABLE shadow_replay_runs")
    elif fault == "store_missing":
        Path(actual.body["executions"][0]["store"]).rename(actual.root/"unavailable.sqlite")
    elif fault == "fingerprint": monkeypatch.setattr(reports,"fingerprint",lambda:{"changed":"hash"})
    elif fault == "config":
        path = Path(actual.body["matrix"]["plan"]["config_root"])/"technical.yaml"
        path.write_text(path.read_text()+"\n# config drift\n")
    ok,problems = reports.check_batch_report(batch,path=actual.path)
    assert not ok and problems


def test_append_only_retest_needs_new_signature_and_keeps_failed_report(actual):
    actual.run("bad-new")
    actual.run("bad-replay")
    # A real measured violation in the ledger; keep the requirement unchanged.
    with sqlite3.connect(actual.root/"bad-new"/"memory.sqlite") as conn:
        conn.execute("UPDATE task_projection_envelopes SET content_hash='damaged'")
    bad_proof = {**actual.proof,"new_run_id":"bad-new","replay_run_id":"bad-replay"}
    failed = reports.record(report_id="failed",input_hash=actual.value.input_hash(),proof=bad_proof,actor="fixture",path=actual.path,
        legacy_diagnostics=["old entry was broken; historical difference accepted"])
    assert not failed["executions"][0]["assertions"]["passed"]
    with pytest.raises(ValueError,match="failed or untested"):
        sign(actual,"failed")
    original = reports.read("failed",path=actual.path)
    with pytest.raises(ValueError,match="new actual executions"):
        reports.record(report_id="same-runs",input_hash=actual.value.input_hash(),proof=bad_proof,actor="fixture",path=actual.path,supersedes="failed")
    actual.run("repaired-new")
    actual.run("repaired-replay")
    repaired = {**actual.proof,"new_run_id":"repaired-new","replay_run_id":"repaired-replay"}
    new = reports.record(report_id="retested",input_hash=actual.value.input_hash(),proof=repaired,actor="fixture",path=actual.path,supersedes="failed")
    assert new["supersedes"] == "failed"
    assert reports.read("retested",path=actual.path)["status"] == "unsigned"
    assert reports.read("failed",path=actual.path) == original
    sign(actual)
    with sqlite3.connect(actual.path) as conn:
        for table in ("new_entry_reports","new_entry_signoffs"):
            with pytest.raises(sqlite3.IntegrityError,match="append-only"):
                conn.execute("DELETE FROM "+table)
    with pytest.raises(sqlite3.IntegrityError):
        reports.record(report_id="failed",input_hash=actual.value.input_hash(),proof=actual.proof,actor="fixture",path=actual.path)


def test_cli_separate_process_checks_actual_and_revoked_reports(actual,record_property):
    matrix_file = actual.root/"cli-matrix.json"
    matrix_file.write_text(json.dumps(actual.matrix("cli-new").as_row()))
    argv = ["uv","run","--offline","--no-sync","ats","shadow","acceptance-run","--isolation-root",str(actual.root),
        "--input-store",actual.proof["input_store"],"--packet-hash",actual.value.input_hash(),
        "--matrix-file",str(matrix_file),"--run-id","cli-new"]
    execution = subprocess.run(argv,env=os.environ.copy(),capture_output=True,text=True)
    assert execution.returncode == 0,execution.stdout+execution.stderr
    assert json.loads(execution.stdout)["workflow_run_id"] == "cli-new"
    record_property("actual_cli_execution",execution.stdout)
    sign(actual)
    args = ["uv","run","--offline","--no-sync","ats","shadow","acceptance-check","--report-id","good",
        "--db",str(actual.path),"--batch-class","research_read","--scope-json",json.dumps(actual.batch.scope)]
    result = subprocess.run(args,env=os.environ.copy(),capture_output=True,text=True)
    assert result.returncode == 0,result.stdout+result.stderr
    assert json.loads(result.stdout)["valid"]
    record_property("readonly_cli_check",result.stdout)
    reports.signoff(report_id="good",action="revoked",actor="fixture",authority="fixture-only",reason="withdrawn",path=actual.path)
    refused = subprocess.run(args,env=os.environ.copy(),capture_output=True,text=True)
    assert refused.returncode == 1
    assert not json.loads(refused.stdout)["valid"]


def test_untested_required_boundaries_cannot_be_waived_by_legacy_acceptance():
    from test_phase_f_acceptance_matrix import matrix
    result = assertions.evaluate(matrix(("sector-review","fundamental-routine","macro-review","technical-review"),batch_class="trading"),{})
    decision = dispose(result,legacy_diagnostics=["accept all old differences"])
    assert not decision["eligible"] and not decision["legacy_acceptance_can_waive"]
    assert {"risk.contract","approval.contract","attribution.contract"} <= set(decision["measure_and_rerun"])


def test_actual_report_is_used_by_dry_run_and_activation_write(actual,monkeypatch):
    from ats.workflow import batch_manifest as bm, cutover as plane, cutover_wiring as wiring
    from ats.workflow.shadow_reports import check_batch_report
    control = actual.root/"gate-control.sqlite"
    wiring.bootstrap_wired(path=control,actor="fixture")
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced",lambda *a,**k:None)
    batch = bm.CutoverBatch(batch_id="candidate",batch_class="research_read",scope=actual.batch.scope,
        shadow_report_id="good",fallback_route="legacy",fallback_proof="valid",fallback_retired="no",
        fallback_available="yes",fallback_drill_ref="fixture")
    checker = lambda b:check_batch_report(b,path=actual.path)
    before = plane.all_boundaries(control)
    unsigned = bm.dry_run_batch(batch,report_checker=checker,qualification_reader=lambda *a:{"status":"eligible"},
        path=actual.root/"batches.sqlite",cutover_path=control,record=False)
    assert not unsigned.report_citable
    sign(actual)
    signed = bm.dry_run_batch(batch,report_checker=checker,qualification_reader=lambda *a:{"status":"eligible"},
        path=actual.root/"batches.sqlite",cutover_path=control,record=False)
    assert signed.report_citable
    assert plane.all_boundaries(control) == before
    request = plane.ActivationRequest(boundary="projection_read",consumer_id="technical",scope=actual.batch.scope,report_id="good")
    reports.signoff(report_id="good",action="revoked",actor="fixture",authority="fixture-only",reason="withdrawn",path=actual.path)
    with pytest.raises(plane.CutoverError,match="revoked"):
        plane.record_activation(request=request,report_checker=checker,path=control)
    from ats.workflow import read_cutover as rc
    refused = rc.execute_batch(batch,report_checker=checker,
        authorisation=rc.DeploymentAuthorization(reference="fixture",authorised_by="owner",issued_by="owner",
            scope=("technical",),valid_until="2099-01-01T00:00:00+00:00"),
        projection_checker=lambda *a:{"available":True},qualification_reader=lambda *a:{"status":"eligible"},
        path=actual.root/"batches.sqlite",cutover_path=control)
    assert refused.outcome == rc.BATCH_REFUSED
    assert any(s.outcome == rc.REPORT_INVALID for s in refused.scopes)
    assert plane.all_boundaries(control) == before
    with sqlite3.connect(control) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cutover_activations").fetchone()[0] == 0


def test_readonly_inspection_restores_capability_and_never_bootstraps(tmp_path):
    from ats.workflow.isolation import inspect_isolated_records
    from ats.execution import broker_write_guard as guard
    before = dict(os.environ)
    prohibited = guard.is_prohibited()
    missing = tmp_path/"missing"
    with pytest.raises(ValueError,match="root missing"):
        with inspect_isolated_records(missing):
            pytest.fail("missing root inspected")
    assert not missing.exists()
    existing = tmp_path/"existing"
    existing.mkdir()
    with pytest.raises(RuntimeError):
        with inspect_isolated_records(existing):
            assert verified_isolation_root() == existing.resolve()
            assert guard.is_prohibited()
            raise RuntimeError("inspection failed")
    assert dict(os.environ) == before
    assert guard.is_prohibited() == prohibited
    assert list(existing.iterdir()) == []


def test_hashed_signature_with_missing_reviewer_is_invalid(actual):
    state = sign(actual)
    event = {k:v for k,v in state["events"][-1].items() if k not in {"seq","event_hash"}}
    event["actor"] = ""
    event["previous_hash"] = state["events"][-1]["event_hash"]
    with sqlite3.connect(actual.path) as conn:
        conn.execute("INSERT INTO new_entry_signoffs(report_id,action,actor,authority,reason,body_hash,previous_hash,event_hash,recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (*[event[k] for k in ("report_id","action","actor","authority","reason","body_hash","previous_hash")],replay.digest(event),event["recorded_at"]))
    assert not reports.check_batch_report(actual.batch,path=actual.path)[0]
