"""Reconcile versioned Tasks 2–4 evidence and the live assurance ledger.

Read-only, offline. It checks that detailed, cached verification artifacts are
present and consistent, then queries qualification for every registered role.
It does not ingest data or write qualification evidence.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import yaml

from ats.config import REPO_ROOT
from ats.data.assurance import evidence_history, qualification
from ats.data.catalog.loader import DataCatalog
from ats.data.contract_validation import validate_target_contract


def load(path: Path):
    return json.loads(path.read_text())


def run():
    validation = validate_target_contract()
    catalog_validation = DataCatalog.load().validate()
    task3 = load(REPO_ROOT / "docs/validation/TARGET_DATAFLOW_TASK3_REPLAY.json")
    task4 = load(REPO_ROOT / "docs/validation/TARGET_DATAFLOW_TASK4_REPLAY.json")
    runtime = load(REPO_ROOT / "docs/validation/TARGET_DATAFLOW_TASK4_RUNTIME_ROLES.json")
    task3_doc = (REPO_ROOT / "docs/validation/TARGET_DATAFLOW_TASK3_ACCEPTANCE.md").read_text()
    structured_cases = all(
        row.get("normal", {}).get("status") == "succeeded"
        and row.get("duplicate", {}).get("status") == "no_change"
        and row.get("transport", {}).get("status") == "unreachable"
        and row.get("permission", {}).get("status") == "unauthorized"
        and row.get("stale", {}).get("status") == "stale"
        and row.get("quarantine", {}).get("status") == "validation_failed"
        and row.get("synthetic_revision", {}).get("status") == "succeeded"
        and row.get("historical_equal") is True
        and row.get("native_packet_equal") is True
        for row in task3.get("structured", [])
    )
    mechanism_names = task4.get("mechanism", {}).get("checks", [])
    manifest = yaml.safe_load((REPO_ROOT / "config/data/target_dataflow_coverage.yaml").read_text())
    registry_paths = {
        "structured": REPO_ROOT / "config/data/structured.yaml",
        "unstructured": REPO_ROOT / "config/data/unstructured.yaml",
    }
    statuses = {}
    for role in manifest["consumers"]:
        scope = {"scope_kind": "task5_registered_contract", "products": sorted(role["products"])}
        result = qualification(
            domain_id=role["domain"],
            consumer_id=role["id"],
            contract_version=role["contract_version"],
            scope=scope,
        )
        history = evidence_history(domain_id=role["domain"], consumer_id=role["id"], limit=1000)
        statuses[role["id"]] = {
            "domain": role["domain"],
            "contract_version": role["contract_version"],
            "products": role["products"],
            "status": result["status"],
            "missing_evidence": result["missing"],
            "reasons": result["reasons"],
            "evidence_history_count": len(history),
            "stable_route": role.get("legacy", "not_declared"),
            "current_routing_changed": False,
        }
    report = {
        "schema_version": "target-dataflow-task5-reconciliation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "network_calls": 0,
        "production_writes": 0,
        "production_qualification_granted": False,
        "checks": {
            "target_contract_valid": bool(validation.get("valid")),
            "catalog_valid": bool(catalog_validation.valid),
            "task3_cached_replay_passed": bool(task3.get("passed"))
            and task3.get("network_calls") == 0,
            "six_structured_replay_cases_match_expected_states": len(task3.get("structured", []))
            == 6
            and structured_cases,
            "qualification_gate_cases_present": all(
                name in mechanism_names
                for name in (
                    "exact_scope_isolation",
                    "evidence_ttl_not_report_age",
                    "manifest_drift_fail_closed",
                    "dependency_drift_fail_closed",
                    "latest_failed_evidence_overrides_prior_pass",
                    "passed_label_without_real_rollback_proof_rejected",
                )
            ),
            "source_budget_and_duplicate_refresh_cases_documented": (
                "预算延期" in task3_doc and "重复" in task3_doc and "revision" in task3_doc.lower()
            ),
            "task4_cached_replay_passed": bool(task4.get("passed"))
            and task4.get("network_calls") == 0,
            "task4_runtime_roles_passed": bool(runtime.get("passed"))
            and len(runtime.get("proofs", [])) == 9,
            "task4_no_trade_or_cutover": runtime.get("orders_submitted") == 0
            and runtime.get("production_routes_changed") is False,
            "all_roles_queried_against_ledger": len(statuses) == 10,
            "all_roles_fail_closed_without_evidence": all(
                x["status"] == "ineligible" for x in statuses.values()
            ),
        },
        "catalog": {
            "valid": catalog_validation.valid,
            "checks": len(catalog_validation.checks),
            "failed_checks": len(catalog_validation.reason_codes),
        },
        "authoritative_registry_hashes": {
            key: sha256(path.read_bytes()).hexdigest() for key, path in registry_paths.items()
        },
        "task3_evidence": {
            "structured_dataset_count": len(task3.get("structured", [])),
            "structured_replay_checks": {
                "normal": "succeeded",
                "duplicate": "no_change",
                "transport_fault": "unreachable",
                "permission": "unauthorized",
                "stale": "stale",
                "quality_rejection": "validation_failed",
                "revision": "succeeded",
                "historical_asof_and_packet_equal": True,
            },
            "calendar_sources": len(task3.get("calendars", [])),
            "consumer_edges": len(task3.get("edges", {}).get("edges", [])),
            "architecture_negative_cases": len(task3.get("negative_guards", [])),
            "document_sources": len(task3.get("documents", [])),
            "fixed_source_parser_chains": len(task3.get("fixed_sources", [])),
            "fixed_publication_gates": len(task3.get("fixed_publication_gates", [])),
            "queue_budget_and_revision_gate_names": mechanism_names,
        },
        "task4_evidence": {
            "cached_consumer_drills": len(task4.get("consumer_drills", [])),
            "mechanism_checks": len(task4.get("mechanism", {}).get("checks", [])),
            "runtime_role_edges": len(runtime.get("proofs", [])),
            "runtime_edge_results": runtime.get("proofs", []),
            "runtime_negative_recovery_checks": runtime.get("checks", []),
            "live_status_only": runtime.get("live", {}),
            "actual_internal_ledger": runtime.get("production_internal", {}),
        },
        "consumer_gates": statuses,
        "summary": {
            "eligible_consumers": sorted(
                k for k, v in statuses.items() if v["status"] == "eligible"
            ),
            "ineligible_consumers": sorted(
                k for k, v in statuses.items() if v["status"] == "ineligible"
            ),
            "all_current_roles_ineligible": all(
                v["status"] == "ineligible" for v in statuses.values()
            ),
            "interpretation": (
                "qualification queries are exact-scope read-only checks; no evidence was written"
            ),
        },
    }
    report["passed"] = all(report["checks"].values())
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps({"passed": False, "status": "running_or_incomplete"}) + "\n")
    report = run()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "eligible": len(report["summary"]["eligible_consumers"]),
                "ineligible": len(report["summary"]["ineligible_consumers"]),
                "report": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)
