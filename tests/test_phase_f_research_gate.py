"""Actual read models, dispatcher business runs and fixed decision requirements."""
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from test_phase_e_dispatcher import _payload
from test_phase_f_safe_reads import (
    isolated as safe_isolated,
)
from test_phase_f_safe_reads import (
    scope,
    seed_performance,
)

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.agents.chief import assemble
from ats.config import load_sector_config
from ats.graph import chief
from ats.graph.chief_state import ChiefDecisionState
from ats.runtime import cli
from ats.workflow.consumer_reads import trace_reads
from ats.workflow.cutover_routing import RouteUnavailable
from ats.workflow.runtime_reads import bind_read


@pytest.fixture(name="isolated")
def research_isolation(tmp_path):
    yield from safe_isolated.__wrapped__(tmp_path)


def small_plan():
    return {role: {"role": role, "scopes": [ProjectionScope(kind=kind, id=ident)]}
            for role, kind, ident in [
                ("layer_analysis", "layer", "L1_app"),
                ("information_brief", "entity", "COHR"),
                ("sector_allocation", "sector", "ai_hardware"),
                ("fundamental_expectation_update", "entity", "COHR"),
                ("macro_review", "portfolio", ""),
                ("technical_review", "entity", "COHR")]}


def seed_research(store, plan=None):
    envelopes = []
    for role, item in (plan or small_plan()).items():
        for target in item["scopes"]:
            envelope = build_envelope(role=role, scope=target, payload=_payload(role, target),
                as_of=(datetime.now(UTC)-timedelta(seconds=1)).isoformat(),
                valid_until=(datetime.now(UTC)+timedelta(hours=1)).isoformat(),
                input_refs=["accepted:fixture-input@v1"], data_vintage_refs=["fixture-vintage@v1"])
            store.save_task_projection_envelope(envelope)
            envelopes.append(envelope)
    return envelopes


def test_chief_actual_render_requires_every_scope_and_keeps_full_references(isolated, monkeypatch, record_property):
    plan = small_plan()
    plan["technical_review"]["scopes"].append(ProjectionScope(kind="entity", id="NVDA"))
    monkeypatch.setattr(assemble, "projection_query_plan", lambda: plan)
    envelopes = seed_research(isolated, plan)
    seed_performance(isolated)
    with bind_read(scope()), trace_reads() as rows:
        context = assemble.build(live_broker=False)
        snapshot, detail = assemble.build_chief_snapshot(isolated)
    assert snapshot.complete and len(snapshot.items) == 7
    assert all(e.projection_id in context.as_context() and e.content_hash in context.as_context() for e in envelopes)
    isolated.conn.execute("DELETE FROM task_projection_envelopes WHERE projection_id=?", (envelopes[-1].projection_id,))
    isolated.conn.commit()
    with bind_read(scope()), pytest.raises(RouteUnavailable, match="required Chief"):
        assemble.build(live_broker=False)
    record_property("read_trace", json.dumps(rows))
    record_property("snapshot", json.dumps(snapshot.to_payload()))
    record_property("rendered_context", context.as_context())


def test_chief_missing_projection_stops_actual_graph_before_model_or_cycle(isolated, monkeypatch):
    monkeypatch.setattr(assemble, "projection_query_plan", small_plan)
    seed_research(isolated, {k:v for k,v in small_plan().items() if k != "technical_review"})
    monkeypatch.setattr(assemble, "build", lambda **k: pytest.fail("context assembled with missing required projection"))
    state = ChiefDecisionState(cycle_id="missing-research", as_of=datetime.now(UTC), event_data={"COHR":{}}, use_broker=False)
    out = chief.assemble_context(state)
    assert out["gap_report"] and not out["context_text"]
    assert chief.route_after_assemble(state.model_copy(update=out)) == "end"
    assert isolated.conn.execute("SELECT count(*) FROM decision_cycles").fetchone()[0] == 0


