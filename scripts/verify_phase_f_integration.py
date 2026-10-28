"""Readonly, reproducible audit of actual Phase F runs (7.8, 13.4, 13.5).

This script consumes original databases, never supplied passed flags or trades
counts. Output files are exclusive-create so a later audit cannot rewrite one.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from ats.workflow import acceptance_reports as reports
from ats.workflow.acceptance_assertions import audit_attribution, audit_approval, _revision
from ats.workflow.isolation import inspect_isolated_records, build_environment


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def ledger_snapshot(path):
    """Snapshot logical rows, including WAL, without opening a migration owner."""
    with closing(readonly(path)) as conn:
        conn.execute("BEGIN")
        tables = {}
        for table in ("trades", "fills"):
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
            rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
            tables[table] = rows
    return {"path": str(Path(path).resolve()), "tables": tables,
            "hash": hashlib.sha256(json.dumps(tables, sort_keys=True).encode()).hexdigest()}


def index(source):
    result = []
    for item in source["reports"]:
        original = reports.read(item["report_id"], path=item["report_db"])
        body = original["body"]
        value, runs, measured = reports._executions(body["proof"], body["input_hash"])
        problems = []
        try:
            reports._validate(body)
        except ValueError as exc:
            problems.append(str(exc))
        executions = []
        for run, (matrix, checked) in zip(runs, measured):
            executions.append({"run_id": run["run_id"], "workflow_run_id": matrix.body["plan"]["run_id"],
                "store": run["output"]["store"], "side_root": run["output"]["side_root"],
                "reads": len(run["reads"]), "matrix_hash": matrix.matrix_hash,
                "assertions": checked.as_dict(),
                "refs": sorted({ref for a in checked.results for ref in a.refs})})
        result.append({"label": item["label"], "report_id": body["report_id"],
            "report_db": item["report_db"], "report_hash": original["body_hash"],
            "status": original["status"], "eligible_for_explicit_signoff": not problems,
            "problems": problems, "class": body["batch_class"], "scope": body["scope"],
            "input_hash": value.input_hash(), "proof": body["proof"], "executions": executions,
            "production_qualification": "not-established", "tws_readonly": "untested"})
    return {"reports": result, "implementation": reports.fingerprint(),
            "static_scans": "auxiliary-only", "network_model_and_provider": "fixture-only",
            "production_activation": "not-executed"}


def trace(source):
    result = []
    for item in source["reports"]:
        if item["class"] != "trading":
            continue
        body = reports.read(item["report_id"], path=item["report_db"])["body"]
        reports._validate(body)
        value, runs, _ = reports._executions(body["proof"], body["input_hash"])
        for run in runs:
            output = run["output"]
            with inspect_isolated_records(output["side_root"]), closing(readonly(output["store"])) as conn:
                evidence = {**output, "store": SimpleNamespace(conn=conn, path=output["store"]), "inputs": value}
                actual, refs = audit_attribution(evidence)
                verify_persisted_receipts(output, actual)
                approval, _ = audit_approval(evidence)
                repo, revision, orders = _revision(evidence)
                review = dict(repo.effective_review(revision["cycle_id"], revision["revision_no"], revision["decision_hash"]))
                snapshot = json.loads(repo.get_cycle(revision["cycle_id"])["research_snapshot"])
                chains = []
                for receipt in actual["receipts"]:
                    seq = receipt["sequence"]
                    chains.append({"research_snapshot": snapshot, "cycle_id": revision["cycle_id"],
                        "revision_no": revision["revision_no"], "decision_hash": revision["decision_hash"],
                        "order": orders[seq], "review": review, "approval": approval, "receipt": receipt,
                        "trade": next(t for t in actual["trades"] if t["order_id"] == receipt["broker_order_id"]),
                        "fills": [f for f in actual["fills"] if f["order_id"] == receipt["broker_order_id"]]})
            result.append({"label": item["label"], "run_id": run["run_id"], "store": output["store"],
                "input_hash": value.input_hash(), "status": "passed", "refs": sorted(set(refs)), "chains": chains})
    return {"status": "passed" if result else "untested", "executions": result}


def verify_persisted_receipts(output, actual):
    path = build_environment(output["side_root"]).path_for("ATS_ROUTE_REGISTRY_PATH")
    with closing(readonly(path)) as conn:
        saved = [dict(r) for r in conn.execute("SELECT * FROM trade_submit_receipts")]
    immutable = lambda rows: sorted([{k:v for k,v in r.items() if k != "status"} for r in rows], key=lambda r:r["intent_id"])
    if immutable(saved) != immutable(actual["receipts"]):
        raise ValueError("actual arbitration receipts differ from execution; stop batch")
    for current in saved:
        original = next(r for r in actual["receipts"] if r["intent_id"] == current["intent_id"])
        trade = next(t for t in actual["trades"] if t["order_id"] == current["broker_order_id"])
        permitted = {"submitted": {"submitted", "partial", "filled"},
                     "partial": {"partial", "filled"}, "filled": {"filled"}}
        if current["status"] not in permitted.get(original["status"], set()) or current["status"] != trade["status"]:
            raise ValueError("arbitration lifecycle differs from actual fill ledger; stop batch")


def submission_safety(output, actual):
    """Broker observations dominate an empty local ledger; simulation is explicit."""
    accepted = output.get("accepted", [])
    receipts = actual["receipts"]
    if not accepted or not receipts:
        raise ValueError("nonempty actual simulation acceptance required")
    if len(accepted) != len(receipts) or {r["order_id"] for r in accepted} != {r["broker_order_id"] for r in receipts}:
        raise ValueError("broker accepted order has no unique local receipt; stop batch")
    for entry in accepted:
        receipt = next(r for r in receipts if r["broker_order_id"] == entry["order_id"])
        trade = next((t for t in actual["trades"] if t["order_id"] == entry["order_id"]), None)
        if (not trade or entry["decision_hash"] != trade["decision_hash"]
                or entry["approval_id"] != trade["approval_id"] or entry["qty"] != trade["qty"]
                or entry["order_ref"] != receipt["order_ref"]
                or Path(entry.get("isolation_root", "")).resolve() != Path(output["side_root"]).resolve()
                or receipt["route_id"] != "simulation" or receipt["environment"] != "paper"):
            raise ValueError("unapproved, unrecorded or escaped acceptance; stop batch")
    refusal = output.get("broker_refusal", {})
    if refusal.get("destination") != "IBKR" or refusal.get("reason_code") != "isolated_run_prohibited" or not refusal.get("refusal_id"):
        raise ValueError("actual IBKR prohibition not exercised; stop batch")
    return {"status": "passed", "simulated_acceptances": len(accepted), "refusal": refusal,
            "capabilities": [{k: r[k] for k in ("intent_id", "route_id", "generation", "account", "environment")} for r in receipts]}


def production_pollution(snapshot, executions):
    """Match exact causal IDs; unrelated historical/late trades are not violations."""
    leaked = []
    for execution in executions:
        for chain in execution["chains"]:
            receipt = chain["receipt"]
            for table, rows in snapshot["tables"].items():
                for row in rows:
                    if (row.get("order_ref") == receipt["order_ref"]
                            or row.get("approval_id") == chain["approval"]["approval_id"]
                            or row.get("decision_hash") == chain["decision_hash"]
                            or row.get("isolation_root") == str(Path(execution["store"]).parent)):
                        leaked.append({"table": table, "row": row})
    if leaked:
        raise ValueError("isolated order attribution appeared in production; stop batch: " + json.dumps(leaked))
    return {"status": "passed", "production_path": snapshot["path"], "snapshot_hash": snapshot["hash"],
            "rows_checked": {k: len(v) for k, v in snapshot["tables"].items()}, "leaked_rows": 0,
            "unrelated_history": "retained; exact causal identities checked"}


def safety(source, production_db):
    before = ledger_snapshot(production_db)
    traced = trace(source)
    executions = []
    for item in source["reports"]:
        if item["class"] != "trading":
            continue
        body = reports.read(item["report_id"], path=item["report_db"])["body"]
        value, runs, _ = reports._executions(body["proof"], body["input_hash"])
        for run in runs:
            output = run["output"]
            with inspect_isolated_records(output["side_root"]), closing(readonly(output["store"])) as conn:
                actual, _ = audit_attribution({**output, "store": SimpleNamespace(conn=conn,path=output["store"]), "inputs":value})
                verify_persisted_receipts(output, actual)
                checked = submission_safety(output, actual)
            executions.append({"label": item["label"], "run_id": run["run_id"], **checked})
    pollution = production_pollution(before, traced["executions"])
    after = ledger_snapshot(production_db)
    if before["hash"] != after["hash"]:
        raise ValueError("production changed during audit; investigate attribution and repeat with a new output")
    return {"status": "passed" if executions else "untested", "executions": executions,
            "production": pollution, "production_unchanged_during_audit": True,
            "historical_capture_window": "not-observed; no before snapshot existed for 3.10",
            "tws_readonly": "untested", "production_or_paper_submit": "not-authorized/not-tested"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("index", "trace", "safety", "snapshot"))
    parser.add_argument("--index", type=Path)
    parser.add_argument("--production-db", type=Path, default=Path("var/ats.sqlite"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.stage == "snapshot":
        result = ledger_snapshot(args.production_db)
    else:
        if args.index is None:
            parser.error("--index required")
        source = json.loads(args.index.read_text())
        result = {"index": index, "trace": trace, "safety": lambda x: safety(x,args.production_db)}[args.stage](source)
    result.update(stage=args.stage, recorded_at=datetime.now(UTC).isoformat(),
                  auditor_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"output": str(args.out), "stage":args.stage, "status":result.get("status","recorded")}))


if __name__ == "__main__":
    main()
