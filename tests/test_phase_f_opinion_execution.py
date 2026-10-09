"""Actual isolated research -> decision -> execution -> recovery acceptance."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_phase_f_research_gate import business_inputs, research_isolation, decision_run
from test_phase_f_safe_reads import seed_performance
from test_phase_f_trader_a import quote

from ats.execution import broker_write_guard as guard
from ats.execution.clerk import clerk_run
from ats.execution.route_registry import install_route
from ats.execution.simulation import simulated_execution
from ats.graph import chief
from ats.graph.chief_state import ChiefDecisionState
from ats.runtime.cli import run_decision_graph
from ats.schemas.decision import BossApproval
from ats.schemas.portfolio import PortfolioSnapshot
from ats.workflow.analysis_verification import verify_analysis_access
from ats.workflow.consumer_reads import trace_reads, read_projection
from ats.agent.task_projection import ProjectionScope


@pytest.mark.parametrize("future_seconds", [0,60])
def test_broker_snapshot_generated_during_request_uses_receipt_clock(future_seconds):
    from ats.data.runtime.broker import portfolio_snapshot
    class Broker:
        def get_portfolio(self):
            return PortfolioSnapshot(as_of=datetime.now(UTC)+timedelta(seconds=future_seconds),
                                     net_liquidation=1e6,cash=1e6,daily_pnl=0,gross_exposure=0,positions=[])
    packet=portfolio_snapshot(Broker())
    assert packet["status"]==("unavailable" if future_seconds else "complete")


def test_actual_opinion_edges_and_published_lineage(business_inputs, isolated, record_property):
    import os, sqlite3, hashlib
    def fact_hashes():
        with sqlite3.connect(os.environ["ATS_DATA_DB_PATH"]) as conn:
            tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if r[0].startswith("data_") and "processing" not in r[0]]
            return {t:hashlib.sha256(json.dumps(sorted(map(str,conn.execute('SELECT * FROM "'+t+'"')))).encode()).hexdigest()
                    for t in tables}
    original=fact_hashes()
    with verify_analysis_access() as calls, trace_reads() as reads:
        result=decision_run(business_inputs, isolated)
    assert result.complete and result.decision_cycle_ready, result.as_dict()
    assert fact_hashes()==original
    allowed={("layer","sector"),("information","fundamental")}
    edges=[r for r in calls if r.get("api")=="task_projection" and r["status"]=="complete"
           and r["consumer"] in {"sector","fundamental"}]
    assert {(r["producer"],r["consumer"]) for r in edges}==allowed
    published=[e for o in result.outcomes for e in o.projections]
    by_id={e.projection_id:e for e in published}
    for r in edges:
        assert r["refs"] and r["content_hash"]==by_id[r["refs"][0]].content_hash
        children=[e for e in published if e.agent_role=={
            "sector":"sector_allocation", "fundamental":"fundamental_expectation_update"}[r["consumer"]]]
        assert any(r["refs"][0] in e.input_refs for e in children)
    with trace_reads() as refused, pytest.raises(PermissionError, match="undeclared"):
        read_projection(isolated,consumer="fundamental",role="macro_review",scope=ProjectionScope(kind="portfolio"))
    assert refused[0]["status"]=="blocked"
    record_property("actual_access",json.dumps(calls))
    record_property("actual_reads",json.dumps(reads))
    record_property("published",json.dumps([e.model_dump(mode="json") for e in published]))
    record_property("unchanged_shared_facts",json.dumps(original))


@pytest.mark.parametrize("operation", ["provider", "repository", "sql_read", "sql_cursor", "opinion_write", "opinion_read"])
def test_executed_analysis_bypass_invalidates_acceptance(isolated, operation, record_property):
    import os
    import sqlite3
    from ats.data.stores.unstructured import get_platform_unstructured_store
    from ats.data import fundamentals
    repo=get_platform_unstructured_store()
    conn=sqlite3.connect(os.environ["ATS_DATA_DB_PATH"])
    cursor=conn.cursor()
    before=conn.iterdump()
    original=list(before)
    statements={"provider":"fundamentals.fetch_light('COHR')",
        "repository":"repo.document_candidates()",
        "sql_read":"conn.execute('SELECT * FROM data_documents').fetchall()",
        "sql_cursor":"cursor.execute('SELECT * FROM data_documents').fetchall()",
        "opinion_write":"conn.execute(\"CREATE TABLE forbidden_opinion_as_fact(value TEXT)\")",
        "opinion_read":"store.task_projection_envelopes(agent_role='macro_review')"}
    namespace={"__name__":"ats.agents.fundamental.boundary_probe","conn":conn,
               "repo":repo,"fundamentals":fundamentals,"store":isolated,"cursor":cursor}
    exec("def probe():\n    "+statements[operation],namespace)
    with pytest.raises(PermissionError,match="analysis_acceptance_failed"):
        with verify_analysis_access() as calls:
            # Catching a refusal cannot turn the overall run into success.
            try:namespace["probe"]()
            except PermissionError:pass
    assert any(r["status"]=="blocked" for r in calls)
    assert list(conn.iterdump())==original
    record_property("actual_refusal",json.dumps(calls))
    conn.close();repo.close()


def test_candidate_record_cannot_become_published_information(business_inputs, isolated, record_property):
    from ats.data.admission import CandidateDocument, admit
    from ats.data.stores.unstructured import get_platform_unstructured_store
    from ats.agents.information import assemble
    from ats.workflow.runtime_reads import bind_read
    from test_phase_f_safe_reads import scope
    repo=get_platform_unstructured_store()
    candidate=CandidateDocument(expected_entity="COHR",claimed_entity="NVDA",
        target_period="2026Q3",claimed_period="2026Q3",
        expected_semantic="article",claimed_semantic="article", text="Unreviewed rival guidance "*100,
        source="ibkr_news",source_url="fixture:quarantined-rival",external_id="quarantined-rival",
        published_at=(datetime.now(UTC)-timedelta(seconds=1)).isoformat(),completeness="full",min_chars=1)
    outcome=admit(candidate,store=repo)
    assert outcome.validation.status=="quarantined"
    saved=repo.document_candidates(status="quarantined")
    assert any(r["candidate_id"]==candidate.candidate_id for r in saved)
    with bind_read(scope("information",entity=True)), verify_analysis_access() as calls:
        rows=assemble.admitted_events(isolated,"COHR",cutoff=datetime.now(UTC)-timedelta(days=1))
    assert rows and all(r["publication_id"] and r["version_id"] for r in rows)
    assert all(r["source_url"]!=candidate.source_url for r in rows)
    record_property("actual_candidate",json.dumps(saved,default=str))
    record_property("admitted_inputs",json.dumps(rows))
    record_property("actual_access",json.dumps(calls))
    repo.close()


@pytest.mark.parametrize("notional,price_jump", [(1000,False), (50000,False), (1000,True)])
def test_complete_business_execution_and_recovery(business_inputs, isolated, monkeypatch,
                                                 record_property, notional, price_jump, tmp_path):
    from ats.agents.chief.outputs import ChiefOutput
    from ats.decision.repository import DecisionAuditRepository
    from ats.broker.ibkr import IBKRBroker
    seed_performance(isolated)
    monkeypatch.setattr(chief, "_write_chief_report", lambda state: None)
    pf = PortfolioSnapshot(as_of=datetime.now(UTC), net_liquidation=1e6,
                           cash=1e6, daily_pnl=0, gross_exposure=0, positions=[])
    monkeypatch.setattr(IBKRBroker, "get_portfolio", lambda self: pf)
    monkeypatch.setattr("ats.data.execution_prices.session_context",
                        lambda now: (True, now-timedelta(days=1)))
    quotes=[]
    approved=[False]
    def fetch(symbol, **kwargs):
        value=quote(symbol=symbol, bid=102 if price_jump and approved[0] else 100,
                    ask=102.1 if price_jump and approved[0] else 100.1)
        quotes.append(value.model_dump(mode="json"))
        return value
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price", fetch)
    monkeypatch.setattr("ats.agents.chief.decide.run_structured", lambda *a, **k:
        ChiefOutput(summary="Fixture synthesis over actual governed research",
            decisions=[dict(symbol="COHR", action="buy", notional_usd=notional,
                            conviction=.5, rationale="Actual six-role research snapshot")]))
    cards=[]
    class Channel:
        def request_approval(self, request):
            cards.append(request.model_dump(mode="json"))
            approved[0]=True
            return BossApproval(status="rejected" if price_jump and len(cards)>1 else "approved", reviewer="fixture-human",
                                channel="fixture", reviewed_at=datetime.now(UTC))
    route=install_route("simulation", generation=1, environment="paper", account="DU1",
                        actor="fixture", reason="isolated full chain")
    guard.grant_write(route.route_id, route.generation, environment="paper", account="DU1")
    states=[]
    matrices=[]
    from contextlib import ExitStack
    clocks = ExitStack()
    def fix_requirements(plan):
        from ats.workflow.acceptance_matrix import freeze
        from ats.workflow.scoped_routes import RouteIdentity
        from ats.workflow.evaluation_clock import at
        clocks.enter_context(at(datetime.fromisoformat(plan.as_of)))
        matrices.append(freeze(plan,workflow_id="complete-chain",batch_class="trading",
            identity=RouteIdentity("research","chief","v1",{"kind":"portfolio","id":"portfolio",
                "entities":["COHR"],"time_range":{"start":plan.as_of,"end":plan.as_of}}),
            criteria={"risk.contract":[{"path":"verdict","op":"eq","value":"approved"},
                {"path":"orders_by_symbol.COHR.notional_usd","op":"range","value":[0,25000]}],
                "approval.contract":[{"path":"decision","op":"eq","value":"approved"}],
                "attribution.contract":[{"path":"route.account","op":"eq","value":"DU1"},
                    {"path":"route.route_id","op":"eq","value":"simulation"},
                    {"path":"route.generation","op":"eq","value":1}]}))
    with clocks, simulated_execution(store=isolated, account="DU1") as broker:
        def runner(snapshot, manifest):
            # Freeze research evaluation only; runtime quote source/receipt times
            # are real and must never be relabelled with the research clock.
            clocks.close()
            state=ChiefDecisionState(cycle_id="complete-chain", as_of=datetime.now(UTC),
                use_broker=True, use_llm=True, dry_run=False,
                research_snapshot=snapshot.to_payload())
            result=run_decision_graph(state, channel=Channel())
            states.append(result)
            return ChiefDecisionState.model_validate(result).model_dump(mode="json")
        result=decision_run(business_inputs, isolated, runner=runner,before_dispatch=fix_requirements)
        assert result.decision_cycle_entered, (result.as_dict(), states)
        if price_jump:
            assert len(cards)==2 and not broker.accepted
            assert states[0]["approval"].status=="rejected"
            revision_rows=isolated.conn.execute("SELECT * FROM decision_revisions ORDER BY revision_no").fetchall()
            assert len(revision_rows)==2
            assert json.loads(revision_rows[0]["orders_json"])[0]["limit_price"]==100.6
            assert json.loads(revision_rows[1]["orders_json"])[0]["limit_price"]>102
            assert revision_rows[0]["decision_hash"]!=revision_rows[1]["decision_hash"]
            record_property("price_refusal",json.dumps(ChiefDecisionState.model_validate(states[0]).model_dump(mode="json")))
            return
        assert len(broker.accepted)==1, [r.error for s in states for r in s.get("order_results",[])]
        order=broker.accepted[0]
        assert order.qty>0 and order.decision_hash and order.approval_id
        audit=DecisionAuditRepository(isolated)
        cycle=audit.get_cycle("complete-chain")
        assert cycle["status"]=="executed", dict(cycle)
        assert len(json.loads(cycle["research_snapshot"])["items"])==6
        assert len(cards)==1
        assert audit.latest_revision("complete-chain")["revision_no"]==(2 if notional==50000 else 1)
        from ats.trader.execute import place_orders
        from ats.execution.route_registry import submission_receipts
        receipts_before_retry = submission_receipts()
        original=states[0]["approved_decisions"]
        retried,_=place_orders([(d,d.qty) for d in original],"complete-chain",
            revision_no=order.revision_no,authorization=states[0]["authorization"])
        assert len(broker.accepted)==1 and retried[0].order_id==order.order_id
        receipts_after_retry = submission_receipts()
        first=4
        broker.simulate_fill(order.order_id, shares=first, price=100.1)
        class Readback:
            def get_portfolio(self):
                from ats.schemas.portfolio import Position
                qty=sum(f["shares"] for f in broker.fills)
                cost=sum(f["shares"]*f["price"] for f in broker.fills)
                pnl=qty*103-cost
                return pf.model_copy(update={"as_of":datetime.now(UTC),"cash":1e6-cost,
                    "net_liquidation":1e6+pnl,"daily_pnl":pnl,"gross_exposure":qty*103,
                    "positions":[Position(symbol="COHR",qty=qty,avg_cost=cost/qty,market_price=103,
                        market_value=qty*103,unrealized_pnl=pnl,beta=1)]})
            def get_fills(self):return broker.get_fills()
            def completed_orders(self):return []
        reader=Readback()
        day=datetime.now(UTC).date().isoformat()
        partial_cutoff=datetime.now(UTC).isoformat()
        late_cutoff=(order.submitted_at+timedelta(days=1,seconds=1)).isoformat()
        partial=clerk_run(store=isolated, broker=reader, as_of=partial_cutoff,
                          window_start=day, window_end=day)
        assert partial["status"]=="completed", partial
        row=dict(isolated.conn.execute("SELECT * FROM trades").fetchone())
        assert row["status"]=="partial" and row["filled_qty"]==first
        from ats.execution.rebuild import rebuild_performance, rebuild_attribution
        period=order.submitted_at.strftime("%Y-%m")
        partial_model=rebuild_performance(isolated,period=period)
        assert partial_model["status"]=="rebuilt"
        from ats.execution.authorization_lifecycle import AuthorizationLifecycle
        from ats.execution.order_disposition import disposition_report
        disposition=disposition_report(AuthorizationLifecycle(cycle_status=cycle["status"],order_rows=[row]))
        assert disposition["blocks_switch"]
        replay=clerk_run(store=isolated, broker=reader, as_of=partial_cutoff,
                         window_start=day, window_end=day)
        assert replay.get("reused"), replay
        broker.simulate_fill(order.order_id, shares=order.qty-first, price=100.2,
                             at=order.submitted_at+timedelta(days=1))
        complete=clerk_run(store=isolated, broker=reader, as_of=late_cutoff,
                           window_start=day, window_end=(order.submitted_at+timedelta(days=1)).date().isoformat())
        assert complete["status"]=="completed", complete
        row=dict(isolated.conn.execute("SELECT * FROM trades").fetchone())
        assert row["status"]=="filled" and row["filled_qty"]==order.qty
        assert row["avg_fill_price"]==pytest.approx((first*100.1+(order.qty-first)*100.2)/order.qty)
        assert isolated.conn.execute("SELECT count(*) FROM fills").fetchone()[0]==2
        ledger_fills=[dict(r) for r in isolated.conn.execute("SELECT * FROM fills")]
        assert all(f["origin"]=="system" and f["cycle_id"]==order.cycle_id
                   and f["decision_hash"]==order.decision_hash and f["approval_id"]==order.approval_id for f in ledger_fills)
        assert complete["steps"]["reconcile"]["late_backfilled"]==1
        assert complete["steps"]["performance"]["recorded"]
        full_model=rebuild_performance(isolated,period=period)
        assert full_model["status"]=="rebuilt" and full_model["source_facts_hash"]!=partial_model["source_facts_hash"]
        assert full_model["payload"]["system_trades"]==1
        assert rebuild_performance(isolated,period=period)["status"]=="skipped"
        assert rebuild_attribution(isolated,period=period)["status"]=="rebuilt"
        # 3.3: consume actual business records; no legacy oracle or caller passed flag.
        from ats.workflow.acceptance_assertions import audit_risk, audit_approval, audit_attribution, check_criteria
        assertion_evidence = {"store":isolated,"cycle_id":"complete-chain",
            "risk_review":states[0]["risk_review"],"receipts":receipts_before_retry,
            "retry_receipts":receipts_after_retry,"dispatch":result.as_dict()}
        from ats.workflow.acceptance_assertions import evaluate
        from ats.workflow.schedule_runtime import snapshot as schedule_snapshot
        assertion_evidence["schedule"] = schedule_snapshot()
        risk_actual,risk_refs = audit_risk(assertion_evidence)
        approval_actual,approval_refs = audit_approval(assertion_evidence)
        attribution_actual,attribution_refs = audit_attribution(assertion_evidence)
        assertion_run = evaluate(matrices[0],assertion_evidence)
        assert all(r.status == "passed" for r in assertion_run.results if r.dimension != "inputs"),assertion_run.as_dict()
        assert assertion_run.results[0].status == "untested"
        check_criteria(risk_actual,[{"path":"verdict","op":"eq","value":"approved"},
            {"path":"orders_by_symbol.COHR.notional_usd","op":"range","value":[0,25000]}])
        check_criteria(approval_actual,[{"path":"decision","op":"eq","value":"approved"}])
        check_criteria(attribution_actual,[{"path":"route.account","op":"eq","value":"DU1"}])
        damaged = dict(assertion_evidence)
        import copy
        damaged["risk_review"] = copy.deepcopy(risk_actual)
        damaged["risk_review"]["order_verdicts"] = []
        with pytest.raises(ValueError,match="coverage"):
            audit_risk(damaged)
        damaged = dict(assertion_evidence)
        damaged["receipts"] = copy.deepcopy(receipts_before_retry)
        damaged["receipts"][0]["payload_hash"] = "corrupted-payload"
        damaged["retry_receipts"] = copy.deepcopy(damaged["receipts"])
        with pytest.raises(ValueError,match="instruction differs"):
            audit_attribution(damaged)
        record_property("actual_boundary_assertions",json.dumps({"risk":risk_actual,"risk_refs":risk_refs,
            "approval":approval_actual,"approval_refs":approval_refs,
            "attribution":attribution_actual,"attribution_refs":attribution_refs}))
        record_property("actual_trading_requirement_assertions",json.dumps(assertion_run.as_dict()))
        evidence=tmp_path/"broker-reports.json"
        evidence.write_text(json.dumps({"portfolio":reader.get_portfolio().model_dump(mode="json"),"fills":broker.fills}))
        import os, subprocess, sys
        program='''import json,sys
from ats.memory import get_store
from ats.execution.clerk import clerk_run
from ats.schemas.portfolio import PortfolioSnapshot
p=json.load(open(sys.argv[1]))
class Readback:
 def get_portfolio(self):return PortfolioSnapshot.model_validate(p["portfolio"])
 def get_fills(self):return p["fills"]
 def completed_orders(self):return []
s=get_store(); before=s.conn.execute("SELECT count(*) FROM fills").fetchone()[0]
r=clerk_run(store=s,broker=Readback(),as_of=sys.argv[4],window_start=sys.argv[2],window_end=sys.argv[3])
assert r["status"]=="completed" and r["steps"]["performance"]["recorded"],r
assert s.conn.execute("SELECT count(*) FROM fills").fetchone()[0]==before==2
print(json.dumps(r))
'''
        reopened=subprocess.run([sys.executable,"-c",program,str(evidence),day,
            (order.submitted_at+timedelta(days=1)).date().isoformat(),
            (order.submitted_at+timedelta(days=1,seconds=2)).isoformat()],env=os.environ.copy(),
            capture_output=True,text=True)
        assert reopened.returncode==0,reopened.stderr
        current = audit.latest_revision("complete-chain")
        assert current["decision_hash"] == order.decision_hash
        assert current["revision_no"] == order.revision_no
        audit_approval(assertion_evidence)
        after_reopen = evaluate(matrices[0],assertion_evidence)
        assert all(r.status == "passed" for r in after_reopen.results if r.dimension != "inputs")
        record_property("repaired_assertions_after_reopen",json.dumps(after_reopen.as_dict()))
        record_property("reopened_clerk",reopened.stdout)
        record_property("chain", json.dumps({"dispatch":result.as_dict(),"cycle":dict(cycle),
            "cards":cards,"quotes":quotes,"accepted":order.model_dump(mode="json"),
            "fills":broker.fills,"partial":partial,"complete":complete,"trade":row,
            "partial_model":partial_model,"full_model":full_model,"partial_disposition":disposition,
            "audit_chain":audit.read_chain("complete-chain"),"ledger_fills":ledger_fills}, default=str))
