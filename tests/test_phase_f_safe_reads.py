"""Real consumption points (the read-route fallback cases went with the route tables, switch-trade r5 9.3)."""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_phase_f_trader_a import execution, price_clock, ready_state  # noqa: F401,F811

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.agents.chief import assemble
from ats.agents.fundamental import entry
from ats.data.products import DataProducts
from ats.decision.repository import DecisionAuditRepository
from ats.execution import broker_write_guard as guard
from ats.execution.clerk import clerk_run
from ats.graph import chief, pead
from ats.graph.chief_state import ChiefDecisionState
from ats.graph.pead_state import PeadState
from ats.memory import get_store
from ats.schemas.decision import TradeDecision
from ats.schemas.memory import PerformanceRecord
from ats.workflow import runtime_reads as rr
from ats.workflow.isolation import isolated_run
from ats.workflow.read_scope import ReadUnavailable, read_projection, trace_reads


@pytest.fixture
def isolated(tmp_path):
    guard.reset_for_tests()
    # isolated_verification (intake_verification) was isolated_run plus a production side-effect check; it was
    # retired in r5 8.7 and the long-term isolation entry is used directly.
    with isolated_run("safe-reads", root=tmp_path / "iso"):
        yield get_store()
    guard.reset_for_tests()


def scope(consumer="chief", entity=False):
    now = datetime.now(UTC)
    entities = ["COHR"] if entity else rr.configured_entities("portfolio", "portfolio")
    return rr.business_identity(consumer, kind="entity" if entity else "portfolio",
        scope_id="COHR" if entity else "portfolio", entities=entities,
        explicit={"kind": "entity" if entity else "portfolio", "id": "COHR" if entity else "portfolio",
                  "entities": entities, "time_range": {"start": (now-timedelta(minutes=5)).isoformat(),
                                                         "end": (now+timedelta(minutes=5)).isoformat()}})


def seed_performance(store):
    record = PerformanceRecord(cycle_id="fixture-state", as_of=datetime.now(UTC),
        net_liquidation=1_000_000, daily_pnl=2, cumulative_pnl=100)
    store.save_performance(record)
    store.set_meta("last_reconcile_at", record.as_of.isoformat())
    return record


def test_actual_routine_consumes_published_brief_and_preserves_refs(isolated, record_property):
    now = datetime.now(UTC)
    from ats.schemas.pead import PeadDossier, ExpectationSet, Expectation
    baseline_at = now - timedelta(minutes=30)
    isolated.save_dossier(PeadDossier(symbol="COHR", fiscal_label="Q3 FY2026", phase="prep",
        updated_at=baseline_at, expectation_set=ExpectationSet(symbol="COHR", fiscal_label="Q3 FY2026",
        as_of=baseline_at, narrative="Previous capacity expectation before the new factory information.",
        expectations=[Expectation(dim_key="capacity", metric="Production lines", conservative="8",
            neutral="10", optimistic="12", source="isolated previous baseline fixture")])))
    brief = build_envelope(role="information_brief", scope=ProjectionScope(kind="entity", id="COHR"),
        as_of=(now-timedelta(seconds=1)).isoformat(), input_refs=["published-document@v1"],
        payload={"entity": "COHR", "headline": "厂房", "summary": "事实更新", "relevance": "high",
                 "sources": ["published-document@v1"], "fact_changes": ["新厂新增 10 条产线"]})
    isolated.save_task_projection_envelope(brief)
    with trace_reads() as rows:
        result = entry.run_fundamental_pass(entry.routine_request("COHR", trigger="information_brief_update",
                                                                 use_llm=False))
    assert result["published"] and rows[0]["refs"] == [brief.projection_id]
    output = isolated.get_task_projection(result["published"][0])
    assert brief.projection_id in output["input_refs"] and output["content_hash"]
    assert any(ref.startswith("pead-baseline:COHR:Q3 FY2026@") for ref in output["input_refs"])
    record_property("read_trace", json.dumps(rows))
    record_property("projection", json.dumps(output))
    with pytest.raises(PermissionError, match="undeclared"):
        read_projection(isolated, consumer="fundamental", role="macro_review",
                        scope=ProjectionScope(kind="portfolio"))


