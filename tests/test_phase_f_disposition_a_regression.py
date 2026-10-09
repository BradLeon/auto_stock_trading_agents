"""Scoped disposition and all-consumer evidence invalidation; no production writes."""
import hashlib
import json
from datetime import datetime, timezone

import pytest
import yaml

from ats.data import assurance
from ats.workflow import assurance_surface as surface_module
from ats.workflow import consumer_disposition as cd


def test_actual_artifact_binding_and_scope_isolation(tmp_path):
    artifact = tmp_path / "records.json"
    artifact.write_text(json.dumps({"run_id": "fixture", "history": "missing"}))
    refs = [{"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
             "locator": "run_id=fixture"}]
    broken = cd.dispose_recorded_scope(consumer_id="fundamental", scope={"kind": "entity", "id": "COHR"},
        evidence=refs, history_broken=["old document version"])
    clean = cd.dispose_recorded_scope(consumer_id="technical", scope={"kind": "entity", "id": "NVDA"},
        evidence=refs, optional_inputs=["sec_edgar_filing_body"])
    cd.assert_scope_isolation([broken, clean])
    assert broken.blocks_cutover and not broken.auto_released
    assert not clean.blocks_cutover and clean.disposition == "ok"
    assert not clean.production_qualification_granted
    artifact.write_text("changed")
    with pytest.raises(cd.DispositionError, match="drift"):
        cd.dispose_recorded_scope(consumer_id="technical", scope=clean.scope, evidence=refs)


def test_absent_evidence_and_adaptation_cannot_be_waived():
    portfolio = cd.dispose_recorded_scope(consumer_id="macro", scope={"kind": "portfolio", "id": ""}, evidence=[])
    assert portfolio.disposition == "pending"
    with pytest.raises(cd.DispositionError, match="explicit scope"):
        cd.dispose_recorded_scope(consumer_id="fundamental", scope={"kind": "entity", "id": ""}, evidence=[])
    missing = cd.dispose_recorded_scope(consumer_id="clerk", scope={"kind": "account", "id": "paper"},
        evidence=[], legacy_defects=["substep failure not reflected in completed"])
    assert missing.disposition == "pending" and missing.blocks_cutover
    assert not any("verified as isolated" in reason for reason in missing.reasons)
    required = cd.dispose_recorded_scope(consumer_id="chief", scope={"kind": "portfolio", "id": "paper"},
        evidence=[], engineering_gaps=["publication adapter"], legacy_defects=["legacy model failure"])
    assert required.disposition == "partial" and required.blocks_cutover
    assert "publication adapter" in required.partial_ranges


def test_shared_governed_api_is_in_every_role_fingerprint():
    surface = surface_module.load_surface()
    path = "src/ats/data/products/unstructured.py"
    assert all(path in surface.paths_for(role) for role in surface.consumers)
    assert len(surface.consumers_touched_by(path)) == 10


