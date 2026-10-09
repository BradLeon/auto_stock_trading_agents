"""Read-only verification of this run's sources, evidence and production inventory."""
from pathlib import Path
import hashlib
import json
import runpy
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    after = json.loads((HERE / "after.json").read_text())
    before = json.loads((HERE / "before.json").read_text())
    for name, expected in after["files"].items():
        assert digest((ROOT / name).read_bytes()) == expected, f"source drift: {name}"
    for name, expected in after["artifact_hashes"].items():
        assert digest((HERE / name).read_bytes()) == expected, f"artifact drift: {name}"
    for name, expected in after["tests"].items():
        actual = ET.parse(HERE / name).getroot().find("testsuite").attrib
        assert actual == expected, name
        assert actual["errors"] == actual["failures"] == actual["skipped"] == "0", name
    # This helper uses existing files through mode=ro and query_only; never main().
    import os
    os.chdir(ROOT)
    production = runpy.run_path(str(ROOT / "docs/validation/phase_f_recovery_20261007/preflight.py"))["read_databases"]()
    assert production == before["production"] == after["production"], "production inventory drift"
    sqlite_counts = {}
    for label in ("entries", "business", "protection"):
        index = json.loads((HERE / f"{label}-materials.json").read_text())
        count = 0
        with zipfile.ZipFile(HERE / f"{label}-materials.zip") as archive:
            assert set(archive.namelist()) == set(index), label
            for name, metadata in index.items():
                data = archive.read(name)
                assert digest(data) == metadata["sha256"], (label, name)
                assert len(data) == metadata["bytes"], (label, name)
                if metadata["sqlite"]:
                    # Temporary copies only, never writes to archived or production DBs.
                    with tempfile.TemporaryDirectory(prefix="phase-f-runtime-verify-", dir="/private/tmp") as directory:
                        copy = Path(directory) / "snapshot.sqlite"
                        copy.write_bytes(data)
                        with sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True) as conn:
                            conn.execute("PRAGMA query_only=ON")
                            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", (label, name)
                    count += 1
        sqlite_counts[label] = count
    print(json.dumps({"source_files": len(after["files"]), "protected_paths": len(after["protected_paths"]),
                      "test_counts": {name: value["tests"] for name, value in after["tests"].items()},
                      "archived_sqlite_checked": sqlite_counts, "production_unchanged": True,
                      "production_qualification_registered": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
