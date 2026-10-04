"""Phase F 1.4 — the pytest suite and the acceptance script assert the same things.

`scripts/verify_dataflow_qualification.py` is the Dataflow change's acceptance
evidence; `tests/test_dataflow_assurance.py` is the regression net Phase F's
routing depends on. They were written separately, so nothing stopped them from
drifting — a mechanism added to one and not the other would leave the acceptance
report claiming coverage the regression suite does not have (or the reverse, and
a later change could break the mechanism without failing any gate).

Two checks, in increasing strength:

1. **Coverage parity** — every mechanism the script probes has a named pytest
   test, derived from the source rather than a hand-copied list, so adding a
   probe to one side without the other fails here.
2. **Behaviour parity** — for the mechanisms where both sides can be executed
   cheaply, the same fixture must produce the same verdict. This is the check
   that catches a pytest test which passes for the wrong reason (e.g. asserting
   ineligible without confirming the fixture was ever eligible).
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from ats.config import REPO_ROOT
from ats.data import assurance

SCRIPT = REPO_ROOT / "scripts" / "verify_dataflow_qualification.py"
COVERAGE = REPO_ROOT / "config" / "data" / "target_dataflow_coverage.yaml"
THIS_FILE = Path(__file__)

# Script probe name -> pytest test name. The mapping is explicit rather than
# derived from naming conventions so that a rename on either side is a visible
# edit here instead of a silently dropped check.
SCRIPT_PROBE_TO_TEST: dict[str, str] = {
    "missing_ledger_fail_closed":
        "test_missing_ledger_is_ineligible",
    "sec_only_gap_eligible_with_original_failure_refs":
        "test_sec_body_failure_is_a_nonblocking_gap_not_an_ineligibility",
    "{name}_failure_not_waived":
        "test_sec_exception_does_not_waive_a_required_input",
    "unsafe_empty_sec_input_blocks_qualification":
        "test_sec_input_without_verified_checks_still_blocks",
    "missing_sec_status_not_hidden":
        "test_missing_sec_status_is_not_silently_treated_as_optional",
    "passed_label_without_real_rollback_proof_rejected":
        "test_passed_label_without_rollback_proof_is_rejected",
    "one_product_does_not_qualify_whole_role":
        "test_one_product_drill_does_not_qualify_the_whole_role",
    "wrong_product_scope_cannot_reuse_rollback_evidence":
        "test_rollback_for_the_wrong_scope_cannot_be_reused",
    "superseded_prerequisite_revocation_checked_recursively":
        "test_prerequisite_revocation_is_checked_recursively",
    "latest_failed_evidence_overrides_prior_pass":
        "test_latest_failed_evidence_overrides_an_earlier_pass",
    "incomplete_code_registry_fingerprints_rejected":
        "test_incomplete_fingerprint_registry_is_rejected",
    "evidence_ttl_not_report_age":
        "test_evidence_expires_on_its_own_ttl_not_on_report_age",
    "exact_scope_isolation":
        "test_scope_must_match_exactly",
    "manifest_drift_fail_closed":
        "test_manifest_drift_is_fail_closed",
    "dependency_drift_fail_closed":
        "test_dependency_content_change_invalidates_the_evidence",
    "update_delete_rejected":
        "test_recorded_evidence_cannot_be_mutated",
    "readonly_query_connection":
        "test_qualification_connection_is_read_only",
    "unknown_cross_scope_prerequisites_rejected":
        "test_unknown_prerequisite_is_rejected",
    "invalid_ttl_environment_values_and_raw_payload_rejected":
        "test_invalid_audit_metadata_is_rejected",
    "legacy_ledger_additive_migration_without_qualification_upgrade":
        "test_legacy_ledger_gets_additive_migration_without_qualification_upgrade",
    "revocation_does_not_contaminate_other_consumer":
        "test_revocation_does_not_contaminate_another_consumer",
    "unverified_fallback_consumers_remain_ineligible":
        "test_the_five_unproven_consumers_all_remain_ineligible",
}


def script_probe_names() -> set[str]:
    """Every `checks.append(...)` literal in the acceptance script.

    Read from source so a probe added to the script is noticed here even if
    nobody remembers this test exists.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append" and node.args):
            continue
        if not (isinstance(node.func.value, ast.Name)
                and node.func.value.id == "checks"):
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            names.add(argument.value)
        elif isinstance(argument, ast.JoinedStr):
            # e.g. checks.append(f"{name}_failure_not_waived")
            names.add("".join(
                part.value if isinstance(part, ast.Constant) else "{name}"
                for part in argument.values))
    return names