@pytest.mark.parametrize("damage", ["payload", "refs", "schema", "withdrawn", "expired"])
def test_chief_refuses_damaged_or_unusable_required_projection(isolated, monkeypatch, damage):
    monkeypatch.setattr(assemble, "projection_query_plan", small_plan)
    envelopes = seed_research(isolated)
    field, value = {"payload": ("payload", '{}'), "refs": ("input_refs", '["different-input"]'),
                    "schema": ("schema_version", "v0"), "withdrawn": ("status", "failed"),
                    "expired": ("valid_until", (datetime.now(UTC)-timedelta(seconds=1)).isoformat())}[damage]
    isolated.conn.execute(f"UPDATE task_projection_envelopes SET {field}=? WHERE projection_id=?", (value,envelopes[0].projection_id))
    isolated.conn.commit()
    with bind_read(scope()), pytest.raises((RouteUnavailable, ValueError)):
        assemble.build(live_broker=False)


def test_sector_actual_cli_html_uses_governed_projection(isolated, monkeypatch, tmp_path, record_property):
    config = load_sector_config("ai_hardware").model_copy(update={"output_dir":str(tmp_path)})
    monkeypatch.setattr("ats.config.load_sector_config", lambda name: config)
    row = next(e for e in seed_research(isolated) if e.agent_role == "sector_allocation")
    monkeypatch.setattr(isolated, "latest_sector_review", lambda *a: pytest.fail("target CLI read legacy sector_reviews"))
    with trace_reads() as trace:
        assert cli.main(["sector", "html", "ai_hardware"]) == 0
    html = next(tmp_path.glob("sector-*.html")).read_text()
    assert row.projection_id in html and row.content_hash in html and "neutral" in html
    assert any(row.projection_id in item["refs"] for item in trace)
    record_property("read_trace", json.dumps(trace))
    record_property("html", html)


def test_sector_actual_cli_missing_projection_is_explicit_failure(isolated, tmp_path, monkeypatch):
    config = load_sector_config("ai_hardware").model_copy(update={"output_dir":str(tmp_path)})
    monkeypatch.setattr("ats.config.load_sector_config", lambda name: config)
    with pytest.raises(RouteUnavailable, match="Sector allocation"):
        cli.main(["sector", "html", "ai_hardware"])
    assert not list(tmp_path.glob("*.html"))


def test_references_resolve_after_new_process(isolated, tmp_path, record_property):
    envelopes = seed_research(isolated)
    selected = [{"role":e.agent_role,"scope_kind":e.scope.kind,"scope_id":e.scope.id,
                 "projection_id":e.projection_id,"content_hash":e.content_hash} for e in envelopes]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(selected))
    program = """import json,sys
from ats.memory import get_store
from ats.agents.chief.assemble import _envelope_from_row
from ats.agent.task_projection import ProjectionScope
from ats.workflow.consumer_reads import read_projection
rows=json.load(open(sys.argv[1]))
for r in rows:
 s=ProjectionScope(kind=r['scope_kind'],id=r['scope_id'])
 v=read_projection(get_store(),consumer='chief',role=r['role'],scope=s,projection_id=r['projection_id'],content_hash=r['content_hash'])
 assert v and v['input_refs'] and v['data_vintage_refs']
print(json.dumps(rows))"""
    result = subprocess.run([sys.executable,"-c",program,str(manifest)],env=os.environ.copy(),capture_output=True,text=True,check=True)
    assert json.loads(result.stdout) == selected
    record_property("reopened_refs", result.stdout)


