"""Recoverable shadow foundation, synthetic inputs; no production acceptance."""
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats.data.consumer_api import ConsumerInput
from ats.workflow import batch_manifest as bm
from ats.workflow import cutover as plane
from ats.workflow import cutover_wiring as wiring
from ats.workflow import read_cutover as rc
from ats.workflow import shadow_compare as compare
from ats.workflow import shadow_inputs as si
from ats.workflow import shadow_replay as replay
from ats.workflow import shadow_reports as reports
from ats.workflow.isolation import isolated_run


@pytest.fixture
def isolated(tmp_path):
    with isolated_run("shadow-foundation", root=tmp_path / "iso"):
        yield tmp_path / "iso"


def frozen():
    clock = datetime.now(UTC).isoformat()
    def reader(consumer, product, *, scope, **kwargs):
        now = datetime.fromisoformat(clock)
        return ConsumerInput(contract_version="target-dataflow-v2", consumer=consumer,
            product=product, owner="runtime", input_mode="runtime", scope=scope,
            as_of=now, queried_at=now, source_as_of=[now.isoformat()], status="complete",
            completeness="complete", input_refs=["source:bidask"], fallback="reject",
            payload={"symbol": "AAPL", "currency": "USD", "source": "synthetic",
                     "source_as_of": clock, "queried_at": clock, "price_kind": "bid_ask",
                     "bid": 100, "ask": 100.1, "session": "regular", "market_data_mode": "live",
                     "adjusted": False, "min_size": 1, "size_increment": 1, "min_tick": .01})
    capture = replay.CaptureReads(reader)
    for stage in ("preapproval_normalization", "approved_execution_check"):
        capture.read_input("trader", "MARKET_DATA", stage=stage,
                           scope={"kind": "execution_price", "entity": "AAPL", "cycle_id": "cycle",
                                  "currency": "USD", "purpose": stage})
    contents = {surface: {"version": "synthetic-v1"} for surface in si.ALL_SURFACES
                if surface != si.LOGICAL_EVAL_TIME}
    contents.update(capture.contents())
    return replay.freeze_inputs(run_id="capture", consumer_id="trader", batch_class="trading",
                                scope={"entities": ["AAPL"]}, logical_eval_time=clock,
                                contents=contents, reads=capture.reads)


