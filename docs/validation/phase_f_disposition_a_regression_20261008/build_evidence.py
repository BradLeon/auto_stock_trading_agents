"""Preserve actual isolated runs and bind dispositions to their records."""
import hashlib
import json
import runpy
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import yaml

from ats.config import REPO_ROOT
from ats.workflow.assurance_surface import load_surface, fingerprint
from ats.workflow.consumer_disposition import dispose_recorded_scope, assert_scope_isolation


def main():
    folder = Path(__file__).resolve().parent
    results = {}
    failures = []
    for case in ET.parse(folder / "business-regression.xml").iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failures.append(case.attrib["name"])
            continue
        props = case.find("properties")
        if props is not None:
            results[case.attrib["name"]] = {p.attrib["name"]: p.attrib["value"] for p in props}
    evidence = folder / "business-evidence.json"
    assert failures == ["test_quote_fail_closed[changes14-future]"], failures
    corrected = ET.parse(folder / "trader-current.xml")
    assert all(case.find("failure") is None and case.find("error") is None for case in corrected.iter("testcase"))
    for case in ET.parse(folder / "business-current.xml").iter("testcase"):
        assert case.find("failure") is None and case.find("error") is None, case.attrib
        props = case.find("properties")
        if props is not None:
            results[case.attrib["name"]] = {p.attrib["name"]: p.attrib["value"] for p in props}
    evidence.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
    lineage = results["test_actual_opinion_edges_and_published_lineage"]
    published = json.loads(lineage["published"])
    chain = json.loads(results["test_complete_business_execution_and_recovery[1000-False]"]["chain"])
    assert chain["accepted"] and len(chain["ledger_fills"]) == 2
    assert chain["complete"]["status"] == "completed"
    assert not chain["complete"]["errors"]
    assert not chain["complete"]["steps"]["reconcile"]["errors"]
    assert chain["complete"]["steps"]["performance"]["recorded"] is True
    assert all(row["origin"] == "system" and row["approval_id"] for row in chain["ledger_fills"])
    surface = load_surface()
    manifest = yaml.safe_load((REPO_ROOT / surface.manifest).read_text())
    before = json.loads((folder / "before.json").read_text())
    rows, minimum = [], []
    role_names = {"layer_analysis": "layer", "information_brief": "information",
        "sector_allocation": "sector", "fundamental_expectation_update": "fundamental",
        "macro_regime": "macro", "technical_signal": "technical"}
    # Use the exact envelopes produced by the actual run, including their scope.
    projections = {}
    for envelope in published:
        role = envelope["agent_role"].split(".")[0]
        consumer = role_names.get(role, role)
        if consumer not in surface.consumers:
            matches = [name for name in surface.consumers if role.startswith(name)]
            assert len(matches) == 1, role
            consumer = matches[0]
        projections[consumer] = envelope
    for consumer in surface.consumers:
        record = next(row for row in manifest["consumers"] if row["id"] == consumer)
        if consumer in projections:
            envelope = projections[consumer]
            assert envelope["input_refs"] and envelope["content_hash"]
            scope = envelope["scope"]
            locator = "test_actual_opinion_edges_and_published_lineage/published/" + envelope["projection_id"]
        else:
            scope = {"kind": "account", "id": "DU1", "entity": "COHR", "cycle_id": chain["cycle"]["cycle_id"]}
            locator = "test_complete_business_execution_and_recovery[1000-False]/chain"
        findings = {}
        if consumer == "macro":
            findings["coverage_gaps"] = ["controlled run: optional FactSet release unavailable; not a production coverage check"]
        if consumer == "layer":
            findings["coverage_gaps"] = ["actual Layer output retains evidence-missing finding in controlled inputs"]
        if consumer == "clerk":
            findings["legacy_defects"] = ["clerk_run may record completed when returned step errors/recorded=false do not raise; require step/fact readback"]
        disposition = dispose_recorded_scope(consumer_id=consumer, scope=scope,
            evidence=[{"path": str(evidence.relative_to(REPO_ROOT)), "sha256": sha, "locator": locator}], **findings)
        rows.append(disposition)
        minimum.append({"consumer": consumer, "contract": record["contract_version"], "scope": scope,
            "dependency_paths": surface.paths_for(consumer), "required_evidence": record["required_evidence"],
            "ttl_days": record["evidence_ttl_days"], "runtime_verified_in": locator,
            "qualification": "not_registered; requires final 6.1 baseline and 6.4 append",
            "reuse": "accepted source/version/hash and archived run material; no full collection required"})
    assert len(rows) == 10
    assert_scope_isolation(rows)
    current = fingerprint(surface.all_paths())
    changed = {path: {"before": before["files"].get(path), "after": sha,
                "consumers": "all" if path == surface.manifest else surface.consumers_touched_by(path)}
               for path, sha in current.items() if before["files"].get(path) != sha}
    with zipfile.ZipFile(folder / "before_sources.zip") as archive:
        old_manifest = yaml.safe_load(archive.read(surface.manifest))
    assert old_manifest["qualification_policy"]["optional_inputs"] == manifest["qualification_policy"]["optional_inputs"]
    assert old_manifest["data_input_contracts"] == manifest["data_input_contracts"]
    assert old_manifest["consumers"] == manifest["consumers"]
    (folder / "dispositions.json").write_text(json.dumps({"scope_kind": "isolated controlled inputs",
        "production_qualification_granted": False, "rows": [r.as_row() for r in rows],
        "policy_unchanged": ["optional", "no_coverage", "FactSet", "partial"],
        "untested_scopes": "production full universe/history; TWS; real broker writes",
        "engineering_dependencies": "groups 4/5 remain mandatory; independent from legacy business defects"}, indent=2, ensure_ascii=False))
    (folder / "minimum-reverification.json").write_text(json.dumps({"manifest_hash": current[surface.manifest],
        "protected_paths": current, "added_paths": sorted(set(surface.all_paths()) - set(before["protected_paths"])),
        "changed_files": changed, "consumers": minimum}, indent=2))
    with tempfile.TemporaryDirectory(prefix="phase-f-7717-backup-") as temporary:
        with zipfile.ZipFile(folder / "execution_records.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name, props in results.items():
                if "chain" not in props:
                    continue
                root = Path(json.loads(props["chain"])["accepted"]["isolation_root"])
                assert root.is_dir()
                for database in root.rglob("*.sqlite"):
                    dest = Path(temporary) / database.name
                    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as reader:
                        with sqlite3.connect(dest) as writer:
                            reader.backup(writer)
                    archive.write(dest, name + "/" + str(database.relative_to(root)))
                for config in (root.parent / "config").rglob("*.yaml"):
                    archive.write(config, name + "/config/" + str(config.relative_to(root.parent / "config")))
                for path in root.rglob("*"):
                    if path.is_file() and path.suffix in {".json", ".txt", ".md"}:
                        archive.write(path, name + "/" + str(path.relative_to(root)))
    production = runpy.run_path(str(REPO_ROOT / "docs/validation/phase_f_recovery_20261007/preflight.py"))["read_databases"]()
    assert production == before["production"]
    print(json.dumps({"consumers": len(rows), "protected_paths": len(current), "production_inventory_unchanged": True}))


if __name__ == "__main__":
    main()
