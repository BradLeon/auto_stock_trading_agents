"""Plan A business paths; synthetic quotes/accounts, no real broker writes."""
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import yaml

from ats.config import REPO_ROOT
from ats.data import execution_prices as prices
from ats.data.consumer_api import read_input
from ats.data.contract_validation import validate_target_contract
from ats.data.runtime.execution_prices import ExecutionPrice, session_context
from ats.decision.repository import DecisionAuditRepository
from ats.execution import broker_write_guard as guard
from ats.execution.authorization import bind_to_active_route, build_authorization
from ats.execution.route_registry import install_route
from ats.execution.simulation import simulated_execution
from ats.graph import chief
from ats.graph.chief_state import ChiefDecisionState
from ats.memory import bound_store, get_store
from ats.schemas.decision import BossApproval, TradeDecision
from ats.schemas.portfolio import PortfolioSnapshot
from ats.schemas.risk import RiskReview
from ats.trader import execute as trader
from ats.workflow.isolation import isolated_run


def quote(**changes):
    now = datetime.now(UTC)
    values = {"symbol": "AAPL", "currency": "USD", "source": "synthetic:bid_ask",
              "source_as_of": now, "queried_at": now, "price_kind": "bid_ask",
              "bid": 100., "ask": 100.1, "session": "regular", "market_data_mode": "live",
              "adjusted": False, "min_size": 1, "size_increment": 1, "min_tick": .01}
    return ExecutionPrice(**{**values, **changes})


@pytest.fixture
def price_clock(monkeypatch):
    # Actual exchange-calendar tests below independently cover holidays/early close.
    monkeypatch.setattr(prices, "session_context", lambda now: (True, now - timedelta(days=1)))


@pytest.fixture
def execution(tmp_path, monkeypatch, price_clock):
    guard.reset_for_tests()
    with isolated_run("trader-a", root=tmp_path / "isolated"):
        store = get_store()
        route = install_route("simulation", generation=1, environment="paper", account="DU1",
                              actor="test", reason="synthetic")
        guard.grant_write(route.route_id, route.generation, environment="paper", account="DU1")
        provider_calls = []
        def provider(symbol, **kwargs):
            provider_calls.append((symbol, kwargs))
            return quote(symbol=symbol)
        monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price", provider)
        pf = PortfolioSnapshot(as_of=datetime.now(UTC), net_liquidation=1_000_000,
                               cash=1_000_000, daily_pnl=0, gross_exposure=0, positions=[])
        monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: pf)
        monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda pf: None)
        monkeypatch.setattr("ats.risk.assess.enrich_options", lambda pf: None)
        monkeypatch.setattr("ats.risk.assess._prices", lambda symbols: {})
        monkeypatch.setattr("ats.risk.assess.assess", lambda pf, **kw: RiskReview(
            as_of=pf.as_of, risk_state="normal"))
        class Channel:
            def request_approval(self, request):
                return BossApproval(status="approved", reviewer="test-human",
                                    channel="test", reviewed_at=datetime.now(UTC))
        monkeypatch.setattr("ats.channel.get_channel", lambda *a, **k: Channel())
        with bound_store(store), simulated_execution(store=store, account="DU1") as broker:
            yield SimpleNamespace(store=store, broker=broker, pf=pf, calls=provider_calls)
    guard.reset_for_tests()


def ready_state(execution, orders=None, cycle="test-a"):
    orders = orders or [TradeDecision(symbol="AAPL", action="buy", notional_usd=1000)]
    state = ChiefDecisionState(cycle_id=cycle, as_of=datetime.now(UTC), decide=False,
                               use_llm=False, dry_run=False, decisions=orders)
    state = state.model_copy(update=chief.risk_gate(state))
    assert state.risk_review.verdict == "approved", state.risk_notes
    state = state.model_copy(update=chief.persist_decision(state))
    approval = BossApproval(status="approved", reviewer="test-human", channel="test",
                            reviewed_at=datetime.now(UTC))
    chief._record_approval(state, approval)
    return state.model_copy(update={"approval": approval})