def test_cross_process_replay_has_exact_stage_quotes_without_provider(isolated):
    value = frozen()
    path = isolated / "inputs.sqlite"
    identity = replay.save_inputs(value, path=path)
    program = """
import json,sys
from ats.execution.broker_write_guard import install_from_environment
install_from_environment()
from ats.workflow.shadow_replay import load_inputs,ReplayReads
from ats.data import consumer_api
consumer_api.read_input=lambda *a,**k: (_ for _ in ()).throw(AssertionError('provider fallback'))
value=load_inputs(sys.argv[2],path=sys.argv[1])
adapter=ReplayReads(value)
packets=[adapter.read_input(**r['request']).model_dump(mode='json') for r in value.reads]
print(json.dumps({'hash':value.input_hash(),'clock':adapter.logical_time.isoformat(),'packets':packets}))
"""
    result = subprocess.run([sys.executable, "-c", program, str(path), identity],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["hash"] == identity
    assert body["packets"] == [r["input"] for r in value.reads]
    assert body["clock"] == value.packet.surfaces[si.LOGICAL_EVAL_TIME]


@pytest.mark.parametrize("fault", ["hash_only", "price_changed", "phase_changed", "naive_clock", "missing_account"])
def test_incomplete_or_tampered_content_refuses(isolated, fault):
    value = frozen()
    if fault == "hash_only":
        value.contents = {}
    elif fault == "price_changed":
        value.contents[si.MARKET_RUNTIME][0]["payload"]["ask"] = 999
    elif fault == "phase_changed":
        value.reads[0]["request"]["stage"] = "research"
    elif fault == "naive_clock":
        value.packet.surfaces[si.LOGICAL_EVAL_TIME] = "2026-10-08T10:00:00"
    else:
        value.contents.pop(si.ACCOUNT_STATE)
    with pytest.raises((si.IncompletePacketError, ValueError)):
        replay.save_inputs(value, path=isolated / "inputs.sqlite")


def test_unrecorded_replay_scope_refuses_and_returns_defensive_copies(isolated):
    value = frozen()
    adapter = replay.ReplayReads(value)
    request = value.reads[0]["request"]
    original = adapter.read_input(**request)
    original.payload["ask"] = 999
    assert adapter.read_input(**request).payload["ask"] == 100.1
    with pytest.raises(si.IncompletePacketError, match="uncaptured"):
        adapter.read_input(**{**request, "scope": {"entity": "NVDA"}})
    with pytest.raises(si.IncompletePacketError, match="uncaptured"):
        adapter.read_input(**request, as_of=adapter.logical_time + timedelta(seconds=1))


def test_same_input_and_clock_injected_in_two_calls(isolated):
    value = frozen()
    def entry(*, read_input, logical_time):
        return logical_time.isoformat(), read_input(**value.reads[0]["request"]).payload
    assert replay.ReplayReads(value).invoke(entry) == replay.ReplayReads(value).invoke(entry)


def test_append_only_and_destination_checks(isolated, tmp_path):
    value = frozen()
    path = isolated / "inputs.sqlite"
    identity = replay.save_inputs(value, path=path)
    assert replay.save_inputs(value, path=path) == identity
    with sqlite3.connect(path) as conn:
        for sql in ("UPDATE shadow_replay_inputs SET body='{}'", "DELETE FROM shadow_replay_inputs"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)
    with pytest.raises(PermissionError, match="outside_isolation"):
        replay.save_inputs(value, path=tmp_path / "production.sqlite")
    assert not (tmp_path / "production.sqlite").exists()


def test_replay_is_not_a_production_read_capability():
    with pytest.raises(PermissionError, match="complete_isolation"):
        replay.ReplayReads(frozen()).surface(si.MARKET_RUNTIME)


def test_report_lookup_does_not_create_database(tmp_path):
    path = tmp_path / "missing.sqlite"
    batch = SimpleNamespace(shadow_report_id="missing", scope={}, batch_class="research_read",
                            required_surfaces=())
    assert reports.check_batch_report(batch, path=path)[0] is False
    assert not path.exists()


def _report(path, scope):
    result = compare.compare_all(run_id="tool", left={"_packet": {}, "analyst_output": {}},
                                 right={"_packet": {}, "analyst_output": {}}, expected_triggers=[])
    reports.record_comparison(report_id="report", run_id="tool", consumer_id="layer",
                              batch_class="research_read", scope=scope, packet_hash="hash-only",
                              result=result, path=path)
    reports.record_signoff(report_id="report", actor="test", reason="tool test", path=path)


def test_imported_json_report_cannot_become_formal_cutover_evidence(tmp_path):
    path = tmp_path / "reports.sqlite"
    scope = {"entities": ["AAPL"]}
    _report(path, scope)
    batch = bm.CutoverBatch(batch_id="test", batch_class="research_read", scope=scope,
                           shadow_report_id="report")
    ok, problems = reports.check_batch_report(batch, path=path)
    assert not ok and any("execution evidence" in p for p in problems)


@pytest.mark.parametrize("fault", ["empty", "checker_missing", "raises", "bad_result", "refused"])
def test_dry_run_and_executor_never_default_pass_reports(tmp_path, monkeypatch, fault):
    cutover = tmp_path / "cutover.sqlite"
    wiring.bootstrap_wired(path=cutover, actor="test")
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", lambda *a, **k: None)
    batch = bm.CutoverBatch(batch_id="test", batch_class="research_read", scope={"consumers": ["layer"]},
        shadow_report_id="" if fault == "empty" else "report", fallback_route="legacy",
        fallback_proof="valid", fallback_retired="no", fallback_available="yes", fallback_drill_ref="drill")
    def throws(batch):
        raise RuntimeError("unreadable")
    checker = {"empty": lambda b: (True, []), "checker_missing": None, "raises": throws,
               "bad_result": lambda b: ("yes", []), "refused": lambda b: (False, ["revoked"])}[fault]
    result = bm.dry_run_batch(batch, report_checker=checker,
        qualification_reader=lambda *a: {"status": "eligible"}, path=tmp_path / "batch.sqlite",
        cutover_path=cutover, record=False)
    assert not result.switchable and not result.report_citable
    result = rc.execute_batch(batch, report_checker=checker,
        authorisation=rc.DeploymentAuthorization(reference="unit", authorised_by="owner",
            issued_by="owner", scope=("layer",), valid_until="2099-01-01T00:00:00+00:00"),
        projection_checker=lambda *a: {"available": True},
        qualification_reader=lambda *a: {"status": "eligible"},
        path=tmp_path / "batch.sqlite", cutover_path=cutover)
    assert result.outcome == rc.BATCH_REFUSED
    assert any(s.outcome == rc.REPORT_INVALID for s in result.scopes)


def test_activation_refuses_absent_checker_at_actual_write(tmp_path, monkeypatch):
    cutover = tmp_path / "cutover.sqlite"
    wiring.bootstrap_wired(path=cutover, actor="test")
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", lambda *a, **k: None)
    request = plane.ActivationRequest(boundary="projection_read", consumer_id="layer",
                                     scope={"entities": ["AAPL"]}, report_id="report")
    with pytest.raises(plane.CutoverError, match="checker"):
        plane.record_activation(request=request, path=cutover)
    with sqlite3.connect(cutover) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cutover_activations").fetchone()[0] == 0

@pytest.mark.parametrize("fault", ["revoked", "scope", "code", "missing_face", "recompared"])
def test_signed_report_applicability_is_rechecked(tmp_path, fault):
    path = tmp_path / "reports.sqlite"
    scope = {"entities": ["AAPL"]}
    _report(path, scope)
    requested = scope
    required = [compare.INPUT_SNAPSHOT, compare.ANALYST_OUTPUT]
    kwargs = {}
    if fault == "revoked":
        reports.record_revocation(report_id="report", actor="test", reason="withdrawn", path=path)
    elif fault == "scope":
        requested = {"entities": ["NVDA"]}
    elif fault == "code":
        kwargs["code_hash"] = "changed"
    elif fault == "missing_face":
        required.append(compare.RISK_VERDICT)
    else:
        _report_result = compare.compare_all(run_id="new", left={}, right={})
        reports.record_recomparison(report_id="report", run_id="new", consumer_id="layer",
            batch_class="research_read", scope=scope, packet_hash="new", result=_report_result, path=path)
    assert not reports.check_citable(report_id="report", scope=requested,
                                     required_surfaces=required, path=path, **kwargs)[0]


@pytest.mark.parametrize("action", ["set-route", "activate", "preflight"])
def test_actual_cli_missing_report_refuses_without_route_or_activation_write(tmp_path, capsys, action):
    from ats.runtime import cli
    path = tmp_path / "cutover.sqlite"
    wiring.bootstrap_wired(path=path, actor="test")
    before = {k: v.as_row() for k, v in plane.all_boundaries(path).items()}
    args = ["cutover", action, "--db", str(path), "--boundary", "projection_read",
            "--consumer-id", "layer", "--scope-json", '{"entities":["AAPL"]}', "--actor", "test"]
    if action == "set-route":
        args += ["--route", "target", "--reason", "test"]
    assert cli.main(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "report" in str(payload).lower()
    assert {k: v.as_row() for k, v in plane.all_boundaries(path).items()} == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cutover_activations").fetchone()[0] == 0


def test_recording_destination_is_checked_before_entry(isolated, tmp_path):
    called = []
    def entry(**kwargs):
        called.append(True)
    with pytest.raises(PermissionError):
        replay.ReplayReads(frozen()).invoke(entry, run_id="invalid", path=tmp_path / "outside.sqlite")
    assert not called


def test_paired_run_recording_is_durable_but_test_helper_is_not_business_evidence(isolated):
    value = frozen()
    path = isolated / "inputs.sqlite"
    replay.save_inputs(value, path=path)
    def left(*, read_input, logical_time):
        read_input(**value.reads[0]["request"])
        return {"clock": logical_time.isoformat()}
    def right(*, read_input, logical_time):
        read_input(**value.reads[0]["request"])
        return {"clock": logical_time.isoformat()}
    for run_id, entry in (("left", left), ("right", right)):
        replay.ReplayReads(value).invoke(entry, run_id=run_id, path=path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shadow_replay_runs").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM shadow_replay_runs")
    proof = {"input_store": str(path), "left_run_id": "left", "right_run_id": "right"}
    with pytest.raises(si.IncompletePacketError, match="business execution"):
        replay.verify_execution_evidence(proof, report=SimpleNamespace(packet_hash=value.input_hash()),
                                         scope=value.packet.scope)