@pytest.mark.parametrize("consumer", surface_module.load_surface().consumers)
@pytest.mark.parametrize("mutation", ["manifest", "shared_api"])
def test_all_ten_old_fingerprints_fail_without_erasing_history(tmp_path, monkeypatch, consumer, mutation):
    """Real ledger records, then alter a COPY of the manifest; no invented eligibility."""
    surface = surface_module.load_surface()
    manifest = yaml.safe_load(surface_module.MANIFEST.read_text())
    role = next(row for row in manifest["consumers"] if row["id"] == consumer)
    ledger = tmp_path / "assurance.sqlite"
    copied = tmp_path / "coverage.yaml"
    copied.write_text(yaml.safe_dump(manifest))
    import shutil
    import ats.config
    original_root = ats.config.REPO_ROOT
    for path in surface.all_paths():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original_root / path, destination)
    monkeypatch.setattr(ats.config, "REPO_ROOT", tmp_path)
    scope = {"kind": "entity", "id": "COHR", "purpose": "isolated-policy-regression",
             "products": {product: {"product": product} for product in role["products"]}}
    stamp = datetime.now(timezone.utc)
    policy = manifest["qualification_policy"]
    proofs = [{"product": product, "route": policy["rollback_routes"][product],
        "status": "passed", "read_only": True, "native_packet_equal": True,
        "input_refs": ["synthetic-mechanism-ref"], "payload_hash": "a" * 64,
        "scope_hash": hashlib.sha256(json.dumps(scope["products"][product], sort_keys=True).encode()).hexdigest(),
        "as_of": stamp.isoformat()} for product in role["products"]]
    inputs = [{"input_id": name, "source_status": "succeeded", "run_id": "synthetic-mechanism",
        "checked_at": stamp.isoformat(), "input_refs": ["synthetic-ref"]} for name in
        set(policy.get("required_inputs", {}).get(consumer, [])) |
        set(policy.get("required_status_inputs", {}).get(consumer, []))]
    for kind in role["required_evidence"]:
        event = assurance.record_evidence(domain_id=role["domain"], consumer_id=consumer,
            evidence_type=kind, outcome="passed", scope=scope, as_of=stamp,
            dependency_paths=list(surface.paths_for(consumer)), coverage_path=copied,
            details={"rollback": proofs, "inputs": inputs},
            db_path=ledger, command_summary="isolated mechanism", result_summary="synthetic mechanism only")
    args = dict(domain_id=role["domain"], consumer_id=consumer,
        contract_version=role["contract_version"], scope=scope, db_path=ledger, coverage_path=copied)
    positive = assurance.qualification(**args)
    assert positive["status"] == "eligible", positive["reasons"]
    original = assurance.evidence_history(domain_id=role["domain"], consumer_id=consumer, db_path=ledger)
    if mutation == "manifest":
        manifest["schema_version"] += 1
        copied.write_text(yaml.safe_dump(manifest))
    else:
        shared = tmp_path / "src/ats/data/consumer_api.py"
        shared.write_text(shared.read_text() + "\n# isolated drift probe\n")
    result = assurance.qualification(**args)
    assert result["status"] == "ineligible"
    assert any(("manifest_drift" if mutation == "manifest" else "dependency_drift")
               in reason for reason in result["reasons"])
    assert assurance.evidence_history(domain_id=role["domain"], consumer_id=consumer, db_path=ledger) == original
    assert original[0]["event_id"] == event


@pytest.mark.parametrize("consumer", ["risk", "trader"])
def test_a_contract_upgrade_does_not_inherit_v1_events(tmp_path, consumer):
    manifest = yaml.safe_load(surface_module.MANIFEST.read_text())
    role = next(row for row in manifest["consumers"] if row["id"] == consumer)
    copied = tmp_path / "coverage.yaml"
    ledger = tmp_path / "old-contract.sqlite"
    role["contract_version"] = "target-dataflow-v1"
    copied.write_text(yaml.safe_dump(manifest))
    scope = {"kind": "entity", "id": "COHR", "purpose": "isolated-old-contract"}
    event = assurance.record_evidence(domain_id=role["domain"], consumer_id=consumer,
        evidence_type=role["required_evidence"][0], outcome="passed", scope=scope,
        as_of=datetime.now(timezone.utc), dependency_paths=list(surface_module.load_surface().paths_for(consumer)),
        coverage_path=copied, db_path=ledger, command_summary="isolated old contract",
        result_summary="synthetic old contract mechanism")
    role["contract_version"] = "target-dataflow-v2"
    copied.write_text(yaml.safe_dump(manifest))
    result = assurance.qualification(domain_id=role["domain"], consumer_id=consumer,
        contract_version="target-dataflow-v2", scope=scope, db_path=ledger, coverage_path=copied)
    assert result["status"] == "ineligible"
    assert all(value is None for value in result["evidence"].values())
    history = assurance.evidence_history(domain_id=role["domain"], consumer_id=consumer, db_path=ledger)
    assert history[0]["event_id"] == event
    assert history[0]["contract_version"] == "target-dataflow-v1"
