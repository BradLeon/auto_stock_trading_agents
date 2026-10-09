"""Freeze final source/record bytes, with read-only production inventory."""
import hashlib
import json
import runpy
import zipfile
from pathlib import Path
from ats.config import REPO_ROOT
from ats.workflow.assurance_surface import load_surface


def main():
    folder = Path(__file__).resolve().parent
    before = json.loads((folder / "before.json").read_text())
    production = runpy.run_path(str(REPO_ROOT / "docs/validation/phase_f_recovery_20261007/preflight.py"))["read_databases"]()
    assert production == before["production"]
    paths = set(before["files"])
    paths.update(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "tests").rglob("*.py"))
    paths.update(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "docs/validation").glob("PHASE_F_*.md"))
    paths.update(str(p.relative_to(REPO_ROOT)) for p in folder.glob("*.py"))
    files = {p: hashlib.sha256((REPO_ROOT / p).read_bytes()).hexdigest() for p in sorted(paths)}
    with zipfile.ZipFile(folder / "after_sources.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(REPO_ROOT / path, path)
    artifacts = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()
                 if p.is_file() and p.name != "after.json"}
    (folder / "after.json").write_text(json.dumps({"files": files, "artifacts": artifacts,
        "protected_paths": load_surface().all_paths(), "production": production}, indent=2))
    print(json.dumps({"frozen_files": len(files), "artifacts": len(artifacts), "production_inventory_unchanged": True}))


if __name__ == "__main__":
    main()
