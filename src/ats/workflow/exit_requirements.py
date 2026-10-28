"""New-contract exit audit; historical comparison tools remain diagnostic only.

Candidate replay proves requirements/lineage, never production consumer zero.
Production retirement additionally needs per-entry observations over its completed
window; missing observations, active legacy authority or broken history stay pending.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from . import acceptance_reports as reports
from . import cutover as plane
from . import legacy_retirement as registry
from . import schedule_runtime as scheduling
from .scoped_routes import RouteIdentity


def verify_new_coverage(requirements, *, report_reader=None):
    """Requirements are named before execution; only actual report remeasurement counts."""
    if not requirements or len({r['use'] for r in requirements}) != len(requirements):
        raise ValueError('nonempty unique new requirement uses required')
    rows = []
    for requirement in requirements:
        row = {'use': requirement['use'], 'consumer': requirement['consumer'],
               'scope': requirement['scope'], 'status': 'missing', 'problems': []}
        try:
            state = (report_reader or reports.read)(requirement['report_id'], path=requirement.get('report_db'))
            body = state['body']
            reports._validate(body)  # opens original new/replay stores and measures fixed assertions
            value, runs, _ = reports._executions(body['proof'], body['input_hash'])
            if body['scope'] != requirement['scope']:
                raise ValueError('required business scope differs')
            batch = type('CoverageBatch', (), {'scope': requirement['scope'],
                'batch_class': requirement['batch_class'], 'consumers': (requirement['consumer'],),
                'required_surfaces': tuple(requirement['dimensions']),
                'shadow_report_id': requirement['report_id']})()
            ok, problems = reports.check_batch_report(batch, path=requirement.get('report_db'))
            if not ok:
                raise ValueError('; '.join(problems))
            row.update(status='passed', body_hash=state['body_hash'],
                history_refs=[ref for execution in body['executions'] for ref in execution['output_refs']],
                actual_new_reads=[read for run in runs for read in run['reads']],
                actual_governed_inputs=[read for read in value.reads
                    if read['request']['consumer']==requirement['consumer']],
                production_zero_claim=False)
        except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            row['problems'].append(str(exc))
        rows.append(row)
    return {'status': 'passed' if all(r['status']=='passed' for r in rows) else 'pending',
            'requirements': rows, 'criterion': 'new contract coverage/admission/lineage/history; no old oracle'}


def required_plan_uses(plan):
    """Derive the selected request's dependency closure before reading reports.

    ARCHITECTURE sections 8.2/15.3 govern the required set: a
    single research task need not pass unrelated workflows. This inventory
    covers analysis tasks only; decision-profile and downstream approval/trade
    requirements are checked by their own business gates.
    """
    from .planning import TASK_ROLE
    from .runtime_reads import task_identity

    return [{"use": task.task_id + ":" + task.scope.key,
             "consumer": task_identity(plan, task).consumer_id,
             "task_id": task.task_id, "task_scope": task.scope.model_dump(mode="json"),
             "role": TASK_ROLE[task.task_id], "schema": task.output_schema,
             "dependencies": list(task.dependencies)} for task in plan.tasks]


def verify_plan_coverage(plan, bindings):
    """Require every predeclared task scope; an executed subset cannot define coverage.

    Bindings supply report references, never the required use list. Each report
    is remeasured through original stores, then its measured task scope is
    compared to the independently expanded selected plan.
    """
    required = required_plan_uses(plan)
    expected = {r["use"] for r in required}
    if not isinstance(bindings, dict):
        raise ValueError("report bindings must be keyed by required use")
    missing = sorted(expected - set(bindings))
    extra = sorted(set(bindings) - expected)
    if missing or extra:
        return {"status": "pending", "required_uses": required,
                "missing_uses": missing, "unexpected_uses": extra,
                "criterion": "ARCHITECTURE 8.2 and 15.3 selected dependency closure"}
    requirements = [{**bindings[r["use"]], **r} for r in required]
    checked = verify_new_coverage(requirements)
    for requirement, row in zip(requirements, checked["requirements"]):
        if row["status"] != "passed":
            continue
        try:
            body = reports.read(requirement["report_id"], path=requirement.get("report_db"))["body"]
            measured = [a for a in body["matrix"]["assertions"] if a["required"] and
                a["dimension"] == "analysis" and
                a["expected"]["task"]["task_id"] == requirement["task_id"] and
                a["expected"]["task"]["scope"] == requirement["task_scope"]]
            if len(measured) != 1:
                raise ValueError("required task scope was not measured by this report")
            task = measured[0]["expected"]["task"]
            if task["output_schema"] != requirement["schema"]:
                raise ValueError("required task schema differs from measured report")
            row.update(task_id=task["task_id"], task_scope=task["scope"],
                       required_by="ARCHITECTURE 8.2/15.3")
        except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            row["status"] = "missing"
            row["problems"].append(str(exc))
    checked["required_uses"] = required
    checked["coverage_scope"] = "selected analysis task dependency closure only"
    checked["decision_approval_trade_or_production_claim"] = False
    checked["status"] = "passed" if all(r["status"] == "passed" for r in checked["requirements"]) else "pending"
    return checked


def verify_recovery_window(*, start, end, observations, required_drills, stopped, history_readable, now=None):
    point = now or datetime.now(UTC)
    first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if first.tzinfo is None or last.tzinfo is None or last <= first:
        raise ValueError('explicit nonempty observation window with timezone required')
    problems = []
    if point < last: problems.append('observation window unfinished')
    if not observations or any(not _production_observation_verified(r) or r.get('run_mode') != 'production'
            or r.get('window') != {'start':start,'end':end} for r in observations):
        problems.append('observation receipts missing/failed')
    if not required_drills or any(not _artifact_verified(r)
            or r.get('history_preserved') is not True or r.get('old_worker_fenced') is not True
            for r in required_drills):
        problems.append('actual safe stop/accepted-version recovery drill incomplete')
    if stopped: problems.append('stop conditions triggered')
    if not history_readable: problems.append('historical references unresolved')
    return {'status':'passed' if not problems else 'pending','problems':problems,
        'window':{'start':start,'end':end},'recovery_target':'accepted version or remain stopped'}


def _artifact_verified(receipt):
    """A label/reference alone cannot certify a recovery or observation.

    Raw JUnit must contain every predeclared case, successful and unskipped;
    protected implementation hashes bind the executed assertions. These receipts
    demonstrate engineering probes, never production observation by themselves.
    """
    # Qualification tier only: the rollback evidence citing this drill must not
    # retire on a test or evidence-script edit (those are held by the freeze).
    from .business_replay_inputs import qualification_implementation_hashes
    try:
        if receipt.get('kind') != 'junit' or receipt.get('status') != 'passed':
            return False
        raw = Path(receipt['reference']).read_bytes()
        if (hashlib.sha256(raw).hexdigest() != receipt['sha256']
                or receipt['implementation'] != qualification_implementation_hashes()):
            return False
        required = receipt['cases']
        if not required or len(required) != len(set(required)):
            return False
        cases = {case.attrib.get('classname','')+'::'+case.attrib['name']:case
                 for case in ET.fromstring(raw).iter('testcase')}
        return all(name in cases and not any(cases[name].find(tag) is not None
            for tag in ('failure','error','skipped')) for name in required)
    except (OSError, KeyError, ValueError, TypeError, ET.ParseError):
        return False


def _production_observation_verified(receipt):
    """Reopen actual production entry receipts and independent trigger coverage.

    Empty windows, missing consumers, legacy/fallback reads and isolated JUnit
    are unknown or failed. A caller-supplied zero counter never supplies proof.
    """
    try:
        if receipt.get('kind') != 'production_consumer_receipts':
            return False
        path = Path(receipt['reference']).resolve()
        if path != Path(plane.default_cutover_db_path()).resolve():
            return False
        first, last = receipt['window']['start'], receipt['window']['end']
        required = [RouteIdentity(**row) for row in receipt['identities']]
        if not required:
            return False
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute('SELECT * FROM production_consumer_receipts WHERE started_at>=? AND ended_at<=?',(first,last))]
        for who in required:
            selected = [r for r in rows if json.loads(r['identity_json']) == who.as_row()]
            if not selected:
                return False
            for row in selected:
                reads = json.loads(row['reads_json'])
                if row['route'] != 'target' or row['status'] != 'passed' or not reads or any(
                    r.get('business_identity') != who.as_row() or r.get('run_mode') != 'production'
                    or r.get('route') != 'target' or r.get('status') != 'complete' or r.get('fallback')
                    for r in reads
                ):
                    return False
        # Pure hand-picked successful calls do not prove scheduled exits.
        coverage = scheduling.compare_triggers(scheduling.expected_triggers(
            datetime.fromisoformat(first),datetime.fromisoformat(last)))
        return coverage['clean'] is True
    except (OSError, sqlite3.Error, KeyError, ValueError, TypeError, RuntimeError):
        return False


def production_inventory(*, cutover_path=None, schedule_path=None):
    """Read actual authority and registered callers; absence is unknown, never zero.

    No migration, bootstrap, route setter or source lifecycle action is used here.
    This records current pending conditions even when candidate new entry is green.
    """
    from .boundary_evidence import declaration_report
    result = {'status':'pending','criterion':'production callers zero, separately from candidate proof',
              'entries':[], 'call_points':declaration_report()}
    for tomb in registry.load_registry().tombstones():
        if tomb.status != 'pending':continue
        result['entries'].append({'identifier':tomb.identifier,'target_phase':tomb.target_phase,
            'consumers':list(tomb.consumers),'status':'pending',
            'problems':[tomb.missing_condition,'complete production entry observations and clean window not supplied'],
            'historical_diagnostics_do_not_count_as_production_consumers':True})
    authority = Path(cutover_path or plane.default_cutover_db_path()).resolve()
    schedule = Path(schedule_path or scheduling.state_path()).resolve()
    try:
        plane.assert_runtime_startup(caller='production exit inventory',path=authority)
        with closing(sqlite3.connect(authority.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='scoped_cutover_routes'").fetchone()
            routes = [dict(r) for r in conn.execute('SELECT * FROM scoped_cutover_routes')] if exists else []
            trace_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='production_consumer_receipts'").fetchone()
            result['production_entry_receipts'] = [dict(r) for r in conn.execute('SELECT * FROM production_consumer_receipts')] if trace_table else []
            result['production_entry_receipts_missing'] = not bool(trace_table)
        with closing(sqlite3.connect(schedule.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            owners = [dict(r) for r in conn.execute('SELECT * FROM schedule_scope_owners')]
        result.update(routes=[{**r,'identity':RouteIdentity(**json.loads(r['identity_json'])).as_row()} for r in routes],
                      owners=owners)
    except (OSError,sqlite3.Error,ValueError,plane.CutoverError) as exc:
        result['problems']=[str(exc)]
    return result