def test_sector_exact_scope_qualification_rechecked_before_html(isolated, monkeypatch, tmp_path):
    from test_phase_f_safe_reads import activate

    from ats.workflow import runtime_reads as rr
    now = datetime.now(UTC)
    identity = rr.business_identity("sector", kind="sector", scope_id="ai_hardware",
        entities=rr.configured_entities("sector","ai_hardware"),
        explicit={"kind":"sector","id":"ai_hardware","entities":rr.configured_entities("sector","ai_hardware"),
                  "time_range":{"start":(now-timedelta(minutes=5)).isoformat(),"end":(now+timedelta(minutes=5)).isoformat()}})
    seed_research(isolated)
    activate(identity, tmp_path, monkeypatch)
    with pytest.raises(RouteUnavailable):
        cli.main(["--read-scope-json",json.dumps({"sector":identity.scope}),"sector","html","ai_hardware"])


def test_sector_legacy_report_reader_remains_compatible(isolated, monkeypatch):
    from ats.agents.sector import viz
    from ats.schemas.sector import SectorReview
    legacy = SectorReview(sector="ai_hardware",as_of=datetime.now(UTC),regime="legacy fixture")
    isolated.save_sector_review(legacy)
    observed=[]
    original=viz.build_bundle
    def capture(config, review, **kwargs):
        observed.append(review.regime)
        return original(config,review,**kwargs)
    monkeypatch.setattr(viz,"build_bundle",capture)
    monkeypatch.setattr(viz,"write_html",lambda *a:None)
    # The undecorated renderer exercises the legacy compatibility model under
    # physical isolation; separate tests above exercise the real gated CLI.
    assert cli.run_sector_html.__wrapped__("ai_hardware") == 1
    assert observed and set(observed) == {"legacy fixture"}


