"""Frozen exact-business-scope intake from original assurance events.

Missing original events remain missing. Candidate proofs can be copied only
inside physical isolation; copying never promotes them into production evidence.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from ats.config import REPO_ROOT
from ats.data import assurance

from .assurance_surface import fingerprint, load_surface
from .scoped_routes import RouteIdentity


def freeze_snapshot():
    surface = load_surface()
    expected = {p.relative_to(REPO_ROOT).as_posix() for base, patterns in (
        (REPO_ROOT/'src/ats', ('*.py', '*.md')),
        (REPO_ROOT/'config', ('*.yaml', '*.yml')),
    ) for pattern in patterns for p in base.rglob(pattern)}
    expected.update({'pyproject.toml', 'uv.lock', 'tests/conftest.py'})
    expected.update(p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT/'tests').glob('test_phase_f_*.py'))
    expected.update(p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT/'scripts').glob('*phase_f*.py'))
    expected.add('scripts/verify_dataflow_qualification.py')
    paths = {*surface.all_paths(), surface.manifest}
    missing = expected - paths
    if missing:
        raise ValueError('unregistered implementation dependencies: ' + ', '.join(sorted(missing)))
    return {'version':'phase-f-final-freeze-v1', 'captured_at':datetime.now(UTC).isoformat(),
            'fingerprints':fingerprint(sorted(paths)), 'consumers':list(surface.consumers)}


def check_freeze(snapshot):
    current = freeze_snapshot()
    if snapshot.get('version') != current['version'] or snapshot.get('fingerprints') != current['fingerprints']:
        raise ValueError('final implementation freeze drifted; refresh evidence after revalidation')


def identities(rows):
    result = [RouteIdentity(**row) for row in rows]
    if not result or len({who.key for who in result}) != len(result):
        raise ValueError('nonempty unique exact business identities required')
    for who in result:
        consumer, _, _ = assurance._consumer(who.consumer_id, assurance._COVERAGE)
        if who.domain_id != consumer['domain'] or who.contract_version != consumer['contract_version']:
            raise ValueError('business identity contract differs from current consumer')
    return result


def qualification_report(rows, *, db_path, now=None):
    point = now or datetime.now(UTC)
    results = []
    for who in identities(rows):
        result = assurance.qualification(**who.as_row(), db_path=db_path, now=point)
        results.append({'identity':who.as_row(), 'qualification':result})
    return {'evaluated_at':point.isoformat(), 'ledger':str(Path(db_path).resolve()),
            'scopes':results, 'eligible':sum(r['qualification']['status']=='eligible' for r in results),
            'total':len(results), 'routes_changed':False, 'approval_created':False}


def append_original_events(rows, *, source_db, target_db, freeze, actor):
    """Reuse only events accepted by the current source-ledger verifier.

    Original timestamps, TTLs and prerequisite chains survive copying. The
    qualification verifier rejects expired, revoked and drifted source events;
    a summary file is never interpreted as an event. Every rejected type is named.
    """
    from .isolation import verified_isolation_root

    root = verified_isolation_root()
    target = Path(target_db).resolve()
    if root is None or not target.is_relative_to(root):
        raise ValueError('evidence reuse requires a physically isolated target ledger')
    if target == Path(source_db).resolve() or not actor.strip():
        raise ValueError('distinct source/target and actor required')
    check_freeze(freeze)
    outcomes = []
    for who in identities(rows):
        consumer, _, _ = assurance._consumer(who.consumer_id, assurance._COVERAGE)
        checked = assurance.qualification(**who.as_row(), db_path=source_db)
        history = assurance.evidence_history(domain_id=who.domain_id, consumer_id=who.consumer_id,
            scope=who.scope, db_path=source_db, limit=1000)
        originals = {r['event_id']:r for r in history if r['event_kind']=='evidence'}
        copied = {}

        def resolve_original(event_id, *, originals=originals, who=who, visiting=None):
            visiting = set() if visiting is None else visiting
            if event_id in visiting:
                raise ValueError('original prerequisite cycle')
            if event_id not in originals:
                # The report history is intentionally bounded to 1000 rows;
                # accepted prerequisites can be older than that view. Fetch
                # only the named original, under the same exact identity.
                with closing(assurance._connect(source_db, writable=False)) as conn:
                    row = conn.execute('SELECT * FROM dataflow_assurance_events WHERE event_id=? '
                        'AND event_kind=? AND domain_id=? AND consumer_id=? AND contract_version=? AND scope_json=?',
                        (event_id, 'evidence', who.domain_id, who.consumer_id, who.contract_version,
                         json.dumps(who.scope, sort_keys=True))).fetchone()
                if row is None:
                    raise ValueError('original prerequisite missing: '+event_id)
                original = dict(row)
                original['details'] = json.loads(original.get('details_json') or '{}')
                originals[event_id] = original
            visiting.add(event_id)
            for ref in json.loads(originals[event_id]['prerequisites_json']):
                resolve_original(ref, visiting=visiting)
            visiting.remove(event_id)

        def copy_event(event_id, *, copied=copied, originals=originals, who=who):
            if event_id in copied:
                return copied[event_id]
            row = originals[event_id]
            prerequisites = [copy_event(ref) for ref in json.loads(row['prerequisites_json'])]
            check_freeze(freeze)
            copied[event_id] = assurance.record_evidence(domain_id=who.domain_id,
                consumer_id=who.consumer_id, scope=who.scope, evidence_type=row['evidence_type'],
                outcome=row['outcome'], as_of=datetime.fromisoformat(row['as_of']),
                max_age_days=row['max_age_days'], dependency_paths=list(json.loads(row['dependencies_json'])),
                prerequisite_event_ids=prerequisites, details=row.get('details', {}),
                command_summary='reuse original event '+event_id,
                result_summary='candidate reuse by '+actor+'; source event '+event_id+'; not production qualification',
                db_path=target)
            return copied[event_id]

        for kind in consumer['required_evidence']:
            event = checked.get('evidence', {}).get(kind)
            if event is None:
                outcomes.append({'identity':who.as_row(), 'evidence_type':kind, 'registered':False,
                    'reasons':checked['reasons'] or ['original exact-scope event missing']})
            else:
                try:
                    resolve_original(event['event_id'])
                except (OSError, sqlite3.Error, ValueError) as exc:
                    outcomes.append({'identity':who.as_row(), 'evidence_type':kind, 'registered':False,
                        'reasons':['original event chain unreadable: '+str(exc)]})
                    continue
                outcomes.append({'identity':who.as_row(), 'evidence_type':kind, 'registered':True,
                    'original_event_id':event['event_id'], 'event_id':copy_event(event['event_id']),
                    'production_qualification':False})
    return outcomes