def test_manifest_v2_permissions_evidence_and_nontrader_roles():
    assert validate_target_contract()["valid"]
    raw = yaml.safe_load((REPO_ROOT / "config/data/target_dataflow_coverage.yaml").read_text())
    rows = {row["id"]: row for row in raw["consumers"]}
    for role in ("risk", "trader"):
        assert rows[role]["contract_version"] == "target-dataflow-v2"
        assert {"runtime_query", "timestamp", "failure_semantics", "no_persistence"} <= set(
            rows[role]["required_evidence"])
    assert set(raw["data_input_contracts"]["MARKET_DATA"]["allowed_consumers"]) == {
        "technical", "risk", "trader"}
    assert len(rows) == 10 and rows["technical"]["products"] == ["MARKET_DATA"]


@pytest.mark.parametrize("changes,reason", [
    ({"bid": float("nan")}, "price_invalid"), ({"ask": 0}, "price_invalid"),
    ({"ask": -1}, "price_invalid"), ({"bid": 101}, "spread_invalid"),
    ({"ask": 110}, "spread_invalid"), ({"currency": "EUR"}, "currency_mismatch"),
    ({"symbol": "NVDA"}, "symbol_mismatch"), ({"market_data_mode": "delayed"}, "not_current"),
    ({"adjusted": True}, "adjustment_invalid"), ({"source": ""}, "source_or"),
    ({"min_size": 0}, "precision_missing"), ({"size_increment": 0}, "precision_missing"),
    ({"min_tick": float("nan")}, "precision_missing"),
    ({"source_as_of": lambda: datetime.now(UTC) - timedelta(minutes=1)}, "stale"),
    ({"source_as_of": lambda: datetime.now(UTC) + timedelta(minutes=1)}, "future"),
    ({"source_as_of": lambda: datetime.now(UTC).replace(tzinfo=None)}, "timezone_missing"),
    ({"schema_version": "old"}, "schema_version_invalid"),
    ({"price_kind": "last"}, "not_current"), ({"source_precision": "receipt"}, "not_current"),
])
def test_quote_fail_closed(price_clock, changes, reason):
    # Collection may precede this case by minutes during the full business run.
    changes = {key: value() if callable(value) else value for key, value in changes.items()}
    with pytest.raises(prices.PriceUnavailable, match=reason):
        prices.validate_quote(quote(**changes), symbol="AAPL")


def test_real_calendar_holiday_and_early_close():
    regular, close = session_context(datetime(2026, 11, 27, 17, 59, tzinfo=UTC))
    assert regular
    regular, close = session_context(datetime(2026, 11, 27, 18, 0, tzinfo=UTC))
    assert not regular and close.hour == 18
    regular, close = session_context(datetime(2026, 11, 26, 16, 0, tzinfo=UTC))
    assert not regular and close.day == 25


def test_trader_cannot_read_unbound_market_or_research(execution):
    for scope in ({"entity": "AAPL"}, {"kind": "options", "entity": "AAPL"},
                  {"kind": "execution_price", "entity": "AAPL"}):
        with pytest.raises(PermissionError, match="binding_required"):
            read_input("trader", "MARKET_DATA", scope=scope)
    with pytest.raises(PermissionError, match="undeclared"):
        read_input("trader", "COMPANY_DATA", scope={"entity": "AAPL"})
    assert execution.calls == []


@pytest.mark.parametrize("change", ["entity", "purpose", "currency", "cycle_id"])
def test_order_bound_scope_cannot_escape(execution, change):
    order = TradeDecision(symbol="AAPL", action="buy", qty=1)
    with prices.price_request("trader", [order], cycle_id="pending",
                              purpose="preapproval_normalization"):
        scope = {"kind": "execution_price", "entity": "AAPL", "currency": "USD",
                 "cycle_id": "pending", "purpose": "preapproval_normalization"}
        scope[change] = "escape"
        with pytest.raises(PermissionError, match="mismatch"):
            read_input("trader", "MARKET_DATA", scope=scope)
    assert execution.calls == []


