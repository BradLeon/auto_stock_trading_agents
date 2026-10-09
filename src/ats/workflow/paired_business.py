"""Actual legacy role chain and Dispatcher on the same content-addressed inputs.

No imported left/right outputs, qualification registration, approvals or trading.
A pair is implementation evidence; scope execution/signoff remains task 3.10.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import shadow_inputs as si
from .business_replay_inputs import Tape, bind_tape, encode, implementation_hashes
from .evaluation_clock import at
from .isolation import verified_isolation_root
from .shadow_replay import ReplayReads, digest, freeze_inputs, load_inputs, save_inputs


def _require_isolation():
    root = verified_isolation_root()
    if root is None:
        raise PermissionError("paired business requires complete isolation")
    return root


def _snapshot():
    from ..config import REPO_ROOT
    root = _require_isolation()
    config = Path(os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    files = {str(p.relative_to(config)): p.read_text() for p in config.rglob('*.yaml')}
    # Suppress notification transports structurally; fixed config stays unchanged.
    dbs = {}
    from ..memory import get_store
    get_store()  # Existing schema, no manufactured business inputs.
    for env in ("ATS_DB_PATH", "ATS_DATA_DB_PATH", "ATS_STRUCTURED_DB_PATH", "ATS_PERSISTENT_QUEUE_PATH"):
        path = Path(os.environ[env])
        if path.exists():
            with tempfile.TemporaryDirectory(prefix='capture-snapshot-', dir=root) as directory:
                backup = Path(directory) / 'snapshot.sqlite'
                with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(backup) as target:
                    source.backup(target)
                    target.execute('PRAGMA journal_mode=DELETE')
                dbs[env] = base64.b64encode(backup.read_bytes()).decode()
    return {"config": files, "databases": dbs, "origin_root": str(root),
            "processing_lease": {k:os.environ.get(k, "") for k in ("ATS_PERSISTENT_QUEUE_TASK_ID", "ATS_PERSISTENT_QUEUE_LEASE_OWNER", "ATS_PERSISTENT_QUEUE_SOURCE_ID")}}


@contextmanager
def _side(snapshot, root, run_id):
    from ..config import reset_config_cache
    old_config = os.environ.get('ATS_CONFIG_DIR')
    lease_before = {k:os.environ.get(k) for k in snapshot.get('processing_lease', {})}
    root = Path(root).resolve()
    if not root.is_relative_to(_require_isolation()):
        raise PermissionError('business side escapes parent isolation')
    if root.exists():
        raise ValueError("pair side must be a new directory; existing claims/history cannot be erased")
    root.mkdir(parents=True)
    config = root / 'config'
    for name, text in snapshot['config'].items():
        file = config / name
        if not file.resolve().is_relative_to(config.resolve()):
            raise ValueError('configuration path escapes pair side')
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    try:
        for key, value in snapshot.get('processing_lease', {}).items():
            if key not in {'ATS_PERSISTENT_QUEUE_TASK_ID', 'ATS_PERSISTENT_QUEUE_LEASE_OWNER', 'ATS_PERSISTENT_QUEUE_SOURCE_ID'}:
                raise ValueError('unknown processing lease field')
            os.environ[key] = value
        os.environ['ATS_CONFIG_DIR'] = str(config)
        reset_config_cache()
        from .intake_verification import isolated_verification
        from .isolation import build_environment
        env = build_environment(root)
        for key, content in snapshot['databases'].items():
            if key not in {'ATS_DB_PATH', 'ATS_DATA_DB_PATH', 'ATS_STRUCTURED_DB_PATH', 'ATS_PERSISTENT_QUEUE_PATH'}:
                raise ValueError('unknown seeded data surface')
            env.path_for(key).write_bytes(base64.b64decode(content, validate=True))
        with isolated_verification(run_id, root=root):
            yield
    finally:
        for key, value in lease_before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if old_config is None:
            os.environ.pop('ATS_CONFIG_DIR', None)
        else:
            os.environ['ATS_CONFIG_DIR'] = old_config
        reset_config_cache()


def _plan(request, run_id, point):
    from ..agent.task_projection import ProjectionScope
    from .phase_e import build_plan
    from .run_contracts import TriggerContext
    return build_plan(requested_tasks=request['tasks'], scope=ProjectionScope(**request['scope']),
        trigger=TriggerContext(**request['trigger']), as_of=point.isoformat(), run_id=run_id,
        profile_id=request.get('profile_id', ''), enter_decision_cycle=False,
        config_dir=os.environ['ATS_CONFIG_DIR'], task_inputs=copy.deepcopy(request.get('task_inputs', {})))


def _legacy(plan):
    """Use the existing manual role entrypoints, not Dispatcher adapters."""
    from ..agents.fundamental.entry import event_request, routine_request, run_fundamental_pass
    from ..agents.information.entry import run_information_target
    from ..agents.technical.review import run as technical_run
    from ..runtime import cli
    from .dispatcher import Dispatcher
    from .run_contracts import TriggerContext
    from .runtime_reads import bind_read, task_identity
    from .schedule_runtime import wake
    done = set()
    pending = list(plan.tasks)
    while pending:
        task = next((t for t in pending if set(t.dependencies) <= done), None)
        if task is None:
            raise ValueError('legacy dependency chain blocked')
        _, _, inputs = Dispatcher._task_inputs(None, plan, task, {})
        use_llm = bool(inputs.get('use_llm', True))
        with bind_read(task_identity(plan, task)), wake(TriggerContext(**plan.trigger)):
            if task.task_id == 'information-brief':
                run_information_target(task.scope.id, use_llm=use_llm)
            elif task.task_id == 'fundamental-routine':
                run_fundamental_pass(routine_request(task.scope.id,
                    trigger=inputs.get('fundamental_trigger', 'information_brief_update'), use_llm=use_llm))
            elif task.task_id == 'fundamental-event':
                if inputs.get('material_state') != 'admitted' or not inputs.get('admitted_material_refs'):
                    raise ValueError('event requires admitted material refs')
                run_fundamental_pass(event_request(task.scope.id,
                    trigger=inputs.get('fundamental_trigger', 'earnings_release'),
                    fiscal_label=inputs['fiscal_label'], cutoff=inputs.get('cutoff', ''), use_llm=use_llm))
            elif task.task_id == 'layer-review':
                sector = plan.request_scope.id if plan.request_scope.kind == 'sector' else inputs['sector']
                cli.run_layer_review(sector, task.scope.id, use_llm=use_llm, live_data=True)
            elif task.task_id == 'sector-review':
                cli.run_sector_review(task.scope.id, use_llm=use_llm, live_data=True, write_report=False)
            elif task.task_id == 'macro-review':
                cli.run_macro_review(use_llm=use_llm, live_data=True, write_report=False)
            elif task.task_id == 'technical-review':
                technical_run(symbols=[task.scope.id], live_data=True, persist=True, write_report=False)
            else:
                raise ValueError('unavailable legacy role: ' + task.task_id)
        done.add(task.instance_key)
        pending.remove(task)
    return {'run_id': plan.run_id, 'completed_tasks': sorted(done)}


def _dispatcher(plan):
    from ..memory import get_store

    # Fresh per-side SQL authority: no YAML writes or production migration.
    from . import schedule_runtime as rt
    from .dispatcher import Dispatcher
    from .store import WorkflowStore
    for task in plan.tasks:
        scope = task.scope.model_dump(mode='json')
        rt.check_owner(task.task_id, scope, 'legacy')
        token = rt.freeze(task.task_id, scope, actor=plan.run_id, reason='isolated pair')
        rt.handover(token, to_owner='dispatcher', dispositions={}, actor=plan.run_id, reason='isolated pair')
    result = Dispatcher(workflow_store=WorkflowStore(str(get_store().path)), max_workers=1).dispatch(plan)
    if not result.complete:
        raise ValueError('actual Dispatcher incomplete: ' + json.dumps(result.as_dict()))
    return result.as_dict()


def _risk(request, *, legacy):
    from ..risk import checks
    from ..schemas.decision import TradeDecision
    from ..trader import portfolio
    from .runtime_reads import bind_read, business_identity
    point = datetime.fromisoformat(request['logical_time'])
    orders = [TradeDecision.model_validate(x) for x in request['risk']['orders']]
    entities = sorted({o.symbol for o in orders})
    identity = business_identity('risk', kind='decision', scope_id=request['risk']['cycle_id'],
                                 entities=entities, as_of=point)
    with bind_read(identity):
        pf = portfolio.snapshot()
        if pf is None:
            raise ValueError('paired risk requires frozen portfolio')
        kwargs = {'sector': request['risk'].get('sector', 'ai_hardware'),
                  'event_data': request['risk'].get('event_data')}
        if legacy:
            approved, notes, baseline = checks.pre_trade(orders, pf, **kwargs)
            return {'entry': 'ats.risk.checks.pre_trade', 'approved': encode(approved),
                    'notes': notes, 'baseline': encode(baseline)}
        review = checks.review_revision(orders, pf, **kwargs)
        return {'entry': 'ats.risk.checks.review_revision', 'review': review.model_dump(mode='json')}


def _business(*, legacy, read_input, logical_time, value, root, business_run_id, tape):
    run_id = business_run_id
    from ..memory import get_store
    from .consumer_reads import trace_reads
    def recipe(surface, key):
        content = value.contents[surface]
        rows = content if isinstance(content, list) else [content]
        return next(row[key] for row in rows if key in row)
    request = recipe(si.RULESET_VERSION, 'request')
    seed = recipe(si.HISTORY_STATE, 'seed')
    if request['scope'] != value.packet.scope:
        raise ValueError('business request scope differs from frozen packet')
    with _side(seed, root, run_id), at(logical_time), bind_tape(tape), trace_reads() as trace:
        # Bind consumer-level ReplayReads via Tape; the passed adapter is the same
        # ReplayReads instance's method, not an independent source lookup.
        before = {r['projection_id'] for r in get_store().task_projection_envelopes(limit=100000)}
        plan = _plan(request, run_id, logical_time)
        result = _legacy(plan) if legacy else _dispatcher(plan)
        projections = [row for row in get_store().task_projection_envelopes(limit=100000)
                       if row['projection_id'] not in before]
        from .phase_e import TASK_ROLE
        for task in plan.tasks:
            if not any(row['agent_role'] == TASK_ROLE[task.task_id]
                       and row['scope_kind'] == task.scope.kind and row['scope_id'] == task.scope.id for row in projections):
                raise ValueError('business entry did not publish required projection: ' + task.instance_key)
        risk = _risk(request, legacy=legacy) if request.get('risk') else None
        from .schedule_runtime import snapshot
        authority = snapshot()
        return {'_packet': asdict(value.packet), 'run_id': run_id, 'path': 'legacy' if legacy else 'dispatcher',
                'logical_time': logical_time.isoformat(), 'result': result, 'projections': projections,
                'risk': risk, 'governed_trace': trace, 'input_calls': tape.calls,
                'schedule_authority': authority, 'side_root': str(Path(root).resolve()), 'tradable': False}


def legacy_business(**kwargs):
    return _business(legacy=True, **kwargs)


def dispatcher_business(**kwargs):
    return _business(legacy=False, **kwargs)


def capture(*, request, input_store, work_root, run_id):
    """Capture each input once while exercising both actual paths on cloned seeds.

    Capture uses existing admitted data/provider interfaces; replay never does.
    The caller must already be isolated and explicitly choose the logical clock.
    """
    parent = _require_isolation()
    if not run_id or not Path(input_store).resolve().is_relative_to(parent) or not Path(work_root).resolve().is_relative_to(parent):
        raise PermissionError('capture requires named run and isolated destinations')
    if str(Path(input_store).resolve()) in {os.environ.get(k) for k in ('ATS_DB_PATH','ATS_DATA_DB_PATH','ATS_STRUCTURED_DB_PATH','ATS_DISPATCH_STATE_PATH')}:
        raise ValueError('frozen input store aliases business/control database')
    point = datetime.fromisoformat(request['logical_time'])
    seed = _snapshot()
    tape = Tape()
    contents = {si.RULESET_VERSION: {'request': copy.deepcopy(request)}, si.HISTORY_STATE: {'seed': seed},
                si.PERSISTENT_REFS: [], si.PROJECTION_HASH: {'initial_memory_hash': digest(seed['databases'].get('ATS_DB_PATH'))}}
    class Value:
        pass
    provisional = Value()
    provisional.contents = contents
    provisional.packet = si.ShadowInputPacket(run_id, 'risk' if request.get('risk') else 'technical',
        'decision' if request.get('risk') else 'research_read', scope=request['scope'])
    for side, entry in [('capture-legacy', legacy_business), ('capture-dispatcher', dispatcher_business)]:
        entry(read_input=None, logical_time=point, value=provisional, root=Path(work_root)/side,
              business_run_id=run_id+':'+side, tape=tape)
    contents[si.MODEL_CONFIG] = {'transport': 'recorded-responses', 'rows': tape.rows,
        'dependency_hashes': implementation_hashes()}
    for row in tape.reads:
        surface = row['surface']
        if isinstance(contents.get(surface), dict):
            contents[surface] = [contents[surface]]
        contents.setdefault(surface, []).append(row['input'])
    # Runtime/history/config are real inputs even in an analysis-only request.
    runtime = {'inputs': [r for r in tape.rows.values()
        if any(token in r['request']['api'] for token in ('market_data', 'execution_price', 'sector_price', 'runtime.macro'))]}
    if si.MARKET_RUNTIME in contents:
        contents[si.MARKET_RUNTIME].append(runtime)
    else:
        contents[si.MARKET_RUNTIME] = runtime
    if request.get('risk'):
        portfolios = [r['result'] for r in tape.rows.values() if r['request']['api'] == 'ats.trader.portfolio.snapshot']
        contents.setdefault(si.ACCOUNT_STATE, []).extend(portfolios)
    value = freeze_inputs(run_id=run_id, consumer_id=provisional.packet.consumer_id,
        batch_class=provisional.packet.batch_class, scope=request['scope'], logical_eval_time=point.isoformat(),
        contents=contents, reads=tape.reads)
    identity = save_inputs(value, path=input_store)
    return {'input_hash': identity, 'input_store': str(Path(input_store).resolve()), 'capture_calls': tape.calls}


def run_pair(*, input_store, input_hash, root, pair_id):
    isolation = _require_isolation()
    root = Path(root).resolve()
    if not root.is_relative_to(isolation) or not Path(input_store).resolve().is_relative_to(isolation):
        raise PermissionError('pair destinations must stay under parent isolation')
    if not pair_id:
        raise ValueError('pair_id required')
    value = load_inputs(input_hash, path=input_store)
    expected = value.contents[si.MODEL_CONFIG]['dependency_hashes']
    if expected != implementation_hashes():
        raise ValueError('frozen business implementation/configuration drifted; capture again')
    outputs = []
    ids = []
    def event(kind, **detail):
        with sqlite3.connect(input_store) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS paired_business_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, pair_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS paired_events_no_update BEFORE UPDATE ON paired_business_events
                BEGIN SELECT RAISE(ABORT, 'pair events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS paired_events_no_delete BEFORE DELETE ON paired_business_events
                BEGIN SELECT RAISE(ABORT, 'pair events are append-only'); END;
            ''')
            conn.execute('INSERT INTO paired_business_events(pair_id,body) VALUES (?,?)',
                (pair_id, json.dumps({'kind': kind, 'input_hash': input_hash, **detail}, sort_keys=True)))
    event('started', root=str(root))
    for label, entry in [('legacy', legacy_business), ('dispatcher', dispatcher_business)]:
        replay = ReplayReads(value)
        tape = Tape(replay=replay, rows=copy.deepcopy(value.contents[si.MODEL_CONFIG]['rows']))
        identity = pair_id + ':' + label
        try:
            output = replay.invoke(entry, run_id=identity, path=input_store, value=value,
                                   root=root/label, business_run_id=identity, tape=tape)
        except Exception as exc:
            event('failed', side=label, run_id=identity, error=str(exc), faults=tape.faults,
                  completed_run_ids=ids)
            raise
        outputs.append(output)
        ids.append(identity)
        event('side_completed', side=label, run_id=identity,
              projection_refs=[p['projection_id'] for p in output['projections']])
    proof = {'input_store': str(Path(input_store).resolve()), 'left_run_id': ids[0], 'right_run_id': ids[1]}
    event('completed', proof=proof)
    return {'pair_id': pair_id, 'input_hash': input_hash, 'scope': value.packet.scope,
            'proof': proof, 'left': outputs[0], 'right': outputs[1], 'tradable': False}
