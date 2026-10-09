"""Real consumption points; candidate fixtures never authorize production."""
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
from ats.workflow import cutover as co
from ats.workflow import cutover_wiring as cw
from ats.workflow import runtime_reads as rr
from ats.workflow.assurance_surface import fingerprint, load_surface
from ats.workflow.consumer_reads import (
    InternalFallback,
    consume_read,
    implementation_hash,
    read_internal,
    read_projection,
    revoke_fallback,
    trace_reads,
)
from ats.workflow.cutover_routing import FallbackUnsafe, RouteUnavailable
from ats.workflow.intake_verification import isolated_verification
from ats.workflow.scoped_routes import transition_route


@pytest.fixture
def isolated(tmp_path):
    guard.reset_for_tests()
    with isolated_verification("safe-reads", root=tmp_path / "iso"):
        cw.bootstrap_wired()
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


def activate(identity, tmp_path, monkeypatch, *, reader=read_internal, changes=None):
    surface = load_surface()
    proof = {"identity": identity.as_row(), "legacy_identifier": "store.direct_trade_reads",
             "implementation_sha256": implementation_hash(reader),
             "dependency_hashes": fingerprint([surface.manifest, *surface.paths_for(identity.consumer_id)]),
             "valid_until": (datetime.now(UTC)+timedelta(hours=1)).isoformat()}
    proof.update(changes or {})
    reference = tmp_path / "scope-proof.json"
    reference.write_text(json.dumps(proof))
    evidence = {"identity": identity.as_row(), "valid": True, "reference": str(reference),
                "sha256": hashlib.sha256(reference.read_bytes()).hexdigest()}
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", lambda *a, **k: None)
    transition_route(co.PROJECTION_READ, identity, "target", actor="fixture", reason="candidate",
        expected_generation=0, qualification=lambda i: {"identity": i.as_row(), "status": "eligible"},
        report=lambda i: {"identity": i.as_row(), "valid": True, "reference": "fixture-report"},
        fallback=lambda i: evidence,mode="isolated")
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "ineligible",
                                                                             "reasons": ["revoked"]})
    return reference


def seed_performance(store):
    record = PerformanceRecord(cycle_id="fixture-state", as_of=datetime.now(UTC),
        net_liquidation=1_000_000, daily_pnl=2, cumulative_pnl=100)
    store.save_performance(record)
    store.set_meta("last_reconcile_at", record.as_of.isoformat())
    return record


def test_actual_chief_read_falls_back_using_once_checked_state(isolated, tmp_path, monkeypatch):
    identity = scope()
    seed_performance(isolated)
    activate(identity, tmp_path, monkeypatch)
    from ats.execution import state_api
    original, calls = state_api.get_internal_state, []
    def counted(*a, **k):
        calls.append(1)
        return original(*a, **k)
    monkeypatch.setattr(state_api, "get_internal_state", counted)
    with trace_reads() as rows:
        text = assemble._track_record_block(read_scope=identity.scope,
                                           read_fallback=InternalFallback(isolated))
    assert "1,000,000" in text and "内部状态" in text
    assert len(calls) == 1
    assert rows[0]["fallback"] is True and rows[0]["refs"]
    assert rows[0]["identity"] == identity.as_row() and rr.current_read_context() is None
    assert co.read_boundary(co.LIVE_TRADER).route == "disabled"


@pytest.mark.parametrize("failure,code", [
    ("missing", "fallback_proof_invalid"), ("expired", "fallback_proof_invalid"),
    ("scope", "fallback_proof_invalid"), ("digest", "fallback_proof_invalid"),
    ("dependency", "fallback_proof_invalid"), ("implementation", "fallback_proof_invalid"),
    ("retired", "fallback_target_retired"), ("unreadable", "fallback_target_unavailable")])
