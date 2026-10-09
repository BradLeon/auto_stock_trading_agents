"""Read-only verification of this run's final sources and production inventory."""
import hashlib
import json
import runpy
from pathlib import Path

from ats.config import REPO_ROOT


def main():
    directory = Path(__file__).resolve().parent
    record = json.loads((directory / "after.json").read_text())
    for name, digest in record["files"].items():
        assert hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() == digest, name
    for name, digest in record["artifacts"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest, name
    inventory = runpy.run_path(str(REPO_ROOT / "docs/validation/phase_f_recovery_20261007/preflight.py"))["read_databases"]()
    assert inventory == record["production"] == json.loads((directory / "before.json").read_text())["production"]
    print(json.dumps({"verified":True,"production_unchanged":True,
                      "protected_paths":len(record["protected_paths"]),"tests":record["tests"]},indent=2))


if __name__ == "__main__":
    main()
