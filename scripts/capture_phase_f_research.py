"""Actual admitted-document Information -> Routine capture and offline process replay.

Selected snapshots retain original values/versions/hashes; no collection, production
write, notification, qualification promotion, trading or reviewer signoff.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from ats.config import REPO_ROOT
from ats.agent.task_projection import ProjectionScope
from ats.workflow import acceptance_matrix as matrices, acceptance_reports as reports, acceptance_runner as runner
from ats.workflow.acceptance_assertions import quantity_values
from ats.workflow.isolation import isolated_run, verified_isolation_root
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.runtime_reads import task_identity
from ats.workflow.scoped_intake import check_freeze


def selected_snapshot(source, target, entities):
    """Readonly source, exact original rows. Excluded query caches aren't evidence.

    All document/admission/history tables survive. Structured observations and
    candidates retain only selected entities; unused snapshots/source query caches
    are omitted before any new query. Triggers and indexes are restored unchanged.
    """
    source, target = Path(source).resolve(), Path(target).resolve()
    root = verified_isolation_root()
    if root is None or not target.is_relative_to(root) or target.exists() or source == target:
        raise ValueError('new physically isolated snapshot destination required')
    selected = sorted(set(str(entity).upper() for entity in entities))
    if not selected:
        raise ValueError('nonempty selected entities required')
    omitted = {'structured_snapshots', 'structured_snapshot_items', 'data_fixed_source_rows'}
    placeholders = ','.join('?' for _ in selected)
    conditions = {
        'structured_series': (f'entity_id IN ({placeholders})', selected),
        'structured_observations': (f'series_id IN (SELECT series_id FROM structured_series WHERE entity_id IN ({placeholders}))', selected),
        'structured_candidates': (f'entity_id IN ({placeholders})', selected),
    }
    def quoted(name):
        return '"' + name.replace('"', '""') + '"'
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.execute('BEGIN')
        schema = src.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
        counts = {}
        for kind, name, sql in schema:
            if kind != 'table':
                continue
            dst.execute(sql)
            if name in omitted:
                counts[name] = {'copied': 0, 'selection': 'unused query cache omitted'}
                continue
            where, args = conditions.get(name, ('1', []))
            column_info = src.execute('PRAGMA table_info(' + quoted(name) + ')').fetchall()
            primary = [row[1] for row in sorted(column_info, key=lambda row: row[5]) if row[5]]
            ordering = primary or [row[1] for row in column_info]
            query = ('SELECT * FROM ' + quoted(name) + ' WHERE ' + where +
                     ' ORDER BY ' + ','.join(quoted(column) for column in ordering))
            cursor = src.execute(query, args)
            columns = len(cursor.description)
            count, digest = 0, hashlib.sha256()
            while rows := cursor.fetchmany(1000):
                dst.executemany('INSERT INTO ' + quoted(name) + ' VALUES (' + ','.join('?' for _ in range(columns)) + ')', rows)
                for row in rows:
                    digest.update(json.dumps(row, ensure_ascii=False, default=str).encode())
                    digest.update(b'\n')
                count += len(rows)
            # Compare complete row values before installing append-only triggers.
            reopened = dst.execute(query, args)
            verified = hashlib.sha256()
            while rows := reopened.fetchmany(1000):
                for row in rows:
                    verified.update(json.dumps(row, ensure_ascii=False, default=str).encode())
                    verified.update(b'\n')
            if digest.hexdigest() != verified.hexdigest():
                raise ValueError('snapshot row content differs: ' + name)
            counts[name] = {'copied': count, 'row_sha256': digest.hexdigest(), 'selection': where}
        for kind, name, sql in schema:
            if kind != 'table':
                dst.execute(sql)
        dst.commit()
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('selected snapshot integrity failed')
    return {'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'target': str(target), 'target_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'entities': selected, 'tables': counts, 'production_mutated': False}


def execute(args):
    root = args.root.resolve()
    freeze = json.loads(args.freeze.read_text())
    check_freeze(freeze)
    if args.replay_matrix:
        matrix = matrices.restore(json.loads(args.replay_matrix.read_text()))
        with isolated_run('actual-research-offline-replay', root=root):
            reports.run(matrix=matrix, input_store=args.input_store, input_hash=args.input_hash,
                        root=root / args.replay_run_id, replay_run_id=args.replay_run_id)
        check_freeze(freeze)
        return
    entity = args.entity.upper()
    if entity not in {'MRVL', 'MSFT', 'NVDA', 'TSM'}:
        raise ValueError('this acceptance scope selects only four previously audited entities')
    if root.exists():
        raise ValueError('new acceptance root required; previous evidence must survive')
    point = args.as_of or datetime.now(UTC).isoformat()
    quantity_source = None
    if getattr(args, 'quantity_source_preview', None):
        path = args.quantity_source_preview.resolve()
        preview = json.loads(path.read_text())
        if (preview['entity'] != entity or datetime.fromisoformat(preview['as_of']) != datetime.fromisoformat(point)):
            raise ValueError('quantity source preview entity/as_of differs from approved capture')
        quantity_source = {'path':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'values':quantity_values(preview['prompts']['information']['context'])}
    # Each child installs its own isolation. Inheriting this parent's redirected
    # paths makes its fresh process correctly reject the destination as an alias.
    replay_environment = os.environ.copy()
    with isolated_run('actual-research-candidate', root=root) as env:
        evidence = selected_snapshot(args.data_source, env.path_for('ATS_DATA_DB_PATH'), [entity])
        # Both product surfaces see the same selected original data, with separate files.
        shutil.copyfile(env.path_for('ATS_DATA_DB_PATH'), env.path_for('ATS_STRUCTURED_DB_PATH'))
        with sqlite3.connect(args.memory_source.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(env.path_for('ATS_DB_PATH')) as dst:
            src.backup(dst)
        memory_hash = hashlib.sha256(args.memory_source.read_bytes()).hexdigest()
        from ats.data.consumer_api import read_input
        requested = ['fundamental-routine'] if args.with_routine else ['information-brief']
        def plan(name, inputs=None):
            return build_plan(requested_tasks=requested, scope=ProjectionScope(kind='entity', id=entity),
                trigger=TriggerContext(kind='manual', workflow_id='fundamental-routine' if args.with_routine else 'information-brief',
                                       trigger_id='phase-f-real-research-' + entity),
                as_of=point, run_id=name, task_inputs=inputs or {}, enter_decision_cycle=False)
        preliminary = plan('inputs')
        who = task_identity(preliminary, preliminary.tasks[0])
        packet = read_input(consumer='information', product='DOC_DATA',
            scope={'entities':who.scope['entities'], 'limit':60}, as_of=datetime.fromisoformat(point), business_scope=who)
        refs = packet.input_refs
        if not refs:
            raise ValueError('no admitted versioned documents in selected Information input scope')
        inputs = {'data_vintage_refs':refs, 'input_refs':refs}
        def matrix(name):
            p = plan(name, inputs)
            identity = task_identity(p, p.tasks[-1])
            return matrices.freeze(p, workflow_id='fundamental-routine' if args.with_routine else 'information-brief',
                identity=identity, batch_class='research_read', model_execution=True,
                criteria={'analysis.'+p.tasks[0].instance_key:[{'path':'quantity_values',
                    'op':'subset', 'value':quantity_source['values']}]} if quantity_source is not None else {})
        (root / 'snapshot_review.json').write_text(json.dumps({'data':evidence,
            'memory_source':str(args.memory_source.resolve()), 'memory_source_sha256':memory_hash,
            'as_of':point, 'information_identity':who.as_row(), 'input_refs':refs,
            'pre_run_quantity_source':quantity_source}, ensure_ascii=False, indent=2))
        input_store = root / 'inputs.sqlite'
        captured = runner.capture(matrix=matrix('capture'), input_store=input_store, root=root / 'capture', capture_id='real-inputs')
        (root / 'capture.json').write_text(json.dumps(captured, ensure_ascii=False, indent=2))
        for name in ('new', 'replay'):
            matrix_path = root / (name + '_matrix.json')
            matrix_path.write_text(json.dumps(matrix(name).as_row(), ensure_ascii=False, indent=2))
            command = [sys.executable, str(Path(__file__).resolve()), '--root', str(root), '--freeze', str(args.freeze.resolve()),
                '--replay-matrix', str(matrix_path), '--input-store', str(input_store), '--input-hash', captured['input_hash'],
                '--replay-run-id', name]
            with (root / (name + '.log')).open('x') as log:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, env=replay_environment)
        try:
            body = reports.record(report_id='actual-' + entity, input_hash=captured['input_hash'], actor='phase-f-observer',
                proof={'input_store':str(input_store), 'new_run_id':'new', 'replay_run_id':'replay'}, path=root / 'report.sqlite')
            result = {'report':body, 'signoff':reports.read('actual-' + entity, path=root / 'report.sqlite')['status'],
                      'production_qualification':False}
        except Exception as exc:
            result = {'error':str(exc), 'signoff':'not_created', 'production_qualification':False}
        check_freeze(freeze)
        result['capture'] = captured
        result['source_unchanged'] = memory_hash == hashlib.sha256(args.memory_source.read_bytes()).hexdigest() and evidence['source_sha256'] == hashlib.sha256(args.data_source.read_bytes()).hexdigest()
        with (root / 'review.json').open('x') as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps({'review':str(root / 'review.json'), 'signoff':result['signoff'], 'error':result.get('error'), 'source_unchanged':result['source_unchanged']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--entity', default='')
    parser.add_argument('--with-routine', action='store_true')
    parser.add_argument('--as-of')
    parser.add_argument('--quantity-source-preview', type=Path,
                        help='Approved exact entity/as-of context; freeze quantity/unit criteria before model execution')
    parser.add_argument('--data-source', type=Path, default=REPO_ROOT / 'var/data.sqlite')
    parser.add_argument('--memory-source', type=Path, default=REPO_ROOT / 'var/ats.sqlite')
    parser.add_argument('--replay-matrix', type=Path)
    parser.add_argument('--input-store', type=Path)
    parser.add_argument('--input-hash')
    parser.add_argument('--replay-run-id')
    execute(parser.parse_args())


if __name__ == '__main__':
    main()