def test_actual_read_refuses_each_unsafe_fallback(isolated, tmp_path, monkeypatch, failure, code):
    identity = scope()
    changes = {"expired": {"valid_until": (datetime.now(UTC)-timedelta(seconds=1)).isoformat()},
               "scope": {"identity": scope("risk").as_row()},
               "dependency": {"dependency_hashes": {}},
               "implementation": {"implementation_sha256": "old"}}.get(failure)
    proof = activate(identity, tmp_path, monkeypatch, changes=changes)
    if failure == "missing":
        proof.unlink()
    if failure == "digest":
        proof.write_text(proof.read_text() + " ")
    if failure == "retired":
        monkeypatch.setattr("ats.workflow.legacy_retirement.load_registry", lambda: SimpleNamespace(
            tombstone=lambda identifier: SimpleNamespace(status="retired")))
    if failure == "unreadable":
        isolated.conn.execute("DROP TABLE performance")
    with trace_reads() as rows, pytest.raises(FallbackUnsafe) as exc:
        assemble._track_record_block(read_scope=identity.scope, read_fallback=InternalFallback(isolated))
    assert exc.value.reason_code == code and rows[-1]["status"] in {"blocked", "unavailable"}
    assert isolated.conn.execute("SELECT COUNT(*) FROM boss_approvals").fetchone()[0] == 0
    assert rr.current_read_context() is None


def test_failed_scope_does_not_stop_unrelated_legacy_reader(isolated, tmp_path, monkeypatch):
    blocked, unaffected = scope(), scope("risk")
    activate(blocked, tmp_path, monkeypatch, changes={"dependency_hashes": {}})
    with pytest.raises(FallbackUnsafe):
        read_internal(isolated, consumer="chief", business_scope=blocked)
    assert read_internal(isolated, consumer="risk", business_scope=unaffected).owner == "ats.execution.state_api"


def test_revoked_proof_is_append_only_and_scope_specific(isolated, tmp_path, monkeypatch):
    import sqlite3

    identity = scope()
    seed_performance(isolated)
    proof = activate(identity, tmp_path, monkeypatch)
    original = proof.read_bytes()
    revoke_fallback(scope("risk"), proof, actor="fixture", reason="another consumer")
    assert read_internal(isolated, consumer="chief", business_scope=identity).portfolio
    revoke_fallback(identity, proof, actor="fixture", reason="withdraw")
    with pytest.raises(FallbackUnsafe) as exc:
        read_internal(isolated, consumer="chief", business_scope=identity)
    assert exc.value.reason_code == "fallback_proof_revoked" and proof.read_bytes() == original
    from ats.workflow.scoped_routes import resolve_route
    assert resolve_route(co.PROJECTION_READ, identity)["route"] == "target"
    with sqlite3.connect(co.default_cutover_db_path()) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM fallback_revocations")


def test_emergency_disable_is_not_a_fallback_opportunity(isolated, tmp_path, monkeypatch):
    identity = scope()
    activate(identity, tmp_path, monkeypatch)
    co.set_route(co.PROJECTION_READ, "disabled", actor="test", reason="stop")
    with pytest.raises(RouteUnavailable, match="disabled"):
        assemble._track_record_block(read_scope=identity.scope, read_fallback=InternalFallback(isolated))


def test_actual_risk_node_blocks_before_account_or_dependent_execution(isolated, tmp_path, monkeypatch):
    requested = {**scope("risk", entity=True).scope, "kind": "decision", "id": "blocked-cycle"}
    identity = rr.business_identity("risk", kind="decision", scope_id="blocked-cycle",
                                    entities=["COHR"], explicit=requested)
    activate(identity, tmp_path, monkeypatch)
    def forbidden():
        pytest.fail("unqualified Risk reached account provider")
    monkeypatch.setattr("ats.trader.portfolio.snapshot", forbidden)
    state = ChiefDecisionState(cycle_id="blocked-cycle", as_of=datetime.now(UTC), decide=False,
        decisions=[TradeDecision(symbol="COHR", action="buy", qty=1)])
    with pytest.raises(RouteUnavailable):
        chief.risk_gate(state, read_scope=identity.scope)
    assert isolated.conn.execute("SELECT COUNT(*) FROM decision_cycles").fetchone()[0] == 0


