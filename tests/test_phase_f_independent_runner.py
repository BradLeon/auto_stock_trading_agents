"""Independent real new-entry capture and cross-process replay; no legacy oracle."""
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_phase_f_paired_business import input_fixture, isolated_run
from test_phase_f_safe_reads import seed_performance
from test_phase_f_trader_a import quote
from ats.workflow import acceptance_runner as runner, acceptance_reports as reports, acceptance_matrix as mx
from ats.workflow import shadow_replay as replay
from ats.workflow.isolation import verified_isolation_root
from ats.workflow.phase_e import build_plan
from ats.workflow.scoped_routes import RouteIdentity
from ats.workflow.run_contracts import TriggerContext
from ats.agent.task_projection import ProjectionScope


def plan_matrix(config, run_id, point, mode="routine", tasks=None, extra=None, batch="trading"):
    scope = {"kind":"sector","id":"ai_hardware","entities":["COHR"],
             "time_range":{"start":point,"end":point}}
    plan = build_plan(requested_tasks=tasks or ["sector-review","fundamental-"+mode,"macro-review","technical-review"],
        scope=ProjectionScope(kind="sector",id="ai_hardware"),trigger=TriggerContext(kind="manual",trigger_id="independent-"+mode),
        as_of=point,run_id=run_id,profile_id="ai_hardware",enter_decision_cycle=batch!="research_read",config_dir=config,
        task_inputs={"fundamental-"+mode:{"use_llm":False,**(extra or {})}})
    criteria = {} if batch == "research_read" else {"risk.contract":[{"path":"verdict","op":"eq","value":"approved"},{"path":"orders_by_symbol.COHR.notional_usd","op":"range","value":[0,25000]}]}
    if batch == "trading": criteria.update({"approval.contract":[{"path":"decision","op":"eq","value":"approved"}],
        "attribution.contract":[{"path":"route.account","op":"eq","value":"DU1"},
                                {"path":"route.route_id","op":"eq","value":"simulation"}]})
    return mx.freeze(plan,workflow_id="independent-"+mode,identity=RouteIdentity("research","trader" if batch=="trading" else "chief" if batch=="decision" else "technical","v1",scope),
                     batch_class=batch,criteria=criteria)


@pytest.mark.parametrize("mode",["routine","event"])
def test_actual_full_capture_and_offline_process_replay(business_inputs,isolated,monkeypatch,record_property,mode):
    from ats.broker.ibkr import IBKRBroker
    from ats.schemas.portfolio import PortfolioSnapshot
    from ats.schemas.decision import BossApproval
    from ats.agents.chief.outputs import ChiefOutput
    from ats.agents.information import extract
    from ats.execution import broker_write_guard as guard
    seed_performance(isolated)
    pf = PortfolioSnapshot(as_of=datetime.now(UTC),net_liquidation=1e6,cash=1e6,daily_pnl=0,gross_exposure=0,positions=[])
    monkeypatch.setattr(IBKRBroker,"get_portfolio",lambda self:pf)
    monkeypatch.setattr("ats.data.execution_prices.session_context",lambda now:(True,now-timedelta(days=1)))
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",lambda symbol,**kw:quote(symbol=symbol))
    monkeypatch.setattr("ats.agents.chief.decide.run_structured",lambda *a,**k:ChiefOutput(summary="Actual six-role governed research",
        decisions=[dict(symbol="COHR",action="buy",notional_usd=1000,conviction=.5,rationale="Actual requirements")]))
    monkeypatch.setattr(extract,"run_structured",lambda agent,schema,context,**kw:schema(insights=[]))
    class Human:
        def request_approval(self,request):
            return BossApproval(status="approved",reviewer="fixture-human",channel="fixture-only",reviewed_at=datetime.now(UTC))
    extra = {}
    if mode=="event":
        from ats.data import document_assets
        from ats.data.stores.unstructured import get_platform_unstructured_store
        repository=get_platform_unstructured_store(); stamp=datetime.now(UTC)-timedelta(seconds=2)
        release=document_assets.ingest(entity="COHR",key="independent-release",doc_type="company_release",
            text="Q3 FY2026 results revenue 120 earnings 3. "*30,source="ibkr_news",source_url="fixture:independent-release",
            title="Q3 FY2026 results",period="Q3 FY2026",published_at=stamp.isoformat(),now=stamp,min_chars=1,store=repository)
        repository.close();assert release
        extra={"fiscal_label":"Q3 FY2026","trigger":"earnings_release","material_state":"admitted","admitted_material_refs":[release.document_id]}
    point=datetime.now(UTC).isoformat();root=verified_isolation_root();inputs=root/"fixed.sqlite"
    def matrix(run_id):return plan_matrix(business_inputs.root,run_id,point,mode,extra=extra)
    # Requirements and simulation protocol precede output. Capture is new only.
    captured=runner.capture(matrix=matrix("capture"),input_store=inputs,root=root/"capture",capture_id="frozen",
        execution={"cycle_id":"independent-chain","account":"DU1","fill_price":100.1},channel=Human())
    value=replay.load_inputs(captured["input_hash"],path=inputs)
    assert {"preapproval_normalization","approved_execution_check"} <= {r["request"]["stage"] for r in value.reads}
    assert guard.is_prohibited() and guard.active_grant() is None
    reports.run(matrix=matrix("new"),input_store=inputs,input_hash=value.input_hash(),root=root/"new",replay_run_id="new")
    matrix_file=root/"matrix.json";matrix_file.write_text(json.dumps(matrix("replayed").as_row()))
    command=["uv","run","--offline","--no-sync","ats","shadow","acceptance-run","--isolation-root",str(root),
        "--input-store",str(inputs),"--packet-hash",value.input_hash(),"--matrix-file",str(matrix_file),"--run-id","replayed"]
    process=subprocess.run(command,env=os.environ.copy(),capture_output=True,text=True)
    assert process.returncode==0,process.stdout+process.stderr
    report=reports.record(report_id="independent",input_hash=value.input_hash(),actor="test",
        proof={"input_store":str(inputs),"new_run_id":"new","replay_run_id":"replayed"},path=root/"report.sqlite")
    assert all(e["assertions"]["passed"] for e in report["executions"]),report
    assert reports.read("independent",path=root/"report.sqlite")["status"]=="unsigned"
    record_property("actual_independent_report",json.dumps(report))
    record_property("actual_process",process.stdout)
    record_property("actual_capture",json.dumps(captured))


