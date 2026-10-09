"""Independent requirement oracle and adversarial actual-record checks."""
import copy
import json
from datetime import datetime

import pytest
from test_phase_e_dispatcher import db_paths, _fake_adapter, _config
from test_phase_f_paired_business import input_fixture, isolated_run, request

from ats.agent.task_projection import ProjectionScope
from ats.data.consumer_api import ConsumerInput
from ats.memory import task_store_scope
from ats.workflow import acceptance_assertions as checks, acceptance_matrix as mx, shadow_replay as replay
from ats.workflow.dispatcher import Dispatcher
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.scoped_routes import RouteIdentity
from ats.workflow.store import WorkflowStore
from ats.workflow import schedule_runtime


@pytest.fixture
def measured(tmp_path, db_paths):
    plan = build_plan(requested_tasks=("macro-review",), scope=ProjectionScope(kind="portfolio"),
        trigger=TriggerContext(kind="manual",trigger_id="assertion-scenario"),
        as_of="2026-10-09T00:00:00+00:00",run_id="assertion-new",config_dir=_config(tmp_path),
        task_inputs={"macro-review":{"use_llm":False,"live_data":False},"data_vintage_refs":["facts@v1"],
                     "input_refs":["accepted:fixture-input@v1"]})
    identity = RouteIdentity("research","macro","v1",{"kind":"portfolio","id":"portfolio",
        "entities":["AAA"],"time_range":{"start":plan.as_of,"end":plan.as_of}})
    matrix = mx.freeze(plan,workflow_id="macro-review",identity=identity,model_execution=False,
        criteria={"analysis."+plan.tasks[0].instance_key:[
            {"path":"payload.regime","op":"in","value":["transition","risk_on"]},
            {"path":"payload.indicators","op":"nonempty","value":True}]})
    point = datetime.fromisoformat(plan.as_of)
    data = ConsumerInput(consumer="macro",product="MACRO_DATA",contract_version="v1",owner="data",
        input_mode="persistent",scope=identity.scope,as_of=point,queried_at=point,status="complete",
        completeness="full",input_refs=["facts@v1"],fallback="refuse",payload={"rate":3})
    read = {"request":{"consumer":"macro","product":"MACRO_DATA","scope":identity.scope,"stage":"analysis"},
            "input":data.model_dump(mode="json"),"surface":"persistent_refs"}
    inputs = replay.freeze_inputs(run_id="fixed",consumer_id="macro",batch_class="research_read",
        scope=identity.scope,logical_eval_time=plan.as_of,
        contents={"persistent_refs":[read["input"]],"projection_hash":[]},reads=[read])
    result = Dispatcher(workflow_store=WorkflowStore(db_paths),adapters={"macro-review":_fake_adapter}).dispatch(plan)
    assert result.complete
    with task_store_scope(db_paths) as store:
        yield matrix,{"store":store,"dispatch":result.as_dict(),"inputs":inputs,
                      "consumed_reads":[replay.digest(read["request"])],"schedule":schedule_runtime.snapshot()}


def test_new_requirements_pass_without_legacy_and_different_prose(measured,record_property):
    matrix,evidence = measured
    result = checks.evaluate(matrix,evidence)
    assert result.passed,result.as_dict()
    assert {r.status for r in result.results} == {"passed","not-applicable"}
    assert all(r.refs and r.actual for r in result.results if r.required)
    # Optional diagnostic data never enters the oracle, even if it contradicts new output.
    diagnostic = {**evidence,"legacy":{"failed":True,"payload":{"regime":"risk_off","summary":"different"}}}
    assert checks.evaluate(matrix,diagnostic).as_dict() == result.as_dict()
    record_property("new_requirement_assertions",json.dumps(result.as_dict()))


