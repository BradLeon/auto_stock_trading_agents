"""Save isolated test materials and source closure after the tests have finished."""
import hashlib
import json
import os
import runpy
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ats.workflow.assurance_surface import load_surface

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    os.chdir(ROOT)
    before = json.loads((HERE / 'before.json').read_text())
    files = set(before['files'])
    for base in ('src', 'tests', 'config', 'scripts'):
        files.update(str(p.relative_to(ROOT)) for p in (ROOT / base).rglob('*')
                     if p.is_file() and p.suffix in {'.py', '.yaml', '.json', '.md'})
    hashes = {name: digest((ROOT / name).read_bytes()) for name in sorted(files)}
    with zipfile.ZipFile(HERE / 'after_sources.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in hashes:
            archive.write(ROOT / name, name)
    surface = load_surface()
    changed = [name for name in hashes if hashes[name] != before['files'].get(name)]
    (HERE / 'minimum-reverification.json').write_text(json.dumps({
        'changed_paths': changed, 'new_protected_paths': sorted(set(surface.all_paths()) - set(before['protected_paths'])),
        'requires_revalidation': list(surface.consumers),
        'historical_evidence_preserved': True, 'production_qualification_registered': False,
        'final_freeze_task': '6.1', 'scope_shadow_signoff_task': '3.10'}, indent=2))
    suites = {}
    for label in ('entries', 'business', 'protection', 'paired-final', 'documents'):
        suites[label + '.xml'] = ET.parse(HERE / (label + '.xml')).getroot().find('testsuite').attrib
        assert suites[label + '.xml']['failures'] == suites[label + '.xml']['errors'] == '0', suites[label + '.xml']
        suffix = {'entries': 'entries-final', 'paired-final': 'final-closure'}.get(label, label)
        temp_root = Path('/private/tmp/phase-f-paired-' + suffix)
        index = {}
        with zipfile.ZipFile(HERE / (label + '-materials.zip'), 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(temp_root.rglob('*')):
                if not file.is_file() or file.name.endswith(('-wal', '-shm', '-journal')):
                    continue
                name = str(file.relative_to(temp_root))
                data = file.read_bytes()
                is_db = data.startswith(b'SQLite format 3')
                if is_db:
                    # Backup through SQLite includes any committed WAL content.
                    with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
                        target = Path(directory) / 'snapshot.sqlite'
                        with sqlite3.connect(file.resolve().as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(target) as backup:
                            source.backup(backup)
                            backup.execute('PRAGMA journal_mode=DELETE')
                        data = target.read_bytes()
                archive.writestr(name, data)
                index[name] = {'sha256': digest(data), 'bytes': len(data), 'sqlite': is_db}
        (HERE / (label + '-materials.json')).write_text(json.dumps(index, indent=2))
    production = runpy.run_path(str(ROOT / 'docs/validation/phase_f_recovery_20261007/preflight.py'))['read_databases']()
    assert production == before['production'], 'production inventory changed'
    artifact_hashes = {p.name: digest(p.read_bytes()) for p in HERE.iterdir()
                       if p.is_file() and p.name != 'after.json'}
    after = {'files': hashes, 'protected_paths': list(surface.all_paths()), 'production': production,
             'tests': suites, 'artifact_hashes': artifact_hashes}
    (HERE / 'after.json').write_text(json.dumps(after, indent=2, ensure_ascii=False))
    print(json.dumps({'source_files': len(hashes), 'changed_files': len(changed),
                      'protected_paths': len(surface.all_paths()), 'test_counts': suites,
                      'production_unchanged': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
