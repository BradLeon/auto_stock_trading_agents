"""Read-only verification; a later edit is drift, never an automatic upgrade."""
import hashlib
import json
import runpy
from pathlib import Path
from ats.config import REPO_ROOT


folder = Path(__file__).resolve().parent
frozen = json.loads((folder / "after.json").read_text())
for name, sha in frozen["files"].items():
    assert hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() == sha, name
for name, sha in frozen["artifacts"].items():
    assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == sha, name
production = runpy.run_path(str(REPO_ROOT / "docs/validation/phase_f_recovery_20261007/preflight.py"))["read_databases"]()
assert production == frozen["production"]
dispositions = json.loads((folder / "dispositions.json").read_text())
assert not dispositions["production_qualification_granted"]
assert len(dispositions["rows"]) == 10
for row in dispositions["rows"]:
    assert not row["auto_released"] and not row["production_qualification_granted"]
    for reference in row["evidence"]:
        assert hashlib.sha256((REPO_ROOT / reference["path"]).read_bytes()).hexdigest() == reference["sha256"]
print(json.dumps({"passed": True, "consumers": 10, "production_inventory_unchanged": True}))
