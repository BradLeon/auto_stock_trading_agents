"""Frozen exact-business-scope intake from original assurance events.

Missing original events remain missing. Candidate proofs can be copied only
inside physical isolation; copying never promotes them into production evidence.

The freeze covers both surface tiers. Qualification-tier changes retire evidence
through the verifier itself. Freeze-only changes (tests, evidence scripts,
tooling) do not, so a new freeze taken against an older one must dispose of
each such change: `unaffected` with a reason, or `revoke`, which taints every
event registered under the older freeze (events carry the freeze digest in
their command summary via `provenance`).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from ats.config import REPO_ROOT
from ats.data import assurance

from .assurance_surface import fingerprint, load_surface
from .scoped_routes import RouteIdentity

FREEZE_VERSION = 'phase-f-final-freeze-v2'
DISPOSITIONS = frozenset({'unaffected', 'revoke'})


def freeze_digest(snapshot):
    """Identity of a freeze: its fingerprints, independent of capture time."""
    return hashlib.sha256(json.dumps(snapshot['fingerprints'], sort_keys=True).encode()).hexdigest()


def provenance(snapshot):
    """Tag for an evidence event's command summary, naming the freeze it was taken under."""
    return f'freeze={freeze_digest(snapshot)}'


def evidence_dependency_paths(consumer_id, snapshot):
    """What a production evidence event for `consumer_id` must bind.

    The qualification tier only. Passing the whole freeze (`snapshot['fingerprints']`)
    would bind tests and evidence scripts again and bring back the retire-on-any-edit
    behaviour; the freeze is cited through `provenance` instead.
    """
    check_freeze(snapshot)
    surface = load_surface()
    return [surface.manifest, *surface.paths_for(consumer_id)]


def freeze_snapshot(previous=None, dispositions=None):
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
    qualification = set(surface.qualification_paths())
    snapshot = {'version':FREEZE_VERSION, 'captured_at':datetime.now(UTC).isoformat(),
            'fingerprints':fingerprint(sorted(paths)), 'consumers':list(surface.consumers),
            'tiers':{'qualification':sorted(qualification), 'freeze_only':sorted(paths - qualification)}}
    if previous is None:
        if dispositions:
            raise ValueError('dispositions need the previous freeze they dispose of')
        snapshot['transition'] = None
        return snapshot
    snapshot['transition'] = _transition(previous, snapshot, dispositions or [])
    return snapshot


def _transition(previous, current, dispositions):
    """Account for every change since `previous`; refuse an unexplained freeze-only change."""
    if previous.get('version') not in {'phase-f-final-freeze-v1', FREEZE_VERSION}:
        raise ValueError('previous freeze version unknown')
    before, after = previous['fingerprints'], current['fingerprints']
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    qualification = set(current['tiers']['qualification'])
    if 'tiers' not in previous:
        # An untiered (v1) freeze bound everything to qualification: every event
        # under it retires through the verifier, including for files added since.
        retired = changed
    else:
        # A path qualification-bound under either freeze retires its evidence
        # through the verifier already; it needs no disposition.
        previous_qualification = set(previous['tiers']['qualification'])
        retired = [p for p in changed if p in qualification or p in previous_qualification]
    tooling = [p for p in changed if p not in retired]
    given = {}
    for row in dispositions:
        path, decision, reason = row.get('path'), row.get('decision'), str(row.get('reason') or '').strip()
        if path in given:
            raise ValueError(f'duplicate disposition: {path}')
        if path not in tooling:
            raise ValueError(f'disposition for a path that is not a changed freeze-only file: {path}')
        if decision not in DISPOSITIONS or not reason:
            raise ValueError(f'disposition needs decision in {sorted(DISPOSITIONS)} and a reason: {path}')
        given[path] = {'path':path, 'decision':decision, 'reason':reason}
    undisposed = [p for p in tooling if p not in given]
    if undisposed:
        raise ValueError('freeze-only changes need a disposition before a new freeze is accepted: '
                         + ', '.join(undisposed))
    digest = freeze_digest(previous)
    tainted = list((previous.get('transition') or {}).get('tainted_freezes') or [])
    if any(row['decision'] == 'revoke' for row in given.values()) and digest not in tainted:
        tainted.append(digest)
    return {'previous_freeze':digest, 'qualification_changes':retired,
            'freeze_only_dispositions':[given[p] for p in tooling], 'tainted_freezes':tainted}


def check_freeze(snapshot):
    current = freeze_snapshot()
    if snapshot.get('version') != current['version'] or snapshot.get('fingerprints') != current['fingerprints']:
        raise ValueError('final implementation freeze drifted; refresh evidence after revalidation')


def tainted(row, freeze):
    """True when `row` was registered under a freeze whose tooling was disposed as `revoke`."""
    marks = (freeze.get('transition') or {}).get('tainted_freezes') or []
    summary = row.get('command_summary') or ''
    return any(f'freeze={digest}' in summary for digest in marks)


def identities(rows):
    result = [RouteIdentity(**row) for row in rows]
    if not result or len({who.key for who in result}) != len(result):
        raise ValueError('nonempty unique exact business identities required')
    for who in result:
        consumer, _, _ = assurance._consumer(who.consumer_id, assurance._COVERAGE)
        if who.domain_id != consumer['domain'] or who.contract_version != consumer['contract_version']:
            raise ValueError('business identity contract differs from current consumer')
    return result


def qualification_report(rows, *, db_path, now=None, freeze=None):
    point = now or datetime.now(UTC)
    results = []
    for who in identities(rows):
        result = assurance.qualification(**who.as_row(), db_path=db_path, now=point)
        entry = {'identity':who.as_row(), 'qualification':result}
        if freeze is not None:
            # Reported, not applied: the verifier stays the authority, and revoking
            # production rows is a separately authorized ledger action.
            accepted = {item['event_id'] for item in result.get('evidence', {}).values()}
            history = assurance.evidence_history(domain_id=who.domain_id, consumer_id=who.consumer_id,
                scope=who.scope, db_path=db_path, limit=1000)
            entry['tainted_accepted_events'] = sorted(
                row['event_id'] for row in history if row['event_id'] in accepted and tainted(row, freeze))
        results.append(entry)
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
            if tainted(originals[event_id], freeze):
                raise ValueError('original event registered under a freeze whose tooling was '
                                 'disposed as revoke: ' + event_id)
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
                command_summary='reuse original event '+event_id+'; '+provenance(freeze),
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
