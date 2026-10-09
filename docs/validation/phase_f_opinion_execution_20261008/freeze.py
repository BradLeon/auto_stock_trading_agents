"""Freeze actual run records and final implementation; never writes production."""
import hashlib
import json
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ats.config import REPO_ROOT
from ats.workflow.assurance_surface import load_surface


def main():
    directory = Path(__file__).resolve().parent
    results = json.loads((directory / "business-evidence.json").read_text())
    with tempfile.TemporaryDirectory(prefix="phase-f-execution-backup-") as temporary:
        temporary = Path(temporary)
        with zipfile.ZipFile(directory / "execution_records.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name, properties in results.items():
                if "chain" not in properties:
                    continue
                chain = json.loads(properties["chain"])
                root = Path(chain["accepted"]["isolation_root"])
                assert root.is_dir(), root
                case = name.replace("[", "-").replace("]", "")
                for database in root.rglob("*.sqlite"):
                    destination = temporary / (case + "-" + database.name)
                    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as reader:
                        with sqlite3.connect(destination) as writer:
                            reader.backup(writer)
                    archive.write(destination, case + "/" + str(database.relative_to(root)))
                for config in (root.parent / "config").rglob("*.yaml"):
                    archive.write(config, case + "/config/" + str(config.relative_to(root.parent / "config")))
                for material in root.rglob("*"):
                    if material.is_file() and material.suffix in {".txt", ".md", ".json"}:
                        archive.write(material, case + "/" + str(material.relative_to(root)))
                archive.writestr(case + "/origin.json", json.dumps({"isolation_root": str(root),
                    "source": "actual final acceptance database, backed up after subprocess recovery"}))

    index = subprocess.run([sys.executable, "-m", "ats.runtime.cli", "intake", "evidence-index"],
                           capture_output=True, text=True, check=True)
    (directory / "current-static-index.json").write_text(json.dumps(json.loads(index.stdout), indent=2))
    surface = load_surface()
    protected = {surface.manifest, *(p for consumer in surface.consumers for p in surface.paths_for(consumer))}
    names = {str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "src").rglob("*.py")} | protected
    names.update(["uv.lock", "pyproject.toml", "tests/conftest.py", "tests/test_phase_f_opinion_execution.py",
                  "tests/test_phase_f_research_gate.py", "tests/test_phase_f_safe_reads.py",
                  "tests/test_phase_f_trader_a.py", "tests/test_phase_e_dispatcher.py"])
    names.update(str(p.relative_to(REPO_ROOT)) for p in
                 (REPO_ROOT / "openspec/changes/implement-phase-f-shadow-run-and-cutover").rglob("*.md"))
    names.update("docs/validation/PHASE_F_" + suffix + ".md" for suffix in
                 ["SUMMARY", "GROUP_PROGRESS", "ACCEPTANCE", "CUTOVER_RUNBOOK", "OPINION_AND_EXECUTION_2026-10-08"])
    files = {name: hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}
    with zipfile.ZipFile(directory / "after_sources.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(REPO_ROOT / name, name)
    inventory = runpy.run_path(str(REPO_ROOT / "docs/validation/phase_f_recovery_20261007/preflight.py"))["read_databases"]()
    assert inventory == json.loads((directory / "before.json").read_text())["production"]
    counts = {}
    for name in ["acceptance-current.xml", "final-core-regression.xml", "regression.xml", "guards.xml",
                 "final-record-guards.xml"]:
        suite = ET.parse(directory / name).find("testsuite")
        counts[name] = {key: suite.attrib[key] for key in ["tests", "failures", "errors", "skipped"]}
    artifacts = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()
                 if p.is_file() and p.name != "after.json"}
    (directory / "after.json").write_text(json.dumps({"files": files, "protected_paths": sorted(protected),
        "production": inventory, "artifacts": artifacts, "tests": counts}, indent=2))
    print(json.dumps({"source_paths":len(files),"protected_paths":len(protected),"production_unchanged":True}))


if __name__ == "__main__":
    main()
