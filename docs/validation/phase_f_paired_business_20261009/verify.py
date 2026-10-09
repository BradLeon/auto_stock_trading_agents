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
    paired_runs_checked = 0
    for label in ("entries", "business", "protection", "paired-final", "documents"):
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
                            if label == 'paired-final' and conn.execute("SELECT 1 FROM sqlite_master WHERE name='shadow_replay_runs'").fetchone():
                                from ats.workflow.business_replay_inputs import implementation_hashes
                                from ats.workflow.shadow_replay import FrozenInputs, digest as packet_digest
                                from ats.workflow.shadow_inputs import ShadowInputPacket
                                inputs = {row[0]: json.loads(row[1]) for row in conn.execute('SELECT input_hash,body FROM shadow_replay_inputs')}
                                for row in conn.execute('SELECT body FROM shadow_replay_runs'):
                                    run = json.loads(row[0])
                                    body = inputs[run['input_hash']]
                                    assert packet_digest(body) == run['input_hash']
                                    value = FrozenInputs(ShadowInputPacket(**body['packet']), body['contents'], body['reads'])
                                    value.validate()
                                    assert body['contents']['model_config']['dependency_hashes'] == implementation_hashes()
                                    assert digest(Path(run['source']).read_bytes()) == run['source_hash']
                                    output = run['output']
                                    assert output['_packet'] == body['packet']
                                    assert output['run_id'] == run['run_id'] and output['logical_time'] == run['logical_time']
                                    assert run['logical_time'] == body['packet']['surfaces']['logical_eval_time']
                                    allowed = set(body['contents']['model_config']['rows']) | {packet_digest(r['request']) for r in body['reads']}
                                    assert run['reads'] and set(run['reads']) <= allowed
                                    side = Path(output['side_root']).relative_to('/private/tmp/phase-f-paired-final-closure')
                                    side_copy = Path(directory) / 'side.sqlite'
                                    side_copy.write_bytes(archive.read(str(side / 'memory.sqlite')))
                                    with sqlite3.connect(side_copy.as_uri() + '?mode=ro', uri=True) as results:
                                        assert output['projections']
                                        for projection in output['projections']:
                                            actual = results.execute('SELECT content_hash,payload FROM task_projection_envelopes WHERE projection_id=?', (projection['projection_id'],)).fetchone()
                                            assert actual and actual[0] == projection['content_hash'] and json.loads(actual[1]) == projection['payload']
                                    paired_runs_checked += 1
                    count += 1
        sqlite_counts[label] = count
    print(json.dumps({"source_files": len(after["files"]), "protected_paths": len(after["protected_paths"]),
                      "test_counts": {name: value["tests"] for name, value in after["tests"].items()},
                      "archived_sqlite_checked": sqlite_counts, "production_unchanged": True,
                      "actual_archived_pair_runs_checked": paired_runs_checked,
                      "production_qualification_registered": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
