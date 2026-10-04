"""Tasks 4: cached native fallback drills and isolated qualification fault probes.

No collection, LLM, broker connection, schedule change or production ledger write.
Run with uv run --offline --no-sync python scripts/verify_dataflow_qualification.py.
The resulting consumer matrix is scoped evidence, NOT production qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from ats.config import REPO_ROOT
from ats.data import assurance
from ats.data.consumer_api import _json, read_input
from ats.data.contract_validation import validate_target_contract
from ats.data.products.base import DataProducts
from ats.data.products.unstructured import admitted_documents
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.stores.unstructured import PlatformUnstructuredRepository
from ats.decision.repository import DecisionAuditRepository
from ats.execution.state_api import get_internal_state
from ats.memory.store import TradingMemory

COVERAGE = REPO_ROOT / "config/data/target_dataflow_coverage.yaml"


def digest(payload):
    return hashlib.sha256(
        json.dumps(_json(payload), sort_keys=True, default=str).encode()
    ).hexdigest()


def readonly_store(path):
    # Bind existing read methods without invoking legacy schema bootstrap/WAL changes.
    store = object.__new__(TradingMemory)
    store.path, store._data, store._closed = str(path), None, False
    store.conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    store.conn.row_factory = sqlite3.Row
    store.conn.execute("PRAGMA query_only=ON")
    store.conn.execute("BEGIN")
    return store


def cached_fallback_drills(data_path, internal_path):
    """Exercise stable native reads directly, preserving exact data scope/time."""
    manifest = yaml.safe_load(COVERAGE.read_text())
    routes = manifest["qualification_policy"]["rollback_routes"]
    structured = SQLiteStructuredRepository(
        data_path, readonly=True, artifact_root=REPO_ROOT / "var/data_artifacts"
    )
    unstructured = PlatformUnstructuredRepository(data_path, writable=False)
    structured.conn.execute("BEGIN")
    unstructured.conn.execute("BEGIN")
    products = DataProducts(structured_repository=structured, unstructured_repository=unstructured)
    now = datetime.now(timezone.utc)
    internal = readonly_store(internal_path) if internal_path.is_file() else None
    output = []
    try:
        for consumer in manifest["consumers"]:
            proofs = []
            scopes = {}
            for product in consumer["products"]:
                scope, native, packet, native_reader = {}, None, None, None
                reason = ""
                refs = []
                try:
                    if product in {"HIER_DATA", "COMPANY_DATA", "MACRO_DATA"}:
                        dataset = {
                            "HIER_DATA": "industry_dram_contract_price",
                            "COMPANY_DATA": "company_financials",
                            "MACRO_DATA": "regional_tw_exports",
                        }[product]
                        row = structured.conn.execute(
                            "SELECT s.entity_id,s.metric_id,o.observation_id "
                            "FROM structured_observations o "
                            "JOIN structured_series s ON s.series_id=o.series_id "
                            "WHERE s.dataset_id=? AND o.quality_status='accepted' "
                            "ORDER BY o.known_at DESC LIMIT 1",
                            (dataset,),
                        ).fetchone()
                        if row is None:
                            raise ValueError("accepted_cache_missing")
                        scope = {
                            "entity": row["entity_id"],
                            "metric": row["metric_id"],
                            "dataset": dataset,
                        }
                        packet = read_input(
                            consumer["id"], product, scope=scope, as_of=now, products=products
                        )

                        def native_reader():
                            return products.metric_series(**scope, as_of=now)

                        native = native_reader()
                    elif product == "DOC_DATA":
                        scope = {"entity": "NVDA", "limit": 20}
                        packet = read_input(
                            consumer["id"], product, scope=scope, as_of=now, products=products
                        )

                        def native_reader():
                            return _json(
                                admitted_documents(
                                    entities=["NVDA"], as_of=now, limit=20, repository=unstructured
                                )
                            )

                        native = native_reader()
                    elif product in {"PORTFOLIO_DATA", "HISTORY_DATA"}:
                        if internal is None:
                            raise ValueError("internal_ledger_missing")
                        packet = read_input(consumer["id"], product, scope=scope, store=internal)
                        state = get_internal_state(internal)
                        native = (
                            state.portfolio
                            if product == "PORTFOLIO_DATA"
                            else {
                                "trades": state.trades,
                                "fills": state.fills,
                                "performance": state.performance,
                                "attribution": state.attribution,
                            }
                        )
                        refs = [f"internal-snapshot:{s}" for s in packet.source_as_of]

                        def native_reader():
                            state = get_internal_state(internal)
                            return (
                                state.portfolio
                                if product == "PORTFOLIO_DATA"
                                else {
                                    "trades": state.trades,
                                    "fills": state.fills,
                                    "performance": state.performance,
                                    "attribution": state.attribution,
                                }
                            )
                    elif product == "RISK_RULES":
                        from ats.config import get_config

                        def native_reader():
                            return _json(get_config().app.risk)

                        native = native_reader()
                        packet = read_input(consumer["id"], product, scope=scope)
                    elif product == "DECISION_APPROVAL_CONTEXT":
                        if internal is None:
                            raise ValueError("internal_ledger_missing")
                        row = internal.conn.execute(
                            "SELECT cycle_id FROM decision_cycles ORDER BY updated_at DESC LIMIT 1"
                        ).fetchone()
                        if row is None:
                            raise ValueError("persisted_decision_context_missing")
                        scope = {"cycle_id": row["cycle_id"]}
                        audit = DecisionAuditRepository(internal)
                        packet = read_input(consumer["id"], product, scope=scope, audit=audit)

                        def native_reader():
                            return audit.read_chain(row["cycle_id"])

                        native = native_reader()
                    elif product == "MARKET_DATA":
                        reason = (
                            "no_reusable_runtime_quote; no_network_or_synthetic_quote_qualification"
                        )
                    elif product == "BROKER_STATE":
                        # clerk_run is a WRITER, even in dry_run some steps write. Never invoke it.
                        reason = (
                            "clerk_run_is_not_readonly_broker_fallback; "
                            "ledger_is_not_live_broker_state"
                        )
                    elif product == "APPROVED_EXECUTION_AUTHORIZATION":
                        reason = (
                            "fail_closed_requires_current_exact_revision_approval; "
                            "historical_auth_not_replayed_live"
                        )
                    equal = packet is not None and packet.payload == _json(native)
                    if (
                        packet is not None
                        and not reason
                        and (packet.status != "complete" or not equal)
                    ):
                        reason = (
                            "native_packet_mismatch"
                            if not equal
                            else f"native_input_{packet.status}"
                        )
                    passed = equal and not reason
                    refs = refs or (packet.input_refs if packet is not None else [])
                    if passed and not refs:
                        reason, passed = "provenance_missing", False
                    # Fault isolation: the new packet reader is locally unavailable;
                    # the already-exercised native route still returns the exact snapshot.
                    # No production routing flag is mutated.
                    if passed:
                        with patch(
                            __name__ + ".read_input",
                            side_effect=RuntimeError("synthetic packet reader outage"),
                        ):
                            try:
                                read_input(consumer["id"], product, scope=scope)
                            except RuntimeError:
                                fallback = native_reader()
                            else:
                                raise AssertionError("reader outage not exercised")
                        assert _json(fallback) == packet.payload
                        recovered = read_input(
                            consumer["id"],
                            product,
                            scope=scope,
                            as_of=now
                            if product in {"HIER_DATA", "COMPANY_DATA", "MACRO_DATA", "DOC_DATA"}
                            else None,
                            products=products,
                            store=internal,
                            audit=DecisionAuditRepository(internal)
                            if internal is not None
                            else None,
                        )
                        assert recovered.payload == packet.payload
                    proofs.append(
                        {
                            "product": product,
                            "route": routes[product],
                            "status": "passed" if passed else "failed",
                            "read_only": True,
                            "native_packet_equal": bool(equal),
                            "input_refs": refs[:200],
                            "payload_hash": digest(native) if native is not None else "",
                            "scope_hash": digest(scope),
                            "reason": reason,
                            "as_of": now.isoformat(),
                        }
                    )
                    scopes[product] = scope
                except Exception as exc:
                    if hasattr(exc, "errors"):
                        location = ",".join(
                            ".".join(str(v) for v in error["loc"]) for error in exc.errors()
                        )
                        reason = f"native_read_unavailable:{type(exc).__name__}:{location}"
                    else:
                        reason = f"native_read_unavailable:{type(exc).__name__}"
                    proofs.append(
                        {
                            "product": product,
                            "route": routes[product],
                            "status": "failed",
                            "read_only": True,
                            "native_packet_equal": False,
                            "input_refs": [],
                            "payload_hash": "",
                            "scope_hash": digest(scope),
                            "reason": reason,
                            "as_of": now.isoformat(),
                        }
                    )
                    scopes[product] = scope
            output.append(
                {
                    "consumer": consumer["id"],
                    "domain": consumer["domain"],
                    "contract_version": consumer["contract_version"],
                    "scope": scopes,
                    "rollback_status": "passed"
                    if all(p["status"] == "passed" for p in proofs)
                    else "failed",
                    "qualification": "ineligible_pending_full_evidence",
                    "proofs": proofs,
                }
            )
        assert structured.conn.total_changes == unstructured.conn.total_changes == 0
        if internal is not None:
            assert internal.conn.total_changes == 0
    finally:
        structured.close()
        unstructured.close()
        if internal is not None:
            internal.conn.close()
    return output


def mechanism_probes(root, drills):
    """Synthetic fault probes ONLY in an isolated assurance ledger."""
    manifest = yaml.safe_load(COVERAGE.read_text())
    paths = manifest["qualification_policy"]["required_fingerprint_paths"] + [
        "src/ats/data/contract_validation.py",
        "scripts/verify_dataflow_qualification.py",
    ]
    db = root / "assurance.sqlite"
    start = datetime.now(timezone.utc)
    scope = {
        "products": next(row for row in drills if row["consumer"] == "fundamental")["scope"],
        "purpose": "isolated-task4-mechanism",
        "data_as_of": start.isoformat(),
    }
    checks = []
    role = next(c for c in manifest["consumers"] if c["id"] == "fundamental")
    proof = next(row for row in drills if row["consumer"] == "fundamental")["proofs"]
    assert all(p["status"] == "passed" for p in proof), proof
    inputs = [
        {
            "input_id": name,
            "source_status": "succeeded",
            "run_id": "synthetic-source-run",
            "checked_at": start.isoformat(),
            "input_refs": ["synthetic-accepted-version"],
        }
        for name in manifest["qualification_policy"]["required_inputs"]["fundamental"]
    ]
    optional = {
        "input_id": "sec_edgar_filing_body",
        "source_status": "failed",
        "run_id": "synthetic-sec-failure-run",
        "task_id": "synthetic-sec-task",
        "checked_at": start.isoformat(),
        "reason": "synthetic_official_body_transport_failure",
        "stage": "official_body_fetch",
        "input_refs": [],
        "checks": {
            name: True
            for name in manifest["qualification_policy"]["optional_inputs"][
                "sec_edgar_filing_body"
            ]["required_checks"]
        },
    }

    def record(
        kind,
        *,
        who=role,
        at=start,
        outcome="passed",
        details=None,
        prereqs=None,
        selected_scope=scope,
        dependencies=paths,
    ):
        return assurance.record_evidence(
            domain_id=who["domain"],
            consumer_id=who["id"],
            evidence_type=kind,
            outcome=outcome,
            scope=selected_scope,
            as_of=at,
            command_summary="isolated Task 4 mechanism replay; no source qualification asserted",
            result_summary="synthetic state/negative probe; native fallback references reused",
            prerequisite_event_ids=prereqs,
            details=details,
            dependency_paths=(
                dependencies
                + manifest["qualification_policy"]
                .get("consumer_fingerprint_paths", {})
                .get(who["id"], [])
            ),
            db_path=db,
        )

    def query(*, who=role, selected_scope=scope, **kwargs):
        selected_db = kwargs.pop("db_path", db)
        return assurance.qualification(
            domain_id=who["domain"],
            consumer_id=who["id"],
            contract_version=who["contract_version"],
            scope=selected_scope,
            db_path=selected_db,
            **kwargs,
        )

    assert query()["status"] == "ineligible"
    checks.append("missing_ledger_fail_closed")
    ids = {}
    for kind in role["required_evidence"]:
        details = (
            {"inputs": inputs + [optional]}
            if kind == "completeness"
            else {"rollback": proof}
            if kind == "rollback"
            else {}
        )
        ids[kind] = record(kind, details=details)
    accepted = query()
    assert accepted["status"] == "eligible", accepted
    gap = accepted["accepted_nonblocking_gaps"][0]
    assert (
        gap["source_status"] == "failed"
        and gap["blocking"] is False
        and gap["source_verified"] is False
    )
    assert gap["task_id"] and gap["run_id"] and gap["policy_version"] == "sec-body-optional-v1"
    checks.append("sec_only_gap_eligible_with_original_failure_refs")
    for name in (
        "company_financials",
        "defeatbeta_earnings_transcript",
        "defeatbeta_sec_filing_index",
    ):
        changed = [
            {**row, "source_status": "failed", "reason": "synthetic_required_input_failure"}
            if row["input_id"] == name
            else row
            for row in inputs
        ]
        record("completeness", details={"inputs": changed + [optional]})
        assert query()["status"] == "ineligible"
        checks.append(f"{name}_failure_not_waived")
    record("completeness", details={"inputs": inputs + [{**optional, "checks": {}}]})
    assert query()["status"] == "ineligible"
    checks.append("unsafe_empty_sec_input_blocks_qualification")
    record("completeness", details={"inputs": inputs})
    assert query()["status"] == "ineligible"
    checks.append("missing_sec_status_not_hidden")
    record("completeness", details={"inputs": inputs + [optional]})
    assert query()["status"] == "eligible"
    record("rollback")
    assert query()["status"] == "ineligible"
    checks.append("passed_label_without_real_rollback_proof_rejected")
    record("rollback", details={"rollback": [proof[0]]})
    assert query()["status"] == "ineligible"
    checks.append("one_product_does_not_qualify_whole_role")
    record("rollback", details={"rollback": [{**row, "scope_hash": "0" * 64} for row in proof]})
    assert query()["status"] == "ineligible"
    checks.append("wrong_product_scope_cannot_reuse_rollback_evidence")
    record("rollback", details={"rollback": proof})
    assert query()["status"] == "eligible"
    record("read", prereqs=[ids["admission"]])
    record("admission")
    assurance.revoke_evidence(ids["admission"], reason="synthetic prereq revoke", db_path=db)
    result = query()
    assert any(r.startswith("prerequisite:revoked") for r in result["reasons"]), result
    checks.append("superseded_prerequisite_revocation_checked_recursively")
    record("read")
    assert query()["status"] == "eligible"
    record("read", outcome="failed")
    assert query()["status"] == "ineligible"
    checks.append("latest_failed_evidence_overrides_prior_pass")
    record("read", dependencies=["src/ats/data/assurance.py"])
    assert query()["status"] == "ineligible"
    checks.append("incomplete_code_registry_fingerprints_rejected")
    record("read")
    assert query()["status"] == "eligible"
    assert query(now=datetime.now(timezone.utc) + timedelta(days=31))["status"] == "ineligible"
    checks.append("evidence_ttl_not_report_age")
    assert query(selected_scope={**scope, "entity": "AMD"})["status"] == "ineligible"
    checks.append("exact_scope_isolation")
    copy = root / "coverage.yaml"
    copy.write_text(COVERAGE.read_text() + "\n# synthetic manifest change\n")
    assert query(coverage_path=copy)["status"] == "ineligible"
    checks.append("manifest_drift_fail_closed")
    with tempfile.TemporaryDirectory(prefix="task4-fingerprint-", dir=REPO_ROOT / "var") as name:
        dependency = Path(name) / "proof.txt"
        dependency.write_text("synthetic dependency v1")
        record("read", dependencies=paths + [str(dependency)])
        assert query()["status"] == "eligible"
        dependency.write_text("synthetic dependency v2")
        assert query()["status"] == "ineligible"
    checks.append("dependency_drift_fail_closed")
    record("read")
    for action in (
        "UPDATE dataflow_assurance_events SET outcome='passed'",
        "DELETE FROM dataflow_assurance_events",
    ):
        with sqlite3.connect(db) as conn:
            try:
                conn.execute(action)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("immutable evidence changed")
    checks.append("update_delete_rejected")
    with assurance._connect(db, writable=False) as conn:
        try:
            conn.execute("CREATE TABLE cannot_write(x)")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("readonly qualification connection writable")
    checks.append("readonly_query_connection")
    for reference in ("nonexistent", ids["admission"]):
        if reference == ids["admission"]:
            changed_scope = {**scope, "entity": "AMD"}
        else:
            changed_scope = scope
        try:
            record("read", prereqs=[reference], selected_scope=changed_scope)
        except ValueError:
            pass
        else:
            raise AssertionError("unknown or cross-scope prerequisite accepted")
    checks.append("unknown_cross_scope_prerequisites_rejected")
    for kwargs in (
        {"max_age_days": 0},
        {"environment_names": ["TOKEN=not-a-variable-name"]},
        {"details": {"raw_body": "not allowed"}},
    ):
        try:
            assurance.record_evidence(
                domain_id=role["domain"],
                consumer_id=role["id"],
                evidence_type="read",
                outcome="passed",
                scope=scope,
                as_of=start,
                command_summary="isolated probe",
                result_summary="validation probe",
                dependency_paths=paths,
                db_path=db,
                **kwargs,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid audit metadata accepted")
    checks.append("invalid_ttl_environment_values_and_raw_payload_rejected")
    legacy = root / "legacy-assurance.sqlite"
    with sqlite3.connect(legacy) as conn:
        conn.executescript(
            assurance._SCHEMA.replace(", details_json TEXT NOT NULL DEFAULT '{}'", "")
        )
    legacy_id = assurance.record_evidence(
        domain_id=role["domain"],
        consumer_id=role["id"],
        evidence_type="read",
        outcome="passed",
        scope=scope,
        as_of=start,
        command_summary="isolated legacy schema probe",
        result_summary="additive details migration; no old qualification promoted",
        dependency_paths=paths,
        db_path=legacy,
    )
    history = assurance.evidence_history(
        domain_id=role["domain"], consumer_id=role["id"], db_path=legacy
    )
    assert history[0]["event_id"] == legacy_id and history[0]["details"] == {}
    assert query(db_path=legacy)["status"] == "ineligible"
    checks.append("legacy_ledger_additive_migration_without_qualification_upgrade")
    sector = next(c for c in manifest["consumers"] if c["id"] == "sector")
    sector_scope = {
        "products": next(row for row in drills if row["consumer"] == "sector")["scope"],
        "purpose": "isolated-task4-mechanism",
    }
    sector_proofs = next(r for r in drills if r["consumer"] == "sector")["proofs"]
    for kind in sector["required_evidence"]:
        record(
            kind,
            who=sector,
            selected_scope=sector_scope,
            details={"rollback": sector_proofs} if kind == "rollback" else {},
        )
    assert query(who=sector, selected_scope=sector_scope)["status"] == "eligible"
    last_read = assurance.evidence_history(
        domain_id=role["domain"], consumer_id=role["id"], scope=scope, db_path=db
    )[0]["event_id"]
    assurance.revoke_evidence(last_read, reason="synthetic consumer isolation", db_path=db)
    assert (
        query()["status"] == "ineligible"
        and query(who=sector, selected_scope=sector_scope)["status"] == "eligible"
    )
    checks.append("revocation_does_not_contaminate_other_consumer")
    # No broker data is invented to pass an unverified fallback.
    for row in drills:
        if row["rollback_status"] != "passed":
            who = next(c for c in manifest["consumers"] if c["id"] == row["consumer"])
            for kind in who["required_evidence"]:
                record(
                    kind,
                    who=who,
                    selected_scope={
                        "products": row["scope"],
                        "purpose": "isolated-task4-mechanism",
                    },
                    details={"rollback": row["proofs"]} if kind in {"rollback", "fallback"} else {},
                )
            if "fallback" in who["required_evidence"] or "rollback" in who["required_evidence"]:
                assert (
                    query(
                        who=who,
                        selected_scope={
                            "products": row["scope"],
                            "purpose": "isolated-task4-mechanism",
                        },
                    )["status"]
                    == "ineligible"
                )
    checks.append("unverified_fallback_consumers_remain_ineligible")
    return {
        "checks": checks,
        "sec_exception_probe": accepted,
        "event_count": len(
            assurance.evidence_history(
                domain_id=role["domain"], consumer_id=role["id"], db_path=db, limit=1000
            )
        ),
        "ledger": str(db),
        "purpose": "synthetic_mechanism_not_production_qualification",
    }


def sec_null_and_publication_probes(data_path, root):
    """Exercise null/error handling and unchanged official identity/publication gates."""
    from ats.data.admission import CandidateDocument
    from ats.data.pipelines.unstructured.document_ingest import _publish_document
    from ats.data.pipelines.unstructured.sec import validate_filing

    source = PlatformUnstructuredRepository(data_path, writable=False)
    structured = SQLiteStructuredRepository(data_path, readonly=True)
    products = DataProducts(structured_repository=structured, unstructured_repository=source)
    now = datetime.now(timezone.utc)
    try:
        packet = read_input(
            "fundamental",
            "COMPANY_DATA",
            products=products,
            as_of=now,
            scope={
                "entity": "TASK4_NO_SEC_COVERAGE",
                "kind": "documents",
                "document_types": ["regulatory_filing", "company_release"],
            },
        )
        assert packet.status == "no_coverage" and packet.payload == [] and packet.gaps
        assert not packet.input_refs
        # No field asserts no risk or completed SEC review; actual source failure is
        # separately retained in the qualification gap, not invented by the read API.
        assert "risk" not in packet.model_dump() and "reviewed" not in packet.model_dump()
        documents = admitted_documents(repository=source, as_of=now, limit=10_000)
        cached = next(doc for doc in documents if doc.source == "sec_edgar_filing_body")
        bad_filing = {
            "symbol": "NVDA",
            "accession_number": "0001045810-26-000001",
            "cik": "1045810",
            "form_type": "10-Q",
            "filing_url": "https://example.invalid/search-result",
        }
        try:
            validate_filing(bad_filing)
        except ValueError:
            pass
        else:
            raise AssertionError("optional policy bypassed official identity gate")
        isolated = PlatformUnstructuredRepository(root / "sec-gate.sqlite", writable=True)
        candidate = CandidateDocument(
            expected_entity="NVDA",
            claimed_entity="NVDA",
            expected_semantic="regulatory_filing",
            claimed_semantic="regulatory_filing",
            target_period="",
            claimed_period="",
            text="too short",
            source="sec_edgar_filing_body",
            external_id="synthetic-short-sec",
            source_url=cached.source_url,
            min_chars=100,
            title="synthetic refusal probe",
        )
        with patch.dict("os.environ", {"ATS_DOCS_ROOT": str(root / "sec-docs")}):
            status, document_id = _publish_document(isolated, candidate)
        assert status == "quarantined" and not document_id
        assert not isolated.documents()
        isolated.close()
        return {
            "null_packet": "no_coverage_with_gap_not_risk_clearance",
            "invalid_official_identity": "rejected",
            "invalid_body": "quarantined_not_published",
            "cached_sec_version_ref": f"{cached.document_id}@{cached.version_id}",
            "policy_changed_quality_gate": False,
        }
    finally:
        source.close()
        structured.close()


def cli_probes(root):
    """Exercise record/history/query/revoke CLI against an isolated DB."""
    paths = yaml.safe_load(COVERAGE.read_text())["qualification_policy"][
        "required_fingerprint_paths"
    ]
    db = root / "cli-assurance.sqlite"
    scope = {"purpose": "isolated-task4-cli"}
    common = [
        "--consumer",
        "sector",
        "--domain",
        "hierarchy",
        "--contract-version",
        "target-dataflow-v1",
        "--scope-json",
        json.dumps(scope),
        "--db",
        str(db),
    ]

    def run(action, args=()):
        result = subprocess.run(
            [sys.executable, "-m", "ats.runtime.cli", "data", "assurance", action, *common, *args],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=REPO_ROOT,
            check=True,
        )
        return json.loads(result.stdout)

    record_args = [
        "--evidence-type",
        "read",
        "--outcome",
        "passed",
        "--as-of",
        datetime.now(timezone.utc).isoformat(),
        "--command-summary",
        "isolated CLI probe",
        "--summary",
        "synthetic CLI argument verification",
        "--environment-name",
        "ATS_DATA_DB_PATH",
        "--evidence-details-json",
        json.dumps({"inputs": []}),
    ]
    for path in paths:
        record_args.extend(["--fingerprint-path", path])
    event = run("record", record_args)["event_id"]
    history = run("history")
    assert history[0]["event_id"] == event and history[0]["details"] == {"inputs": []}
    assert run("query")["status"] == "ineligible"
    revoked = run("revoke", ["--event-id", event, "--reason", "synthetic CLI revocation"])
    assert revoked["event_id"] != event
    assert "revoked:read" in run("query")["reasons"]
    assert len(run("history")) == 2
    return {
        "record_details_passthrough": "passed",
        "history": "passed",
        "query_fail_closed": "passed",
        "revoke_append_only": "passed",
        "production_write": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-cache", type=Path, default=REPO_ROOT / "var/data.sqlite")
    parser.add_argument("--internal-cache", type=Path, default=REPO_ROOT / "var/ats.sqlite")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="ats-task4-"))
    if args.output:
        args.output.write_text(
            json.dumps({"passed": False, "status": "running_or_incomplete"}) + "\n"
        )
    with patch.object(
        socket.socket, "connect", side_effect=AssertionError("Task 4 must not collect data")
    ):
        assert validate_target_contract()["valid"]
        drills = cached_fallback_drills(args.data_cache, args.internal_cache)
        probes = mechanism_probes(root, drills)
        sec_probes = sec_null_and_publication_probes(args.data_cache, root)
        cli = cli_probes(root)
    report = {
        "schema_version": "target-dataflow-task4-v1",
        "passed": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "network_calls": 0,
        "production_cutover": False,
        "production_qualification_granted": False,
        "consumer_drills": drills,
        "mechanism": probes,
        "sec_consumer_publication_checks": sec_probes,
        "cli": cli,
        "manifest_hash": hashlib.sha256(COVERAGE.read_bytes()).hexdigest(),
        "code_config_fingerprints": assurance._dependencies(
            [
                "src/ats/data/assurance.py",
                "src/ats/data/consumer_api.py",
                "src/ats/data/contract_validation.py",
                "src/ats/runtime/cli.py",
                "scripts/verify_dataflow_qualification.py",
                "config/data/structured.yaml",
                "config/data/unstructured.yaml",
                "config/data/target_dataflow_coverage.yaml",
                "config/risk.yaml",
                "src/ats/execution/state_api.py",
                "src/ats/execution/authorization.py",
                "src/ats/decision/repository.py",
                "src/ats/execution/clerk.py",
            ]
        ),
    }
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": True,
                "consumer_drills": len(drills),
                "readable_native_fallback_roles": [
                    r["consumer"] for r in drills if r["rollback_status"] == "passed"
                ],
                "unverified_fallback_roles": [
                    r["consumer"] for r in drills if r["rollback_status"] != "passed"
                ],
                "mechanism_checks": len(probes["checks"]),
                "network_calls": 0,
                "report": str(args.output),
                "isolated_ledger": probes["ledger"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