@pytest.fixture
def business_inputs(isolated, monkeypatch, tmp_path):
    import shutil
    from types import SimpleNamespace

    import pandas as pd
    import yaml

    from ats.agents import base
    from ats.agents.fundamental.outputs import ClassificationView
    from ats.agents.information import documents
    from ats.agents.layer import layer_review
    from ats.agents.macro import review as macro_review
    from ats.agents.macro.outputs import MacroReviewLLMView
    from ats.agents.pead.outputs import ContextUpdateView
    from ats.agents.sector import rotation, structure
    from ats.agents.sector.outputs import LayerRotationView, LayerVerdictView, StructureView
    from ats.config import REPO_ROOT, reset_config_cache
    from ats.data import document_assets, macro
    from ats.data.stores.unstructured import get_platform_unstructured_store

    root = tmp_path / "config"
    shutil.copytree(REPO_ROOT/"config", root)
    sector_file=root/"sectors/ai_hardware.yaml"
    cfg=yaml.safe_load(sector_file.read_text())
    cfg["layers"]=[next(layer for layer in cfg["layers"] if layer["key"]=="L4_interconnect")]
    cfg["output_dir"]=""
    sector_file.write_text(yaml.safe_dump(cfg,allow_unicode=True))
    pead_file=root/"pead.yaml";g=yaml.safe_load(pead_file.read_text());g["targets"]=["COHR"]
    pead_file.write_text(yaml.safe_dump(g,allow_unicode=True))
    macro_file=root/"macro.yaml";mc=yaml.safe_load(macro_file.read_text());mc["themes"]=[]
    macro_file.write_text(yaml.safe_dump(mc,allow_unicode=True))
    monkeypatch.setenv("ATS_CONFIG_DIR",str(root));reset_config_cache()
    from ats.data.persistent_queue import PersistentIngestionQueue
    queue=PersistentIngestionQueue()
    task_id,_=queue.enqueue(source_id="ibkr_news",scope={"sources":["ibkr_news"]},trigger_kind="manual",
        trigger_ref="fixture-admission",command=["ats","data","ingest"],policy_fingerprint="fixture-v1")
    assert queue.claim("fixture-worker",task_id=task_id)
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_TASK_ID",task_id)
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_LEASE_OWNER","fixture-worker")
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_SOURCE_ID","ibkr_news")
    repository=get_platform_unstructured_store()
    at=datetime.now(UTC)-timedelta(seconds=3)
    document=document_assets.ingest(entity="COHR",key="fixture-news",doc_type="article",
        text="Revenue guidance increased to 120. New factory adds ten production lines. "*30,
        source="ibkr_news",source_url="fixture:accepted-news",title="新增产线 revenue 120",
        published_at=at.isoformat(),now=at,min_chars=1,store=repository)
    assert document is not None
    class SharedReadRepository:
        def __getattr__(self,name):
            def read(*args,**kwargs):
                from ats.data.stores.unstructured import PlatformUnstructuredRepository
                reader=PlatformUnstructuredRepository(repository.path)
                try:return getattr(reader,name)(*args,**kwargs)
                finally:reader.close()
            return read
        def close(self):pass
    class StructuredFixture:
        def observations(self,**filters):
            return [{"observation_id":"accepted-company-v1","known_at":at.isoformat(),
                     "metric_id":"financial.revenue","value":100,"period":"2026-Q2"}]
        def close(self):pass
    from ats.data.products import DataProducts
    products=DataProducts(structured_repository=StructuredFixture(),unstructured_repository=SharedReadRepository())
    monkeypatch.setattr("ats.data.products.get_platform_data_products",lambda **kwargs:products)
    from ats.data import consensus, fundamentals
    monkeypatch.setattr(fundamentals,"_yf_info",lambda symbol:{"marketCap":1e9,"beta":1,"forwardPE":20})
    monkeypatch.setattr(fundamentals,"_finnhub_light",lambda symbol:{"market_cap":1e9,"beta":1,"fwd_pe":20})
    monkeypatch.setattr(consensus,"_yf_consensus",lambda symbol:{})
    monkeypatch.setattr(consensus,"_yf_analyst",lambda symbol:{})
    responses=[]
    def model(agent,schema,context,**kwargs):
        responses.append({"agent":agent,"schema":schema.__name__,"context":context})
        if schema is LayerVerdictView:
            return schema(layer_key="L4_interconnect",layer_status="steady",confidence=.2,
                          rationale="Controlled admitted inputs; uncertainties retained.")
        if schema is LayerRotationView:
            return schema(regime="steady",summary="Layer inputs imply neutral allocation.")
        if schema is StructureView:return schema()
        if schema is ContextUpdateView:
            return schema(materiality=.8,event_summary="Revenue guidance and capacity changed.")
        if schema is MacroReviewLLMView:
            return schema(regime="neutral",summary="Controlled inputs; optional FactSet remains unavailable.")
        if schema is ClassificationView:
            return schema(items=[{"index":0,"classification":"新增","reason":"new capacity"}])
        raise AssertionError("unexpected model schema: "+schema.__name__)
    for module in (base,layer_review,rotation,structure,documents,macro_review):
        monkeypatch.setattr(module,"run_structured",model)
    # Replace external transports, never the role entry, compute, or publisher.
    monkeypatch.setattr(macro,"fetch",lambda:None)
    monkeypatch.setattr(macro,"fetch_series",lambda *a,**k:{})
    from ats.data.runtime import market_data
    def close_frame(symbols,period):
        dates=pd.date_range(end=datetime.now(UTC).date(),periods=500)
        frame=pd.DataFrame({symbol:([20.0]*500 if symbol.startswith("^") else
                                     [100+i*.1 for i in range(500)]) for symbol in symbols},index=dates)
        return frame,{symbol:symbol for symbol in symbols}
    monkeypatch.setattr(market_data,"_download_close_frame",close_frame)
    from ats.data import websearch
    monkeypatch.setattr(websearch,"search_news",lambda *a,**k:[])
    # No model/network fallback can silently leave the fixture environment.
    import socket
    monkeypatch.setattr(socket.socket,"connect",lambda *a:pytest.fail("unexpected real network connection"))
    yield SimpleNamespace(root=root,document=document,responses=responses)
    repository.close();reset_config_cache()