def test_actual_clerk_stops_next_step_after_read_revocation(isolated, tmp_path, monkeypatch):
    from ats.execution import clerk
    identity = scope("clerk")
    activate(identity, tmp_path, monkeypatch)
    active, calls = [True], []
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {
        "status": "eligible" if active[0] else "ineligible"})
    def first(*a, **k):
        calls.append("first")
        active[0] = False
        return {}
    monkeypatch.setattr(clerk, "_STEPS", {"first": first, "next": lambda *a, **k: calls.append("next")})
    with pytest.raises(RouteUnavailable):
        clerk_run(store=isolated, broker=object(), steps=("first", "next"), read_scope=identity.scope)
    assert calls == ["first"]
    assert isolated.conn.execute("SELECT status FROM clerk_runs").fetchone()[0] == "running"


def test_actual_read_rechecks_control_change_during_probe(isolated, tmp_path, monkeypatch):
    identity = scope()
    def reader():
        co.set_route(co.PROJECTION_READ, "disabled", actor="test", reason="race")
        return {"value": 1}
    activate(identity, tmp_path, monkeypatch, reader=reader)
    with pytest.raises(RouteUnavailable, match="changed during"):
        consume_read(identity, target_reader=reader, legacy_reader=reader,
                     legacy_identifier="store.direct_trade_reads", usable=lambda v: True, refs=lambda v: ["state"])


def test_actual_routine_consumes_published_brief_and_preserves_refs(isolated, record_property):
    now = datetime.now(UTC)
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
    record_property("read_trace", json.dumps(rows))
    record_property("projection", json.dumps(output))
    with pytest.raises(PermissionError, match="undeclared"):
        read_projection(isolated, consumer="fundamental", role="macro_review",
                        scope=ProjectionScope(kind="portfolio"))


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
    for consumer, api in [("trader", "ats.data.consumer_api.read_input"),
                          ("clerk", "ats.data.runtime.clerk_reads.get_fills"),
                          ("clerk", "ats.decision.repository.DecisionAuditRepository.read_chain")]:
        assert any(row["consumer"] == consumer and row["api"] == api and row["refs"] for row in rows)
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
    assert "accepted-company-v1" in result["fundamentals_text"] and result["consensus"]["eps"] == 3
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


def test_actual_risk_entry_reads_runtime_and_internal_state(isolated, monkeypatch, record_property):
    from ats.agents.risk_officer.review import run
    from ats.schemas.portfolio import PortfolioSnapshot

    seed_performance(isolated)
    pf = PortfolioSnapshot(as_of=datetime.now(UTC), net_liquidation=1_000_000, cash=1_000_000,
        daily_pnl=0, gross_exposure=0, positions=[])
    monkeypatch.setattr("ats.trader.portfolio.IBKRBroker", lambda **k: SimpleNamespace(get_portfolio=lambda: pf))
    monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda pf: None)
    monkeypatch.setattr("ats.risk.assess.enrich_options", lambda pf: None)
    with trace_reads() as rows:
        result = run(use_llm=False)
    assert result.review and {row["api"] for row in rows} >= {
        "ats.data.runtime.broker.portfolio_snapshot", "ats.config.get_config",
        "ats.execution.state_api.performance_history"}
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
    def forbidden(*a, **k):
        pytest.fail("candidate Chief read legacy research/provider")
    for name in ("_pead_block", "_sector_block", "_macro_block", "_portfolio_block"):
        monkeypatch.setattr(assemble, name, forbidden)
    with rr.bind_read(identity), trace_reads() as rows:
        result = assemble.build()
    assert result.net_liquidation == 1_000_000 and brief.projection_id[:18] in result.as_context()
    assert any(brief.projection_id in row["refs"] and row.get("content_hash") for row in rows)
    assert any(row["refs"] and row["api"] == "store.direct_trade_reads" for row in rows)
    record_property("read_trace", json.dumps(rows))
    record_property("context", result.as_context())


def test_isolated_entry_persists_actual_read_refs(isolated, tmp_path):
    from ats.workflow.isolated_entry import run_isolated_entry
    result = run_isolated_entry(run_id="trace-entry", entry=assemble._track_record_block,
                                root=tmp_path / "entry")
    import sqlite3
    with sqlite3.connect(result.record_path) as conn:
        body = json.loads(conn.execute("SELECT body FROM isolated_business_entries").fetchone()[0])
    assert body["read_trace"] and body["tradable"] is False
    assert body["read_trace"][0]["consumer"] == "chief"