@pytest.mark.governed_decision_reads
def test_actual_trader_and_clerk_consume_auth_quotes_reports_and_audit(execution, record_property):  # noqa: F811
    state = ready_state(execution, cycle="consumption-chain")
    # No fixture inserts submitted/filled orders: actual Trader/FakeBroker do so.
    class Readback:
        def get_portfolio(self): return execution.pf
        def get_fills(self): return execution.broker.get_fills()
        def completed_orders(self): return []
    with trace_reads() as rows:
        state = state.model_copy(update=chief.trader(state))
        assert state.order_results[0].status == "submitted"
        chief.persist(state)
        execution.broker.simulate_fill(state.order_results[0].order_id,
                                       shares=state.order_results[0].qty, price=100.1)
        result = clerk_run(store=execution.store, broker=Readback())
    assert result["status"] == "completed", result["errors"]
    # Since task 6.4 Clerk's broker and decision-chain reads are governed reads too (BROKER_STATE steps,
    # DECISION_APPROVAL_CONTEXT).
    for consumer, product in [("trader", "APPROVED_EXECUTION_AUTHORIZATION"), ("clerk", "BROKER_STATE"),
                              ("clerk", "DECISION_APPROVAL_CONTEXT")]:
        assert any(row["consumer"] == consumer and row["api"] == "ats.data.consumer_api.read_input"
                   and row.get("product") == product and row["refs"] for row in rows), (consumer, product)
    assert {row.get("product") for row in rows if row["consumer"] == "trader"} >= {
        "MARKET_DATA", "APPROVED_EXECUTION_AUTHORIZATION"}
    assert execution.store.conn.execute("SELECT COUNT(*) FROM performance").fetchone()[0] > 0
    record_property("read_trace", json.dumps(rows))
    record_property("decision_chain", json.dumps(DecisionAuditRepository(
        execution.store).read_chain(state.cycle_id), default=str))


@pytest.fixture
def company_products(monkeypatch):
    now = datetime.now(UTC)
    financial = {"observation_id": "accepted-company-v1", "known_at": (now-timedelta(seconds=1)).isoformat(),
                 "metric_id": "financial.revenue", "value": 100, "period": "2026-Q2"}
    class Repository:
        def observations(self, **filters):
            assert filters["accepted_only"] and filters["as_of"] <= datetime.now(UTC)
            return [financial]
        def close(self): pass
    products = DataProducts(structured_repository=Repository(), unstructured_repository=Repository())
    monkeypatch.setattr(products, "consensus_snapshot", lambda **k: {"rows": [{**financial,
        "observation_id": "consensus-v1", "metric_id": "consensus.eps.mean", "value": 3}]})
    monkeypatch.setattr(products, "neutral_evidence", lambda **k: {"rows": [{"fact_id": "chain-fact-v1",
        "observed_at": financial["known_at"]}], "rejected": []})
    monkeypatch.setattr("ats.data.products.get_platform_data_products", lambda **k: products)
    def forbidden(*a, **k):
        pytest.fail("provider bypass")
    monkeypatch.setattr("ats.data.fundamentals.fetch", forbidden)
    monkeypatch.setattr("ats.data.consensus.fetch", forbidden)
    return now


def test_actual_fundamental_fetch_uses_governed_products_without_provider(isolated, company_products):
    now = company_products
    with rr.bind_read(scope("fundamental", entity=True)), trace_reads() as rows:
        result = pead.prep_fetch(PeadState(symbol="COHR", as_of=now, live_data=True))
    # The prompt now carries a compact per-metric summary (2026-10-16: the raw snapshot was ~150K tokens per call);
    # lineage to the exact observation is kept in `input_refs` below, not in the prompt text.
    assert "财务快照摘要" in result["fundamentals_text"] and "financial.revenue" in result["fundamentals_text"]
    assert result["consensus"]["eps"] == 3
    assert set(result["input_refs"]) == {"accepted-company-v1", "consensus-v1", "chain-fact-v1"}
    assert len(rows) == 3 and all(row["consumer"] == "fundamental" for row in rows)