def test_normalization_review_revision_card_share_exact_orders(execution):
    state = ready_state(execution)
    decision = state.decisions[0]
    assert decision.qty == 9 and decision.limit_price == 100.6
    assert state.qty_by_symbol == {"AAPL": 9}
    revision = DecisionAuditRepository(execution.store).latest_revision(state.cycle_id)
    assert json.loads(revision["orders_json"]) == [decision.model_dump(mode="json")]
    assert datetime.fromisoformat(state.risk_review.basis.market_as_of) == datetime.fromisoformat(
        decision.execution_basis["quote"]["source_as_of"])
    assert decision.execution_basis["quote_ref"] in state.approval_summary
    assert len(execution.calls) == 1  # Risk consumes the same captured quote, never requeries.


def test_actual_trader_simulates_submission_and_persists_audit(execution):
    state = ready_state(execution)
    result = chief.trader(state)
    assert len(execution.broker.accepted) == 1
    assert result["order_results"][0].status == "submitted"
    chief.persist(state.model_copy(update=result))
    row = execution.store.conn.execute("SELECT * FROM trades").fetchone()
    context = json.loads(row["context"])
    assert context["execution_price_audit"][0]["execution_quote_ref"].startswith("execution-quote:")
    assert row["decision_hash"] == state.revision_hash and row["qty"] == 9
    assert guard.is_prohibited() and trader.AUTO_EXECUTION_ENABLED is False


def test_actual_graph_through_human_approval_to_simulation(execution):
    rows = trader.execute([TradeDecision(symbol="AAPL", action="buy", notional_usd=1000)],
                          source="manual", channel="test")
    assert rows[0].status == "submitted" and len(execution.broker.accepted) == 1
    assert execution.store.conn.execute("SELECT COUNT(*) FROM boss_approvals").fetchone()[0] == 1


@pytest.mark.parametrize("mutation", ["qty", "limit_price", "order_type", "time_in_force", "action"])
def test_submitted_order_must_equal_approved_revision(execution, mutation):
    state = ready_state(execution)
    auth = bind_to_active_route(build_authorization(
        DecisionAuditRepository(execution.store), state.cycle_id)).model_dump()
    decision = state.decisions[0]
    value = {"qty": 8, "limit_price": 100.5, "order_type": "market",
             "time_in_force": "GTC", "action": "sell"}[mutation]
    changed = decision.model_copy(update={mutation: value})
    rows, _fills = trader.place_orders([(changed, changed.qty)], state.cycle_id,
                                     revision_no=state.revision_no, authorization=auth)
    assert rows[0].status == "rejected" and "revision" in rows[0].error
    assert execution.broker.accepted == []


@pytest.mark.parametrize("changes,reason", [
    ({"bid": 101.9, "ask": 102}, "deviation"),
    ({"bid": 100.6, "ask": 100.7}, "limit_not_marketable"),
    ({"bid": 100.2, "ask": 100.3}, "funds_exceeded"),
])
def test_execution_changes_refuse_without_mutating(execution, monkeypatch, changes, reason):
    order = TradeDecision(symbol="AAPL", action="buy", qty=5, order_type="market") if (
        reason == "funds_exceeded") else TradeDecision(symbol="AAPL", action="buy", notional_usd=1000)
    state = ready_state(execution, [order])
    before = state.decisions[0].model_dump(mode="json")
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",
                        lambda symbol, **kw: quote(**changes))
    result = chief.trader(state)
    assert result["order_results"][0].status == "rejected"
    assert reason in result["order_results"][0].error
    assert result["order_results"][0].execution_price_audit[0]["quote"]["ask"] == changes["ask"]
    assert state.decisions[0].model_dump(mode="json") == before
    assert execution.broker.accepted == []


def test_missing_quote_rejects_amount_visibly_before_approval(execution, monkeypatch):
    def absent(*a, **kw):
        raise ValueError("source_timestamp_missing")
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price", absent)
    state = ChiefDecisionState(cycle_id="missing", as_of=datetime.now(UTC), decide=False,
                               decisions=[TradeDecision(symbol="AAPL", action="buy", notional_usd=1)])
    state = state.model_copy(update=chief.risk_gate(state))
    assert state.normalization_errors and "source_timestamp_missing" in state.approval_summary
    assert state.approved_decisions == [] and state.risk_review.verdict == "rejected"
    assert chief.route_after_persist(state) == "manual_review"


