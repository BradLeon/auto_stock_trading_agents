"""Read-only checks of this run's frozen sources, artifacts and production inventory."""
import hashlib
import json
import runpy
from pathlib import Path

from ats.config import REPO_ROOT


def verify():
    directory = Path(__file__).resolve().parent
    record = json.loads((directory / "after.json").read_text())
    before = json.loads((directory / "before.json").read_text())
    for name, digest in record["files"].items():
        assert hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() == digest, name
    for name, digest in record["artifacts"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest, name
    read_databases = runpy.run_path(str(REPO_ROOT / "docs/validation/phase_f_recovery_20261007/preflight.py"))["read_databases"]
    inventory = read_databases()
    assert inventory == before["production"] == record["production"], "production inventory changed"
    print(json.dumps({"verified": True, "production_inventory_unchanged": True,
                      "source_paths": len(record["files"]), "tests": record["tests"]}, indent=2))


if __name__ == "__main__":
    verify()