def test_six_roles_actual_dispatcher_publish_with_provenance(business_inputs, isolated, record_property):
    import threading

    from ats.workflow.dispatcher import Dispatcher
    from ats.workflow.phase_e import build_plan
    from ats.workflow.run_contracts import TriggerContext
    from ats.workflow.store import WorkflowStore
    calls=[]
    def profile(frame,event,arg):
        if event=="call":
            module=frame.f_globals.get("__name__","")
            if module.startswith(("ats.agents.","ats.data.products.","ats.data.consumer_api")):
                calls.append(module+"."+frame.f_code.co_name)
    threading.setprofile(profile)
    try:
        plan=build_plan(requested_tasks=["sector-review","fundamental-routine","macro-review","technical-review"],
            scope=ProjectionScope(kind="sector",id="ai_hardware"),trigger=TriggerContext(kind="manual",trigger_id="six-role-business"),
            as_of=datetime.now(UTC).isoformat(),run_id="six-role-business",profile_id="ai_hardware",
            enter_decision_cycle=False,config_dir=business_inputs.root,
            task_inputs={"fundamental-routine":{"use_llm":False},"data_vintage_refs":[business_inputs.document.sha256]})
        dispatcher=Dispatcher(workflow_store=WorkflowStore(str(isolated.path)),max_workers=1)
        result=dispatcher.dispatch(plan)
    finally:
        threading.setprofile(None)
    assert result.complete,result.as_dict()
    assert {o.task_id for o in result.outcomes}=={"layer-review","information-brief","sector-review","fundamental-routine","macro-review","technical-review"}
    assert all(o.projections and all(e.workflow_run_id==result.run_id and e.agent_run_id and e.input_refs and e.data_vintage_refs and e.content_hash for e in o.projections) for o in result.outcomes)
    for entry in ["ats.agents.layer.layer_review.run","ats.agents.information.entry.run_information_target",
                  "ats.agents.sector.review.run","ats.agents.fundamental.entry.run_fundamental_pass",
                  "ats.agents.macro.review.run","ats.agents.technical.review.run"]:
        assert entry in calls,entry
    info=next(o for o in result.outcomes if o.task_id=="information-brief")
    assert any(business_inputs.document.document_id in ref for e in info.projections for ref in e.input_refs)
    record_property("actual_call_chain",json.dumps(sorted(set(calls))))
    record_property("dispatch_result",json.dumps(result.as_dict()))
    record_property("published_projections",json.dumps([e.model_dump(mode="json") for o in result.outcomes for e in o.projections]))
    record_property("model_fixture_contexts",json.dumps(business_inputs.responses))


@pytest.mark.parametrize("task,kind,ident,expected",[
    ("technical-review","entity","COHR",{"technical-review"}),
    ("fundamental-routine","entity","COHR",{"information-brief","fundamental-routine"}),
    ("sector-review","sector","ai_hardware",{"layer-review","sector-review"})])
def test_actual_workflow_cli_single_and_dependency_subflows(business_inputs, isolated, capsys, record_property,
                                                            task, kind, ident, expected):
    code=cli.main(["workflow","run","--task",task,"--scope-kind",kind,"--scope-id",ident,
                   "--trigger-id","cli-"+task,"--inputs-json",json.dumps({"fundamental-routine":{"use_llm":False},
                    "data_vintage_refs":[business_inputs.document.sha256]})])
    output=capsys.readouterr().out
    start=output.find('{\n  "trigger_key"')
    assert start>=0,output
    payload=json.loads(output[start:])
    assert code==0 and payload["status"]=="complete",payload
    assert {row["task_id"] for row in payload["result"]["outcomes"]}==expected
    assert all(row["projection_refs"] for row in payload["result"]["outcomes"])
    assert not payload["result"]["decision_cycle_entered"]
    record_property("cli_result",json.dumps(payload))