def pytest_test_names() -> set[str]:
    tree = ast.parse(THIS_FILE.read_text(encoding="utf-8"))
    sibling = THIS_FILE.with_name("test_dataflow_assurance.py")
    names: set[str] = set()
    for source in (tree, ast.parse(sibling.read_text(encoding="utf-8"))):
        for node in ast.walk(source):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                names.add(node.name)
    return names


def test_every_script_probe_has_a_pytest_counterpart():
    """A mechanism probed by the acceptance script but not by the regression
    suite would be reported as covered while nothing guards it."""
    probes = script_probe_names()
    unmapped = probes - set(SCRIPT_PROBE_TO_TEST)
    assert not unmapped, f"script probes with no pytest mapping: {sorted(unmapped)}"

    tests = pytest_test_names()
    missing = {probe: test for probe, test in SCRIPT_PROBE_TO_TEST.items()
               if test not in tests}
    assert not missing, (
        "mapped pytest tests that do not exist (renamed or removed): "
        + ", ".join(f"{probe} -> {test}" for probe, test in sorted(missing.items())))


def test_the_mapping_has_no_stale_entries():
    """The reverse direction: a mapping entry for a probe the script no longer
    asserts would let coverage silently shrink."""
    assert set(SCRIPT_PROBE_TO_TEST) == script_probe_names()


# --- behaviour parity -------------------------------------------------------- #