@pytest.mark.parametrize("damage",["shared_omission","duplicate","failed_task","schema","hash","freshness","scope","lineage","unresolved"])
def test_actual_new_violations_cannot_be_hidden_by_old_side(measured,damage):
    matrix,evidence = measured
    evidence = dict(evidence)
    evidence["dispatch"] = copy.deepcopy(evidence["dispatch"])
    if damage == "shared_omission":
        evidence["dispatch"]["outcomes"] = []
        evidence["legacy"] = {"outcomes":[]}
    elif damage == "duplicate": evidence["dispatch"]["outcomes"] *= 2
    elif damage == "failed_task": evidence["dispatch"]["outcomes"][0]["status"] = "failed"
    elif damage == "unresolved": evidence["dispatch"]["outcomes"][0]["projection_refs"] = ["missing-output"]
    else:
        row = evidence["store"].conn.execute("SELECT * FROM task_projection_envelopes").fetchone()
        columns = {"schema":("schema_name","UnapprovedSchema"),"hash":("content_hash","bad"),
                   "freshness":("as_of","2020-01-01T00:00:00+00:00"),"scope":("scope_id","wrong"),
                   "lineage":("input_refs","[]")}
        column,value = columns[damage]
        evidence["store"].conn.execute("UPDATE task_projection_envelopes SET "+column+"=? WHERE projection_id=?",(value,row["projection_id"]))
        evidence["store"].conn.commit()
    result = checks.evaluate(matrix,evidence)
    assert not result.passed
    assert any(r.status in {"failed","untested"} for r in result.results if r.required)


@pytest.mark.parametrize("missing",["inputs","consumed_reads","dispatch","store","schedule"])
def test_missing_actual_measurement_stays_untested(measured,missing):
    matrix,evidence = measured
    evidence = {k:v for k,v in evidence.items() if k != missing}
    evidence["passed"] = True
    result = checks.evaluate(matrix,evidence)
    assert not result.passed
    assert any(r.status == "untested" for r in result.results)


def test_input_hash_and_consumption_are_independent_requirements(measured):
    matrix,evidence = measured
    evidence["inputs"].contents["persistent_refs"][0]["payload"]["rate"] = 99
    assert checks.evaluate(matrix,evidence).results[0].status == "failed"


@pytest.mark.parametrize("damage",["omission","duplicate_publication","old_generation"])
def test_independent_expected_trigger_refuses_shared_omission_and_duplicate_publish(measured,damage):
    matrix,evidence = measured
    evidence["schedule"] = copy.deepcopy(evidence["schedule"])
    if damage == "omission":
        evidence["schedule"]["claims"] = []
        evidence["legacy"] = {"claims":[]}
    else:
        row = next(r for r in evidence["schedule"]["history"] if r["action"] == "publication")
        if damage == "duplicate_publication": evidence["schedule"]["history"].append(copy.deepcopy(row))
        else:
            payload = json.loads(row["payload"])
            payload["generation"] += 1
            row["payload"] = json.dumps(payload)
    result = checks.evaluate(matrix,evidence)
    assert result.results[1].status == "failed",result.as_dict()
    evidence["consumed_reads"] = ["uncaptured-market"]
    assert checks.evaluate(matrix,evidence).results[0].status == "failed"


@pytest.mark.parametrize("value",[float("nan"),1000,True])
def test_numeric_criterion_rejects_nonfinite_outside_range_and_bool(value):
    with pytest.raises(ValueError,match="criterion failed"):
        checks.check_criteria({"confidence":value},[{"path":"confidence","op":"range","value":[0,1]}])


def test_required_trading_boundaries_never_become_not_applicable_without_evidence():
    from test_phase_f_acceptance_matrix import matrix as trading_matrix
    matrix = trading_matrix(("sector-review","fundamental-routine","macro-review","technical-review"),batch_class="trading")
    result = checks.evaluate(matrix,{"passed":True,"legacy":{"matched":True}})
    assert not result.passed
    assert all(r.status == "untested" for r in result.results if r.dimension in {"risk","approval","attribution"})


