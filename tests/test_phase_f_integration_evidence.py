"""Adversarial audits of the durable actual 3.10 ledgers; originals stay readonly."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from ats.config import REPO_ROOT
from ats.workflow import acceptance_reports as reports
from ats.workflow.acceptance_assertions import audit_attribution
from ats.workflow.isolation import inspect_isolated_records

spec = importlib.util.spec_from_file_location("phase_f_integration_auditor", REPO_ROOT / "scripts/verify_phase_f_integration.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
INDEX = REPO_ROOT / "docs/validation/phase_f_independent_runner_20261009/evidence_index.json"


@pytest.fixture(scope="module")
def original():
    source = json.loads(INDEX.read_text())
    item = next(r for r in source["reports"] if r["label"] == "routine")
    body = reports.read(item["report_id"], path=item["report_db"])["body"]
    value, runs, _ = reports._executions(body["proof"], body["input_hash"])
    return source, value, runs[0]["output"]


def test_original_reports_remeasured_unsigned_failure_retained(original):
    result = audit.index(original[0])
    assert len(result["reports"]) == 4
    assert all(r["status"] == "unsigned" for r in result["reports"])
    assert sum(r["eligible_for_explicit_signoff"] for r in result["reports"]) == 3
    assert next(r for r in result["reports"] if "missing" in r["label"])["problems"]
    assert result["production_activation"] == "not-executed"


def test_all_actual_orders_have_research_review_approval_receipt_fills(original):
    result = audit.trace(original[0])
    assert result["status"] == "passed" and len(result["executions"]) == 4
    for execution in result["executions"]:
        assert execution["chains"]
        for chain in execution["chains"]:
            assert len(chain["research_snapshot"]["items"]) == 6
            assert chain["review"]["review_id"] and chain["approval"]["decision"] == "approved"
            assert chain["receipt"]["intent_id"] and chain["fills"]


@pytest.mark.parametrize("broken", ["empty", "snapshot", "revision", "review", "approval", "receipt", "fill"])
def test_real_ledger_chain_breaks_refuse_pass(original, tmp_path, broken):
    _, value, output = original
    target = tmp_path / "copy"; target.mkdir()
    path = target / "memory.sqlite"
    source = audit.readonly(output["store"])
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    source.backup(conn); source.close()
    evidence = {**copy.deepcopy(output), "store":SimpleNamespace(conn=conn,path=str(path)), "inputs":value}
    table = {"empty":"trades", "snapshot":"task_projection_envelopes", "revision":"decision_revisions",
             "review":"decision_risk_reviews", "approval":"boss_approvals", "fill":"fills"}.get(broken)
    try:
        if table:
            # Remove append-only guards only in this disposable corruption copy.
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?",(table,)).fetchall():
                conn.execute('DROP TRIGGER "' + row[0].replace('"','""') + '"')
            conn.execute(f"DELETE FROM {table}"); conn.commit()
        else:
            evidence["receipts"] = []
        with inspect_isolated_records(target), pytest.raises((ValueError, RuntimeError)):
            audit_attribution(evidence)
    finally:
        conn.close()


@pytest.mark.parametrize("broken", ["missing_receipt", "missing_trade", "unapproved", "real_route", "escape", "no_refusal"])
def test_actual_acceptance_audit_dominates_clean_ledger(original, broken):
    _, value, output = original
    output = copy.deepcopy(output)
    with inspect_isolated_records(output["side_root"]), audit.readonly(output["store"]) as conn:
        actual, _ = audit_attribution({**output,"store":SimpleNamespace(conn=conn,path=output["store"]),"inputs":value})
    if broken == "missing_receipt": actual["receipts"] = []
    if broken == "missing_trade": actual["trades"] = []
    if broken == "unapproved": output["accepted"][0]["approval_id"] = "forged"
    if broken == "real_route": actual["receipts"][0]["route_id"] = "live"
    if broken == "escape": output["accepted"][0]["isolation_root"] = "/production"
    if broken == "no_refusal": output["broker_refusal"] = {}
    with pytest.raises(ValueError): audit.submission_safety(output, actual)


def test_unrelated_old_and_late_fills_excluded_exact_isolated_ids_rejected(original):
    traced = audit.trace(original[0])
    snapshot = {"path":"fixture-only", "hash":"fixture", "tables":{
        "trades":[{"cycle_id":"old-legal", "order_ref":"old:route-A:g1:order-2", "approval_id":"old-approved"}],
        "fills":[{"exec_id":"late-1", "order_ref":"old:route-A:g1:order-2", "approval_id":"old-approved"}]}}
    assert audit.production_pollution(snapshot,traced["executions"])["leaked_rows"] == 0
    snapshot["tables"]["fills"].append({"order_ref":traced["executions"][0]["chains"][0]["receipt"]["order_ref"]})
    with pytest.raises(ValueError, match="stop batch"):
        audit.production_pollution(snapshot,traced["executions"])


def test_snapshot_missing_database_refuses_without_creating(tmp_path):
    missing = tmp_path / "absent.sqlite"
    with pytest.raises(sqlite3.OperationalError): audit.ledger_snapshot(missing)
    assert not missing.exists()


def test_frozen_receipt_cannot_replace_missing_arbitration_row(original,tmp_path):
    _, value, output = original
    with inspect_isolated_records(output["side_root"]), audit.readonly(output["store"]) as conn:
        actual, _ = audit_attribution({**output,"store":SimpleNamespace(conn=conn,path=output["store"]),"inputs":value})
    root = tmp_path / "copy"; root.mkdir()
    with sqlite3.connect(root / "phase_f_routes.sqlite") as conn:
        conn.execute("CREATE TABLE trade_submit_receipts(intent_id TEXT)")
    with pytest.raises(ValueError,match="arbitration receipts"):
        audit.verify_persisted_receipts({**output,"side_root":str(root)},actual)


@pytest.mark.parametrize("field,value", [("status","submitted"),("status","unknown"),("payload_hash","forged"),("generation",99)])
def test_actual_registry_cannot_drift_or_claim_unsupported_lifecycle(original,tmp_path,field,value):
    _, inputs, output = original
    with inspect_isolated_records(output["side_root"]), audit.readonly(output["store"]) as conn:
        actual, _ = audit_attribution({**output,"store":SimpleNamespace(conn=conn,path=output["store"]),"inputs":inputs})
    root = tmp_path / "registry-copy";root.mkdir()
    source = audit.readonly(Path(output["side_root"]) / "phase_f_routes.sqlite")
    with sqlite3.connect(root / "phase_f_routes.sqlite") as conn:
        source.backup(conn);source.close()
        conn.execute(f"UPDATE trade_submit_receipts SET {field}=?",(value,))
    with pytest.raises(ValueError,match="stop batch"):
        audit.verify_persisted_receipts({**output,"side_root":str(root)},actual)