def test_below_minimum_quantity_is_visible(execution):
    with pytest.raises(prices.PriceUnavailable, match="no_action_below_minimum"):
        prices.normalize_orders([TradeDecision(symbol="AAPL", action="buy", notional_usd=1)],
                                cycle_id="small")


def test_no_grant_simulation_refuses(execution):
    state = ready_state(execution)
    guard.revoke_grant("test")
    result = chief.trader(state)
    assert result["order_results"][0].status in {"rejected", "error"}
    assert execution.broker.accepted == []


def test_c3_still_blocks_without_explicit_simulation(monkeypatch):
    monkeypatch.setattr(trader, "IBKRBroker", lambda: pytest.fail("must not construct real broker"))
    rows, _ = trader.place_orders([(TradeDecision(symbol="AAPL", action="buy", qty=1), 1)], "prod")
    assert rows[0].status == "rejected" and "生产 C3" in rows[0].error


def test_simulation_requires_complete_isolation():
    with pytest.raises(PermissionError, match="complete_isolation"), simulated_execution(
            store=get_store(), account="DU1"):
        pytest.fail("must not enter")


def test_real_ibkr_transport_stays_prohibited_in_simulation(execution):
    from ats.broker import IBKRBroker
    with pytest.raises(guard.BrokerWriteProhibited):
        IBKRBroker().place_orders([(TradeDecision(symbol="AAPL", action="buy", qty=1), 1)], "escape")


def test_saved_fake_broker_cannot_escape_context(tmp_path):
    with isolated_run("escape", root=tmp_path / "iso"):
        with simulated_execution(store=get_store(), account="DU1") as broker:
            guard.grant_write("simulation", 1, environment="paper", account="DU1")
        assert guard.active_grant() is None
        with pytest.raises(PermissionError, match="not_active"):
            broker.place_orders([], "escape")


def test_read_only_bid_ask_adapter_preserves_server_timestamp(monkeypatch):
    from ats.broker.ibkr import IBKRBroker
    now = datetime.now(UTC)
    source, calls = now - timedelta(seconds=7), []
    class IB:
        def qualifyContracts(self, c):
            return [c]
        def reqTickByTickData(self, c, *a):
            calls.append("read")
            return SimpleNamespace(marketDataType=1, tickByTicks=[SimpleNamespace(
                time=source, bidPrice=100, askPrice=100.1)])
        def cancelTickByTickData(self, *a):
            calls.append("cancel_subscription")
        def sleep(self, delay):
            pass
    @contextmanager
    def session(*a, **kw):
        yield IB()
    broker = IBKRBroker()
    monkeypatch.setattr(broker, "session", session)
    row = broker.get_execution_quote("AAPL", currency="USD", metadata={"symbol": "AAPL", "currency": "USD"})
    assert row["source_as_of"] == source and row["queried_at"] >= now
    assert calls == ["read", "cancel_subscription"]


def test_execution_age_rechecked_at_actual_broker_handoff(execution, monkeypatch):
    from phase_f_broker_harness import FakeIB, broker_for
    state = ready_state(execution)
    decision = state.decisions[0]
    stale = quote(source_as_of=datetime.now(UTC) - timedelta(seconds=31))
    # Real submit implementation, fake IB transport; no production authority used.
    guard.reset_for_tests()
    with monkeypatch.context() as local:
        local.delenv("ATS_BROKER_WRITE_PROHIBITION")
        local.delenv("ATS_RUN_MODE")
        guard.grant_write("simulation", 1, environment="paper", account="DU1")
        ib = FakeIB()
        broker = broker_for(ib)
        rows = broker.place_orders([(decision, decision.qty)], state.cycle_id,
                                   execution_check=lambda d, q: prices.check_execution(d, q, stale))
    assert rows[0].status == "error" and "stale" in rows[0].error and ib.accepted == []