def test_actual_risk_rejection_and_counterproposal_satisfy_fixed_requirements(business_inputs,isolated,record_property):
    from ats.decision.repository import DecisionAuditRepository
    from ats.risk.checks import review_revision
    from ats.schemas.decision import TradeDecision
    from ats.schemas.portfolio import PortfolioSnapshot
    point = datetime.now().astimezone()
    order = TradeDecision(symbol="COHR",action="buy",notional_usd=50000,rationale="cap counterexample")
    expected = [{"path":"verdict","op":"eq","value":"rejected"},
                {"path":"allowed_boundary.per_symbol_max_notional.COHR","op":"range","value":[0,25000]}]
    portfolio = PortfolioSnapshot(as_of=point,net_liquidation=1e6,cash=1e6,daily_pnl=0,gross_exposure=0,positions=[])
    review = review_revision([order],portfolio)
    assert review.verdict == "rejected" and review.basis
    repo = DecisionAuditRepository(isolated)
    repo.create_cycle(cycle_id="assertion-reject",trigger_source="manual")
    rev = repo.append_revision(cycle_id="assertion-reject",orders=[order.model_dump(mode="json")])
    repo.record_review(review_id="assertion-reject:review",cycle_id="assertion-reject",revision_no=rev["revision_no"],
        decision_hash=rev["decision_hash"],**review.basis.model_dump(),verdict=review.verdict,
        violations=[v.model_dump(mode="json") for v in review.violations],
        allowed_boundary=review.allowed_boundary.model_dump(mode="json"),
        before_metrics=review.before_metrics,after_metrics=review.after_metrics)
    evidence={"store":isolated,"cycle_id":"assertion-reject","risk_review":review}
    actual,refs = checks.audit_risk(evidence)
    checks.check_criteria(actual,expected)
    assert refs
    with pytest.raises(checks.Untested): checks.audit_approval(evidence)
    record_property("actual_rejection_assertions",json.dumps({"expected":expected,"actual":actual,"refs":refs}))


@pytest.mark.parametrize("criterion",[
    {"path":"payload.x","op":"callback","value":"accept"},
    {"path":"payload.x","op":"range","value":[1,0]},
    {"path":"payload.x","op":"range","value":[0,float("inf")]},
    {"path":"__class__","op":"eq","value":"accept"}])
def test_criteria_must_be_fixed_finite_declarative_conditions(measured,criterion):
    matrix,_ = measured
    with pytest.raises(ValueError):
        checks.validate_criteria({matrix.body["assertions"][2]["id"]:[criterion]},matrix.body["assertions"])


def test_actual_business_new_side_resolves_schedule_and_analysis_without_old_oracle(business_inputs,isolated,record_property):
    req = request()
    plan = build_plan(requested_tasks=["technical-review"],scope=ProjectionScope(kind="entity",id="COHR"),
        trigger=TriggerContext.model_validate(req["trigger"]),as_of=req["logical_time"],
        run_id="assertion-business-new",config_dir=business_inputs.root)
    # Matrix is fixed before the actual new business execution; no legacy result is supplied.
    matrix = mx.freeze(plan,workflow_id="technical-review",
        identity=RouteIdentity("research","technical","v1",{**req["scope"],
            "entities":["COHR"],"time_range":{"start":plan.as_of,"end":plan.as_of}}),
        criteria={"analysis."+plan.tasks[0].instance_key:[{"path":"payload.signal","op":"in","value":["bullish","bearish","neutral"]}]})
    from ats.workflow.evaluation_clock import at
    with at(datetime.fromisoformat(plan.as_of)):
        actual = Dispatcher(workflow_store=WorkflowStore(str(isolated.path)),max_workers=1).dispatch(plan)
    evidence={"store":isolated,"dispatch":actual.as_dict(),"schedule":schedule_runtime.snapshot()}
    result = checks.evaluate(matrix,evidence)
    assert all(r.status == "passed" for r in result.results if r.dimension in {"schedule","analysis"}),json.dumps(result.as_dict())
    assert result.results[0].status == "untested"  # Entire report/runner remains a separate task.
    record_property("actual_new_business_assertions",json.dumps(result.as_dict()))