@pytest.mark.parametrize("available",[True,False])
def test_research_only_capture_missing_environment_and_legacy_unavailable(business_inputs,isolated,monkeypatch,record_property,available):
    from ats.workflow import paired_business
    monkeypatch.setattr(paired_business,"_legacy",lambda *a,**k:pytest.fail("old entry invoked"))
    root=verified_isolation_root();point=datetime.now(UTC).isoformat();inputs=root/"research.sqlite"
    if not available:
        from ats.data.runtime import market_data
        import pandas as pd
        monkeypatch.setattr(market_data,"_download_close_frame",lambda *a,**k:(pd.DataFrame(),{}))
    def matrix(run_id):return plan_matrix(business_inputs.root,run_id,point,tasks=["technical-review"],batch="research_read")
    captured=runner.capture(matrix=matrix("capture"),input_store=inputs,root=root/"capture",capture_id="research-frozen")
    for name in ("new","replayed"):
        reports.run(matrix=matrix(name),input_store=inputs,input_hash=captured["input_hash"],root=root/name,replay_run_id=name)
    report=reports.record(report_id="research",input_hash=captured["input_hash"],actor="fixture",path=root/"report.sqlite",
        proof={"input_store":str(inputs),"new_run_id":"new","replay_run_id":"replayed"},legacy_diagnostics=["old entry unavailable"])
    assert all(e["assertions"]["passed"] for e in report["executions"]) == available,report
    if not available:
        with pytest.raises(ValueError,match="failed or untested"):
            reports.signoff(report_id="research",action="signed_off",actor="fixture",authority="fixture-only",reason="negative test",path=root/"report.sqlite")
    record_property("actual_research_report",json.dumps(report))


def test_actual_capture_cli_uses_pre_run_matrix(business_inputs,isolated,tmp_path,capsys):
    from ats.runtime.cli import main
    root=verified_isolation_root();point=datetime.now(UTC).isoformat()
    matrix=plan_matrix(business_inputs.root,"capture-cli",point,tasks=["technical-review"],batch="research_read")
    file=root/"matrix.json";file.write_text(json.dumps(matrix.as_row()))
    assert main(["shadow","acceptance-capture","--isolation-root",str(root),"--input-store",str(root/"cli-input.sqlite"),
                 "--matrix-file",str(file),"--run-id","capture-cli"])==0
    payload=json.loads(capsys.readouterr().out)
    value=replay.load_inputs(payload["input_hash"],path=payload["input_store"])
    assert value.packet.batch_class=="research_read" and payload["capture_matrix"]==matrix.as_row()
    assert payload["observed_dispatch"]["status"]=="complete"


def test_chief_report_writes_only_inside_actual_isolation(isolated,tmp_path):
    from ats.agents.chief import report
    from ats.agents.chief.decide import ChiefResult
    production=tmp_path/"production-notes";production.mkdir()
    result=ChiefResult(cycle_id="report-iso",as_of=datetime.now(UTC),summary="Isolated audit",decisions=[],context_text="")
    path=report.write(result,str(production))
    assert path.is_relative_to(verified_isolation_root()) and path.read_text()
    assert list(production.iterdir())==[]