def test_historical_close_is_only_an_overnight_normalization_basis(execution, monkeypatch):
    close = datetime.now(UTC) - timedelta(hours=12)
    monkeypatch.setattr(prices, "session_context", lambda now: (False, close))
    historical = quote(source_as_of=close, price_kind="previous_session_close", close=100,
                       session="previous_completed_regular", market_data_mode="historical",
                       source_precision="session_date")
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",
                        lambda *a, **kw: historical)
    orders, notes = prices.normalize_orders([
        TradeDecision(symbol="AAPL", action="buy", notional_usd=1000, order_type="market")],
        cycle_id="overnight", overnight=True)
    assert orders[0].qty == 9 and orders[0].limit_price == 100.5
    assert orders[0].order_type == "limit" and "previous_session_close" in notes[0]
    with pytest.raises(prices.PriceUnavailable, match="outside_regular"):
        prices.check_execution(orders[0], 9, historical)
    with pytest.raises(prices.PriceUnavailable, match="previous_session_close_invalid"):
        prices.validate_quote(historical.model_copy(update={"source_as_of": close - timedelta(days=1)}),
                              symbol="AAPL", overnight=True)


def test_duplicate_symbol_orders_keep_individual_quotes_and_quantities(execution):
    state = ready_state(execution, [
        TradeDecision(symbol="AAPL", action="buy", qty=2),
        TradeDecision(symbol="AAPL", action="buy", qty=3)])
    assert [d.qty for d in state.decisions] == [2, 3]
    assert len(execution.calls) == 2
    result = chief.trader(state)
    assert [row.qty for row in result["order_results"]] == [2, 3]
    assert len(execution.broker.accepted) == 2


def test_price_rejection_requires_new_review_and_approval(execution, monkeypatch):
    from ats.execution.authorization import AuthorizationError

    state = ready_state(execution)
    old_auth = build_authorization(DecisionAuditRepository(execution.store), state.cycle_id)
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",
                        lambda *a, **kw: quote(bid=102, ask=102.1))
    rejected = chief.trader(state)
    assert rejected["gate_outcome"] == "stale" and rejected["authorization"] is None
    assert not rejected["decisions"][0].execution_basis
    refreshed = state.model_copy(update=rejected)
    refreshed = refreshed.model_copy(update=chief.risk_gate(refreshed))
    assert refreshed.revision_hash != old_auth.decision_hash
    refreshed = refreshed.model_copy(update=chief.persist_decision(refreshed))
    with pytest.raises(AuthorizationError, match="human approval"):
        build_authorization(DecisionAuditRepository(execution.store), state.cycle_id)
    assert execution.broker.accepted == []


@pytest.mark.parametrize("failure", ["account", "generation", "freeze"])
def test_simulation_does_not_bypass_route_authority(execution, failure):
    from ats.execution.route_registry import freeze_submissions

    state = ready_state(execution)
    if failure == "account":
        execution.broker.account = "DU2"
    elif failure == "generation":
        guard.grant_write("simulation", 2, environment="paper", account="DU1")
    else:
        freeze_submissions(actor="test", reason="test")
    result = chief.trader(state)
    assert result["order_results"][0].status in {"rejected", "error"}
    assert execution.broker.accepted == []


@pytest.mark.parametrize("field,value", [("contract_version", "target-dataflow-v1"),
                                         ("execution_price_purposes", ["research"]),
                                         ("execution_price_currency", "EUR"),
                                         ("required_evidence", ["authorization"])])
def test_contract_guard_rejects_incomplete_price_contract(tmp_path, field, value):
    raw = yaml.safe_load((REPO_ROOT / "config/data/target_dataflow_coverage.yaml").read_text())
    next(row for row in raw["consumers"] if row["id"] == "trader")[field] = value
    path = tmp_path / "coverage.yaml"
    path.write_text(yaml.safe_dump(raw))
    assert not validate_target_contract(path)["valid"]


def test_handoff_price_expiry_reenters_review_without_submission(execution, monkeypatch):
    state = ready_state(execution)
    original, calls = prices.check_execution, []
    def expiring(d, q, quote, **kw):
        calls.append(d.symbol)
        if len(calls) == 2:
            raise prices.PriceUnavailable("quote_stale")
        return original(d, q, quote, **kw)
    monkeypatch.setattr(prices, "check_execution", expiring)
    result = chief.trader(state)
    assert result["gate_outcome"] == "stale" and result["authorization"] is None
    assert execution.broker.accepted == []