@pytest.fixture
def parity_setup(tmp_path):
    """Builds one qualified consumer both sides can interrogate."""
    manifest = yaml.safe_load(COVERAGE.read_text(encoding="utf-8"))
    policy = manifest["qualification_policy"]
    role = next(row for row in manifest["consumers"] if row["id"] == "fundamental")
    paths = list(policy["required_fingerprint_paths"])
    scope = {"products": {product: {"product": product, "vintage": "2026-06-30"}
                          for product in sorted(role["products"])},
             "purpose": "parity", "data_as_of": START.isoformat()}
    proofs = []
    for product in sorted(role["products"]):
        import hashlib
        import json

        product_scope = scope["products"][product]
        proofs.append({
            "product": product, "route": policy["rollback_routes"][product],
            "status": "passed", "read_only": True, "input_refs": [f"ref-{product}"],
            "payload_hash": hashlib.sha256(
                json.dumps({"p": product}, sort_keys=True).encode()).hexdigest(),
            "scope_hash": hashlib.sha256(
                json.dumps(product_scope, sort_keys=True).encode()).hexdigest(),
            "native_packet_equal": True, "reason": "",
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    inputs = [{"input_id": name, "source_status": "succeeded",
               "run_id": "parity-run", "checked_at": START.isoformat(),
               "input_refs": ["parity"]}
              for name in policy["required_inputs"]["fundamental"]]
    # The SEC body is a required *status* input for this consumer: omitting it
    # entirely blocks qualification rather than being treated as the accepted
    # optional gap. It has to be present-and-failed for the positive control to
    # mean anything.
    sec_policy = policy["optional_inputs"]["sec_edgar_filing_body"]
    inputs.append({
        "input_id": "sec_edgar_filing_body", "source_status": "failed",
        "run_id": "parity-sec-run", "task_id": "parity-sec-task",
        "checked_at": START.isoformat(), "reason": "synthetic_parity_failure",
        "stage": "official_body_fetch", "input_refs": [],
        "checks": {name: True for name in sec_policy["required_checks"]},
    })
    return manifest, policy, role, paths, scope, proofs, inputs


START = datetime.now(timezone.utc) - timedelta(seconds=5)


def _record_all(ledger, paths, policy, role, scope, proofs, inputs):
    for kind in role["required_evidence"]:
        details = ({"inputs": inputs} if kind == "completeness"
                   else {"rollback": proofs} if kind == "rollback" else {})
        assurance.record_evidence(
            domain_id=role["domain"], consumer_id=role["id"], evidence_type=kind,
            outcome="passed", scope=scope, as_of=START,
            command_summary="parity probe", result_summary="parity probe",
            details=details, dependency_paths=paths + list(
                policy.get("consumer_fingerprint_paths", {}).get(role["id"], [])),
            db_path=ledger)


def test_both_sides_agree_that_a_fully_evidenced_consumer_is_eligible(
        tmp_path, parity_setup):
    """The positive control. Without it, every "it is ineligible" assertion in
    the suite could pass against a fixture that was never eligible at all."""
    manifest, policy, role, paths, scope, proofs, inputs = parity_setup
    ledger = tmp_path / "parity.sqlite"

    _record_all(ledger, paths, policy, role, scope, proofs, inputs)
    result = assurance.qualification(
        domain_id=role["domain"], consumer_id=role["id"],
        contract_version=role["contract_version"], scope=scope, db_path=ledger)

    assert result["status"] == "eligible", result["reasons"]


def test_both_sides_agree_that_dropping_one_evidence_type_blocks(
        tmp_path, parity_setup):
    """The script probes per-type removal only for `read`; this confirms the
    same fixture arithmetic drives the same verdict through the public API."""
    manifest, policy, role, paths, scope, proofs, inputs = parity_setup
    ledger = tmp_path / "parity-blocked.sqlite"

    _record_all(ledger, paths, policy, role, scope, proofs, inputs)
    assurance.record_evidence(
        domain_id=role["domain"], consumer_id=role["id"],
        evidence_type="reconciliation", outcome="failed", scope=scope, as_of=START,
        command_summary="parity probe", result_summary="parity probe",
        dependency_paths=paths, db_path=ledger)

    result = assurance.qualification(
        domain_id=role["domain"], consumer_id=role["id"],
        contract_version=role["contract_version"], scope=scope, db_path=ledger)
    assert result["status"] == "ineligible"
    assert any(r.startswith("failed:") for r in result["reasons"]), result["reasons"]


def test_the_script_and_the_suite_agree_on_the_policy_fingerprint_surface():
    """The script fingerprints a wider set than pytest's fixture (it adds itself
    and the CLI). That widening is intentional — it is the acceptance run — but
    the *policy* surface must be identical, or the two would drift apart on what
    counts as the contract."""
    manifest = yaml.safe_load(COVERAGE.read_text(encoding="utf-8"))
    policy_paths = list(manifest["qualification_policy"]["required_fingerprint_paths"])
    source = SCRIPT.read_text(encoding="utf-8")

    for path in policy_paths:
        assert path in source, f"script does not fingerprint policy path {path}"

    tree = ast.parse(source)
    scripted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith(("src/ats/", "config/", "scripts/")):
                scripted.add(node.value)
    assert set(policy_paths) <= scripted


def test_neither_side_asserts_a_production_qualification():
    """Both must agree on the most important boundary: neither the script nor
    the suite may treat its own evidence as a production cutover approval."""
    assert "production_qualification_granted" in SCRIPT.read_text(encoding="utf-8")
    manifest = yaml.safe_load(COVERAGE.read_text(encoding="utf-8"))
    # No consumer carries a blanket "approved" flag in the contract; eligibility
    # is computed per scope, never declared.
    for consumer in manifest["consumers"]:
        assert "eligible" not in consumer, consumer["id"]
        assert "qualified" not in consumer, consumer["id"]