def decision_run(business_inputs, isolated, *, mode="routine", runner=None, before_dispatch=None):
    from ats.workflow.dispatcher import Dispatcher
    from ats.workflow.phase_e import build_plan
    from ats.workflow.run_contracts import TriggerContext
    from ats.workflow.store import WorkflowStore
    extra={"fundamental-"+mode:{"use_llm":False},
           "data_vintage_refs":[business_inputs.document.sha256]}
    if mode=="event":
        from ats.data import document_assets
        from ats.data.stores.unstructured import get_platform_unstructured_store
        repository=get_platform_unstructured_store()
        at=datetime.now(UTC)-timedelta(seconds=2)
        release=document_assets.ingest(entity="COHR",key="fixture-release-Q3FY2026",
            doc_type="company_release",text="Q3 FY2026 results revenue 120 earnings 3. "*30,
            source="ibkr_news",source_url="fixture:release-Q3FY2026",title="Q3 FY2026 results",
            period="Q3 FY2026",published_at=at.isoformat(),now=at,min_chars=1,store=repository)
        repository.close()
        assert release
        extra["fundamental-event"].update(fiscal_label="Q3 FY2026",trigger="earnings_release",
            live_data=True,material_state="admitted",admitted_material_refs=[release.document_id])
    name="fixed-"+mode
    plan=build_plan(requested_tasks=["sector-review","fundamental-"+mode,"macro-review","technical-review"],
        scope=ProjectionScope(kind="sector",id="ai_hardware"),
        trigger=TriggerContext(kind="manual",trigger_id=name),
        as_of=datetime.now(UTC).isoformat(),run_id=name,profile_id="ai_hardware",
        enter_decision_cycle=True,config_dir=business_inputs.root,
        task_inputs=extra)
    if before_dispatch is not None:
        before_dispatch(plan)
    return Dispatcher(workflow_store=WorkflowStore(str(isolated.path)),
        max_workers=1,chief_runner=runner).dispatch(plan)


@pytest.mark.parametrize("mode",["routine","event"])
def test_fixed_requirements_reach_actual_chief_cycle(business_inputs, isolated, monkeypatch, record_property, tmp_path, mode):
    from ats.decision.repository import DecisionAuditRepository
    seed_performance(isolated)
    monkeypatch.setattr(chief,"_write_chief_report",lambda state:None)
    monkeypatch.setattr("ats.agents.pead.report.write_report",lambda dossier:None)
    def runner(snapshot, manifest):
        state=ChiefDecisionState(cycle_id="actual-fixed-"+mode,as_of=datetime.now(UTC), fundamental_mode=mode,
            use_broker=False,use_llm=False,execute=False,research_snapshot=snapshot.to_payload())
        updates=chief.assemble_context(state)
        assert not updates.get("gap_report"), updates
        state=state.model_copy(update=updates)
        state=state.model_copy(update=chief.chief_decide(state))
        assert not state.decisions
        return chief.persist_decision(state)
    result=decision_run(business_inputs,isolated,mode=mode,runner=runner)
    assert result.complete and result.decision_cycle_ready and result.decision_cycle_entered,result.as_dict()
    cycle=DecisionAuditRepository(isolated).get_cycle("actual-fixed-"+mode)
    assert cycle and cycle["status"]=="no_action"
    frozen=json.loads(cycle["research_snapshot"])
    assert "fundamental-"+mode in frozen["requirements"]["plan"]["requested_tasks"]
    assert len([i for i in frozen["items"] if i["category"]=="fundamental_analysis"])==1
    assert len(frozen["items"])==6
    assert all(i["projection_id"] and i["content_hash"] for i in frozen["items"])
    record_property("actual_cycle",json.dumps(dict(cycle)))
    record_property("decision_run",json.dumps(result.as_dict()))
    path=tmp_path/"fixed-snapshot.json";path.write_text(json.dumps(frozen))
    program="""import json,sys
from ats.memory import get_store
from ats.workflow.decision_requirements import validate_chief
p=json.load(open(sys.argv[1])); plan=validate_chief(get_store(),p)
print(json.dumps({'run_id':plan.run_id,'plan_hash':plan.plan_hash,'requirements_hash':p['requirements']['requirements_hash']}))
"""
    reopened=subprocess.run([sys.executable,"-c",program,str(path)],env=os.environ.copy(),
        capture_output=True,text=True,check=True)
    assert json.loads(reopened.stdout)["plan_hash"]==frozen["requirements"]["plan_hash"]
    record_property("reopened_requirement",reopened.stdout)
    assert cli.main(["chief","run","--offline","--no-llm","--no-execute",
                     "--fundamental-mode",mode,"--decision-profile","ai_hardware"])==0