def test_later_handoff_failure_preserves_first_receipt(execution, monkeypatch):
    state = ready_state(execution, [TradeDecision(symbol="AAPL", action="buy", qty=2),
                                    TradeDecision(symbol="AAPL", action="buy", qty=3)])
    original, calls = prices.check_execution, []
    def expiring(d, q, quote, **kw):
        calls.append(q)
        if len(calls) == 4:
            raise prices.PriceUnavailable("quote_stale")
        return original(d, q, quote, **kw)
    monkeypatch.setattr(prices, "check_execution", expiring)
    result = chief.trader(state)
    assert [entry.status for entry in result["order_results"]] == ["submitted", "rejected"]
    assert len(execution.broker.accepted) == 1
    persisted = chief.persist(state.model_copy(update=result))
    assert persisted["cycle_status"] == "manual_review"
    assert execution.store.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 2


def test_price_native_api_cannot_borrow_production_legacy_or_isolated_candidate(execution, monkeypatch):
    from ats.workflow.cutover_routing import RouteUnavailable

    seen = []
    def legacy(identity):
        seen.append(identity)
        return SimpleNamespace(route="legacy")
    monkeypatch.setattr("ats.workflow.runtime_reads.gate_read", legacy)
    monkeypatch.setattr("ats.workflow.isolation.verified_isolation_root", lambda: None)
    order = TradeDecision(symbol="AAPL", action="buy", qty=1)
    with prices.price_request("trader", [order], cycle_id="unqualified",
                              purpose="preapproval_normalization"), pytest.raises(
                                  RouteUnavailable, match="enabled exact business scope"):
        prices.read_price("trader", order)
    assert execution.calls == []
    assert all(i.consumer_id == "trader" and i.contract_version == "target-dataflow-v2"
               and i.scope["entities"] == ["AAPL"] for i in seen)


def test_runtime_quotes_do_not_write_shared_facts_or_memory(execution):
    changes = execution.store.conn.total_changes
    orders, _notes = prices.normalize_orders([
        TradeDecision(symbol="AAPL", action="buy", notional_usd=1000)], cycle_id="read-only")
    assert orders[0].execution_basis["quote_ref"]
    assert execution.store.conn.total_changes == changes


def test_overnight_chief_refreshes_price_and_revision_at_regular_session(execution, monkeypatch):
    close = datetime.now(UTC) - timedelta(hours=12)
    monkeypatch.setattr(prices, "session_context", lambda now: (False, close))
    historical = quote(source_as_of=close, price_kind="previous_session_close", close=100,
                       session="previous_completed_regular", market_data_mode="historical",
                       source_precision="session_date")
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",
                        lambda *a, **kw: historical)
    state = ChiefDecisionState(cycle_id="overnight-chief", as_of=datetime.now(UTC),
                               source="pead-chief", decide=False, dry_run=False,
                               decisions=[TradeDecision(symbol="AAPL", action="buy",
                                                        notional_usd=1000, order_type="market")])
    state = state.model_copy(update=chief.risk_gate(state))
    assert state.risk_review.verdict == "approved" and state.decisions[0].order_type == "limit"
    state = state.model_copy(update=chief.persist_decision(state))
    prior_hash = state.revision_hash
    monkeypatch.setattr(prices, "session_context", lambda now: (True, close))
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",
                        lambda *a, **kw: quote())
    refreshed = state.model_copy(update=chief.risk_gate(state))
    assert refreshed.risk_review.verdict == "approved"
    assert refreshed.revision_hash != prior_hash
    assert refreshed.decisions[0].execution_basis["quote"]["price_kind"] == "bid_ask"
    refreshed = refreshed.model_copy(update=chief.persist_decision(refreshed))
    from ats.execution.authorization import AuthorizationError

    with pytest.raises(AuthorizationError, match="human approval"):
        build_authorization(DecisionAuditRepository(execution.store), refreshed.cycle_id)