def test_actual_event_graph_keeps_data_document_refs(isolated, company_products, monkeypatch, record_property):
    from ats.data.products.unstructured import EarningsDocument, EarningsDocumentPackage

    now = company_products
    doc = EarningsDocument(role="earnings_release", document_id="release", version_id="v1",
        source="synthetic-admitted", source_url="fixture:release", published_at=now.isoformat(),
        title="Q3 FY2026 results", text="Company results revenue 100 and earnings 3.")
    monkeypatch.setattr("ats.data.products.unstructured.platform_earnings_document_package",
        lambda **kwargs: EarningsDocumentPackage(entity=kwargs["entity"], period=kwargs["period"],
                                                  documents=(doc,), repository="synthetic-admission"))
    monkeypatch.setattr("ats.agents.pead.report.write_report", lambda dossier: None)
    def forbidden(*args, **kwargs):
        pytest.fail("transcript provider bypass")
    monkeypatch.setattr("ats.data.transcript.fetch", forbidden)
    monkeypatch.setattr("ats.data.runup.compute", forbidden)
    with trace_reads() as rows:
        result = entry.run_fundamental_pass(entry.event_request("COHR", trigger="earnings_release",
            fiscal_label="Q3 FY2026", use_llm=False, extra={"live_data": True}))
    assert result["fiscal_label_resolved"] == "Q3 FY2026"
    published = isolated.task_projection_envelopes(agent_role="fundamental_event_review",
        scope_kind="entity", scope_id="COHR", limit=1)[0]
    assert {"accepted-company-v1", "consensus-v1", "chain-fact-v1", "release@v1"} <= set(published["input_refs"])
    assert published["content_hash"] and any("release@v1" in row["refs"] for row in rows)
    record_property("read_trace", json.dumps(rows))
    record_property("projection", json.dumps(published))


@pytest.mark.governed_decision_reads
def test_actual_risk_entry_reads_runtime_and_internal_state(isolated, monkeypatch, record_property):
    from ats.agents.risk_officer.review import run
    from ats.schemas.portfolio import PortfolioSnapshot

    seed_performance(isolated)
    pf = PortfolioSnapshot(as_of=datetime.now(UTC), net_liquidation=1_000_000, cash=1_000_000,
        daily_pnl=0, gross_exposure=0, positions=[])
    monkeypatch.setattr("ats.broker.account.IBKRBroker", lambda **k: SimpleNamespace(get_portfolio=lambda: pf))
    monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda pf: None)
    monkeypatch.setattr("ats.risk.assess.enrich_options", lambda pf: None)
    with trace_reads() as rows:
        result = run(use_llm=False)
    # Since task 6.4 every one of these is a governed read: the book, the ruleset and the NAV history.
    assert result.review and {row["api"] for row in rows} == {"ats.data.consumer_api.read_input"}
    assert {row.get("product") for row in rows} >= {"PORTFOLIO_DATA", "RISK_RULES", "HISTORY_DATA"}
    assert all(row["refs"] for row in rows)
    record_property("read_trace", json.dumps(rows))
    record_property("risk_review", result.review.model_dump_json())


def test_chief_candidate_context_consumes_projection_and_state(isolated, monkeypatch, record_property):
    identity = scope()
    symbol = identity.scope["entities"][0]
    seed_performance(isolated)
    brief = build_envelope(role="information_brief", scope=ProjectionScope(kind="entity", id=symbol),
        as_of=(datetime.now(UTC)-timedelta(seconds=1)).isoformat(), input_refs=["published:fact"],
        payload={"entity": symbol, "headline": "已发布事实", "summary": "公司事实", "relevance": "high",
                 "sources": ["published:fact"]})
    isolated.save_task_projection_envelope(brief)
    from test_phase_f_research_gate import small_plan, seed_research
    plan = small_plan()
    plan["information_brief"]["scopes"] = [brief.scope]
    seed_research(isolated, {k: v for k, v in plan.items() if k != "information_brief"})
    monkeypatch.setattr(assemble, "projection_query_plan", lambda: plan)
    from ats.data.consumer_api import read_input
    from ats.schemas.portfolio import PortfolioSnapshot

    book = PortfolioSnapshot(as_of=datetime.now(UTC) - timedelta(seconds=1),
                             net_liquidation=1_000_000.0, positions=[])
    with rr.bind_read(identity), trace_reads() as rows:
        account = read_input("chief", "PORTFOLIO_DATA", scope={"kind": "portfolio"},
                             broker=SimpleNamespace(get_portfolio=lambda: book))
        result = assemble.build(account=account)
    # the account is the live book; the brief is read into the snapshot but not shown (D3-2)
    assert result.net_liquidation == 1_000_000 and brief.projection_id not in result.as_context()
    assert any(brief.projection_id in row["refs"] and row.get("content_hash") for row in rows)
    assert any(row["refs"] and row["api"] == "ats.data.consumer_api.read_input" for row in rows)
    record_property("read_trace", json.dumps(rows))
    record_property("context", result.as_context())