@pytest.mark.parametrize("damage",["missing_requirements","hash","outcome","manifest","config","lineage","stale"])
def test_fixed_requirement_damage_blocks_actual_cycle_write(business_inputs, isolated, monkeypatch, damage):
    from copy import deepcopy

    from ats.decision.repository import DecisionAuditRepository
    from ats.workflow.phase_e import _hash
    result=decision_run(business_inputs,isolated)
    assert result.decision_cycle_ready,result.as_dict()
    payload=deepcopy(result.chief_snapshot)
    requirement=payload["requirements"]
    if damage=="missing_requirements":payload.pop("requirements")
    elif damage=="hash":requirement["requirements_hash"]="changed"
    elif damage=="outcome":requirement["outcomes"][0]["status"]="failed"
    elif damage=="manifest":requirement["manifest"][0]["content_hash"]="changed"
    elif damage=="config":
        path=business_inputs.root/"technical.yaml"
        path.write_text(path.read_text()+"\n# configuration changed\n")
    elif damage=="lineage":
        isolated.conn.execute("UPDATE task_projection_envelopes SET input_refs='[]' WHERE projection_id=?",
                              (payload["items"][0]["projection_id"],))
        isolated.conn.commit()
    elif damage=="stale":
        isolated.conn.execute("UPDATE task_projection_envelopes SET valid_until=? WHERE projection_id=?",
            ((datetime.now(UTC)-timedelta(seconds=1)).isoformat(),payload["items"][0]["projection_id"]))
        isolated.conn.commit()
    if damage in {"outcome","manifest"}:
        requirement["requirements_hash"]=_hash({k:v for k,v in requirement.items() if k!="requirements_hash"})
    state=ChiefDecisionState(cycle_id="must-not-exist",as_of=datetime.now(UTC),
        use_broker=False,use_llm=False,research_snapshot=payload)
    with pytest.raises((RouteUnavailable,ValueError)):
        chief.persist_decision(state)
    assert DecisionAuditRepository(isolated).get_cycle("must-not-exist") is None


@pytest.mark.parametrize("mode",["event","routine"])
def test_actual_selected_mode_failure_blocks_despite_other_fresh_mode(business_inputs, isolated, monkeypatch, mode):
    monkeypatch.setattr("ats.agents.pead.report.write_report",lambda dossier:None)
    other="routine" if mode=="event" else "event"
    baseline=decision_run(business_inputs,isolated,mode=other)
    assert baseline.decision_cycle_ready,baseline.as_dict()
    module="ats.agents.fundamental.event.compute_tri_diffs" if mode=="event" else "ats.agents.fundamental.routine.build_expectation_updates"
    def fail(*args,**kwargs):raise RuntimeError("selected mode business failure fixture")
    monkeypatch.setattr(module,fail)
    result=decision_run(business_inputs,isolated,mode=mode,
        runner=lambda *args:pytest.fail("Chief entered on failed selected mode"))
    assert not result.complete and not result.decision_cycle_entered
    assert any(o.task_id=="fundamental-"+mode and o.status!="succeeded" for o in result.outcomes)
    assert isolated.conn.execute("SELECT COUNT(*) FROM decision_cycles").fetchone()[0]==0
