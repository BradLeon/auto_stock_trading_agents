"""Current new requirements and production exit pending are distinct outcomes."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_phase_f_acceptance_reports import actual as _actual
from test_phase_f_acceptance_reports import sign
from test_phase_f_research_gate import business_inputs as _business_inputs
from test_phase_f_research_gate import research_isolation as _isolated

from ats.workflow import exit_requirements as exit

actual=_actual
business_inputs=_business_inputs
isolated=_isolated


def requirement(actual):
    return {'use':'technical entity input/output lineage','consumer':'technical',
        'scope':actual.value.packet.scope,'report_id':'good','report_db':str(actual.path),
        'batch_class':'research_read','dimensions':['inputs','schedule','analysis']}


def test_actual_new_coverage_resolves_stores_without_old_oracle_and_keeps_prod_pending(actual,record_property):
    sign(actual)
    checked=exit.verify_new_coverage([requirement(actual)])
    assert checked['status']=='passed',checked
    row=checked['requirements'][0]
    assert row['actual_new_reads'] and row['history_refs'] and not row['production_zero_claim']
    inventory=exit.production_inventory()
    assert inventory['status']=='pending' and inventory['call_points']
    assert inventory['owners']
    assert all(r['status']=='pending' for r in inventory['entries'])
    record_property('new_coverage_and_current_exit',json.dumps({'coverage':checked,'inventory':inventory}))


@pytest.mark.parametrize('gap',['scope','unsigned','history','dimension'])
def test_missing_new_use_or_broken_historical_store_refuses(actual,gap):
    item=requirement(actual)
    if gap!='unsigned':sign(actual)
    if gap=='scope':item['scope']={**item['scope'],'id':'UNMEASURED'}
    if gap=='dimension':item['dimensions'].append('approval')
    if gap=='history':
        # Corrupt a historical execution reference, preserving the immutable report.
        from pathlib import Path
        Path(actual.body['executions'][0]['store']).rename(actual.root/'missing-execution.sqlite')
    assert exit.verify_new_coverage([item])['status']=='pending'


def test_missing_required_uses_and_unfinished_failed_or_undrilled_window_refuse():
    with pytest.raises(ValueError):exit.verify_new_coverage([])
    now=datetime.now(UTC)
    common={'start':(now-timedelta(hours=2)).isoformat(),'end':(now-timedelta(hours=1)).isoformat(),
        'observations':[{'reference':'fixture-observation','status':'passed'}],
        'required_drills':[{'reference':'fixture-current-drill','status':'passed',
            'history_preserved':True,'old_worker_fenced':True}],
        'stopped':False,'history_readable':True,'now':now}
    # Passed metadata without reopening raw probes is not evidence.
    assert exit.verify_recovery_window(**common)['status']=='pending'
    for change in ({'end':(now+timedelta(hours=1)).isoformat()}, {'observations':[]},
                   {'required_drills':[]},{'stopped':True},{'history_readable':False}):
        assert exit.verify_recovery_window(**{**common,**change})['status']=='pending'


def test_selected_plan_does_not_require_unrelated_workflows():
    from ats.agent.task_projection import ProjectionScope
    from ats.workflow.planning import build_plan
    from ats.workflow.run_contracts import TriggerContext

    plan = build_plan(requested_tasks=["technical-review"], scope=ProjectionScope(kind="entity", id="COHR"),
        trigger=TriggerContext(kind="manual", workflow_id="technical-review", trigger_id="selected"),
        as_of=datetime.now(UTC).isoformat(), run_id="selected")
    uses = exit.required_plan_uses(plan)
    assert [r["use"] for r in uses] == ["technical-review:entity:COHR"]
    checked = exit.verify_plan_coverage(plan, {})
    assert checked["status"] == "pending" and checked["missing_uses"] == [uses[0]["use"]]


def test_executed_subset_cannot_define_required_fundamental_dependency_closure():
    from ats.agent.task_projection import ProjectionScope
    from ats.workflow.planning import build_plan
    from ats.workflow.run_contracts import TriggerContext

    plan = build_plan(requested_tasks=["fundamental-routine"], scope=ProjectionScope(kind="entity", id="COHR"),
        trigger=TriggerContext(kind="manual", workflow_id="fundamental-routine", trigger_id="dependency"),
        as_of=datetime.now(UTC).isoformat(), run_id="dependency")
    uses = exit.required_plan_uses(plan)
    assert {r["task_id"] for r in uses} == {"information-brief", "fundamental-routine"}
    checked = exit.verify_plan_coverage(plan, {"fundamental-routine:entity:COHR": {"report_id": "subset"}})
    assert checked["status"] == "pending"
    assert checked["missing_uses"] == ["information-brief:entity:COHR"]


def test_actual_report_only_certifies_its_measured_task_scope(actual):
    from ats.agent.task_projection import ProjectionScope
    from ats.workflow.planning import build_plan
    from ats.workflow.run_contracts import TriggerContext

    sign(actual)
    raw = actual.matrix("new").body["plan"]
    plan = build_plan(requested_tasks=raw["requested_tasks"], scope=ProjectionScope(**raw["request_scope"]),
        trigger=TriggerContext(**raw["trigger"]), as_of=raw["as_of"], run_id=raw["run_id"],
        config_dir=raw["config_root"], task_inputs=raw["task_inputs"])
    item = requirement(actual)
    use = exit.required_plan_uses(plan)[0]["use"]
    checked = exit.verify_plan_coverage(plan, {use: item})
    assert checked["status"] == "passed", checked
    assert checked["requirements"][0]["task_scope"] == {"kind": "entity", "id": "COHR"}


def test_recovery_drill_receipt_binds_the_qualification_tier_not_tooling(tmp_path):
    """The rollback evidence cites this receipt, so it must retire with runtime
    code but not with tests or evidence scripts (those are held by the freeze)."""
    import hashlib

    from ats.workflow.business_replay_inputs import (
        implementation_hashes,
        qualification_implementation_hashes,
    )
    raw = tmp_path / 'drill.junit.xml'
    raw.write_text('<testsuite><testcase classname="drill" name="stop"/></testsuite>')
    receipt = {'kind':'junit','status':'passed','reference':str(raw),
        'sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'cases':['drill::stop'],
        'implementation':qualification_implementation_hashes()}
    assert exit._artifact_verified(receipt)
    assert not [p for p in receipt['implementation'] if p.startswith(('tests/', 'scripts/'))]
    # A receipt pinned to the full tree (tests included) is a different binding.
    assert exit._artifact_verified({**receipt, 'implementation':implementation_hashes()}) is False
    runtime = dict(receipt['implementation'], **{'src/ats/workflow/consumer_reads.py':'0'*64})
    assert exit._artifact_verified({**receipt, 'implementation':runtime}) is False
