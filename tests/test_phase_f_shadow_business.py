"""Actual isolated business calls with synthetic external inputs, no network."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ats.data import execution_prices as prices
from ats.data.runtime.execution_prices import ExecutionPrice
from ats.execution import broker_write_guard as guard
from ats.execution.clerk import clerk_run
from ats.execution.route_registry import install_route
from ats.execution.shadow_execution import shadow_execution
from ats.graph import chief
from ats.graph.chief_state import ChiefDecisionState
from ats.memory import TradingMemory, bound_store, get_store
from ats.schemas.decision import BossApproval, TradeDecision
from ats.schemas.memory import TradeLogEntry
from ats.schemas.portfolio import PortfolioSnapshot
from ats.schemas.risk import RiskReview
from ats.workflow import intake_verification as iv
from ats.workflow import shadow_ledger as ledger
from ats.workflow.isolated_entry import run_isolated_entry


@pytest.fixture
def external_inputs(monkeypatch):
    guard.reset_for_tests()
    monkeypatch.setattr(prices, "session_context", lambda now: (True, now - timedelta(days=1)))
    def provider(symbol, **kwargs):
        now = datetime.now(UTC)
        return ExecutionPrice(symbol=symbol, currency="USD", source="synthetic:shadow-business",
            source_as_of=now, queried_at=now, price_kind="bid_ask", bid=100, ask=100.1,
            session="regular", market_data_mode="live", adjusted=False, min_size=1,
            size_increment=1, min_tick=.01)
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price", provider)
    pf = PortfolioSnapshot(as_of=datetime.now(UTC), net_liquidation=1_000_000,
                           cash=1_000_000, daily_pnl=0, gross_exposure=0, positions=[])
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: pf)
    monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda pf: None)
    monkeypatch.setattr("ats.risk.assess.enrich_options", lambda pf: None)
    monkeypatch.setattr("ats.risk.assess._prices", lambda symbols: {})
    monkeypatch.setattr("ats.risk.assess.assess", lambda pf, **kw: RiskReview(as_of=pf.as_of, risk_state="normal"))
    monkeypatch.setattr(chief, "interrupt", lambda request: BossApproval(status="approved",
        reviewer="fixture-human", channel="fixture", reviewed_at=datetime.now(UTC)).model_dump(mode="json"))
    # Actual IBKR facade must refuse before entering this session.
    def network(*args, **kwargs):
        raise AssertionError("broker session/network reached")
    monkeypatch.setattr("ats.broker.ibkr.IBKRBroker.session", network)
    yield
    guard.reset_for_tests()


def ready(cycle="shadow-cycle"):
    install_route("shadow-candidate", generation=1, environment="paper", account="DU1",
                  actor="fixture", reason="isolated candidate only")
    state = ChiefDecisionState(cycle_id=cycle, as_of=datetime.now(UTC), decide=False,
        use_llm=False, dry_run=False, decisions=[TradeDecision(symbol="AAPL", action="buy", notional_usd=1000)])
    state = state.model_copy(update=chief.risk_gate(state))
    assert state.risk_review.verdict == "approved", state.risk_notes
    state = state.model_copy(update=chief.persist_decision(state))
    state = state.model_copy(update=chief.boss_review(state))
    assert state.approval.status == "approved"
    return state


def test_actual_clerk_entry_overrides_outer_store_without_production_qualification(tmp_path):
    outer = TradingMemory(tmp_path / "production.sqlite")
    before = outer.conn.total_changes
    with bound_store(outer):
        result = run_isolated_entry(run_id="clerk-intake", entry=clerk_run,
                                   root=tmp_path / "iso", broker=object(), steps=())
        assert get_store() is outer
    assert outer.conn.total_changes == before
    assert outer.conn.execute("SELECT COUNT(*) FROM clerk_runs").fetchone()[0] == 0
    with sqlite3.connect(tmp_path / "iso/memory.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM clerk_runs").fetchone()[0] == 1
    assert result.tradable is False and result.attestation["qualification_required"] is False
    assert result.attestation["broker_write_prohibited"] is True
    with sqlite3.connect(tmp_path / "iso/data.sqlite") as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='dataflow_assurance_events'").fetchone()
    with sqlite3.connect(result.record_path) as conn:
        row = json.loads(conn.execute("SELECT body FROM isolated_business_entries").fetchone()[0])
        assert row["entry"] == "ats.execution.clerk.clerk_run" and row["status"] == "completed"
    with pytest.raises(iv.IntakeVerificationError):
        iv.assert_isolated_result_not_tradable(iv.IsolationAttestation(**{
            k:v for k,v in result.attestation.items() if k in iv.IsolationAttestation.__dataclass_fields__}))


def test_actual_chief_trader_expected_refusal_is_nonempty_and_persisted_separately(tmp_path, external_inputs):
    with iv.isolated_verification("actual-shadow", root=tmp_path / "iso"):
        state = ready()
        with shadow_execution(run_id="actual-shadow", store=get_store()) as transport:
            result = chief.trader(state)
            assert result["gate_outcome"] == "shadow_refused"
            assert result["order_results"][0].status == "rejected"
            assert result["order_results"][0].error.startswith("shadow broker write prohibited:")
            state = state.model_copy(update=result)
            chief.persist(state)
            assert get_store().conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
            assert get_store().conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
            rebuilt = ledger.rebuild_attribution(state.cycle_id, path=transport.path)
            assert rebuilt["order_count"] == rebuilt["refused_count"] == 1
            assert rebuilt["submitted_count"] == 0 and rebuilt["every_order_authorized"]
            order = rebuilt["orders"][0]
            assert order["provenance"]["order"] == state.approved_decisions[0].model_dump(mode="json")
            assert order["provenance"]["authorization"]["review_id"]
            assert order["provenance"]["order"]["execution_basis"]["quote"]["source_as_of"]
            attempt = order["attempts"][0]
            assert attempt["detail"]["execution_price_audit"][0]["quote"]["source_as_of"]
            assert attempt["detail"]["guard"]["operation"] == "place_orders"
            assert attempt["refusal_id"] == guard.refusals()[-1].refusal_id
            assertion = ledger.shadow_attestation(run_id="actual-shadow", store=get_store(), path=transport.path)
            assert assertion["prohibition_exercised"] and assertion["no_order_reached_broker"]
        saved = get_store().conn.execute("SELECT final_outcome FROM decision_cycles").fetchone()[0]
        assert saved == "shadow_refused"


@pytest.mark.parametrize("writer", ["save", "insert", "fills"])
def test_real_publication_points_refuse_shadow_writes(tmp_path, writer):
    with iv.isolated_verification("miswrite", root=tmp_path / "iso"):
        store = get_store()
        with shadow_execution(run_id="miswrite", store=store):
            before = store.conn.total_changes
            entry = TradeLogEntry(order_id="", cycle_id="test", symbol="AAPL", action="buy", qty=1,
                                  status="rejected", submitted_at=datetime.now(UTC))
            with pytest.raises(ledger.ShadowLedgerWriteRefused):
                if writer == "fills":
                    store.upsert_fills([{"exec_id": "bad"}])
                else:
                    call = store.save_trades if writer == "save" else store._insert_trades
                    call([entry], cycle_id="test", source="manual")
            assert store.conn.total_changes == before


def test_simulation_grant_does_not_escape_verification(tmp_path):
    guard.reset_for_tests()
    with iv.isolated_verification("grant", root=tmp_path / "iso"):
        guard.grant_write("candidate", 1, environment="paper", account="DU1")
    assert guard.active_grant() is None
    with pytest.raises(guard.BrokerWriteProhibited):
        guard.check_broker_write(operation="placeOrder", caller="escaped-result")


def test_production_same_count_update_invalidates_exceptional_run(tmp_path, monkeypatch):
    production = tmp_path / "prod"
    production.mkdir()
    with sqlite3.connect(production / "phase_f_routes.sqlite") as conn:
        conn.execute("CREATE TABLE trade_route_state (route TEXT)")
        conn.execute("INSERT INTO trade_route_state VALUES ('legacy')")
    monkeypatch.setattr(iv, "_production_db_path", lambda name: production / name)
    with pytest.raises(iv.ProductionSideEffect, match="trade_route_state:content"):
        with iv.isolated_verification("invalidated", root=tmp_path / "iso"):
            with sqlite3.connect(production / "phase_f_routes.sqlite") as conn:
                conn.execute("UPDATE trade_route_state SET route='target'")
            raise ValueError("entry failed before context return")


def test_restart_rebuild_and_retry_keep_original_intent(tmp_path, external_inputs):
    with iv.isolated_verification("restart", root=tmp_path / "iso"):
        state = ready()
        with shadow_execution(run_id="restart", store=get_store()) as transport:
            first = chief.trader(state)
            assert first["gate_outcome"] == "shadow_refused"
            original = ledger.intents(path=transport.path)
            second = chief.trader(state)
            assert second["gate_outcome"] == "shadow_refused"
            assert ledger.intents(path=transport.path) == original
            assert len(ledger.submit_attempts(path=transport.path)) == 2
            # Rebuild in a fresh process with no broker/provider/store.
            program = "from ats.workflow.shadow_ledger import rebuild_attribution;import json,sys;print(json.dumps(rebuild_attribution(sys.argv[1],path=sys.argv[2])))"
            completed = subprocess.run([sys.executable, "-c", program, state.cycle_id, str(transport.path)],
                                       text=True, capture_output=True)
            assert completed.returncode == 0, completed.stderr
            rebuilt = json.loads(completed.stdout)
            assert rebuilt["refused_count"] == 1 and len(rebuilt["orders"][0]["attempts"]) == 2
            assert ledger.intents(path=transport.path) == original
            with sqlite3.connect(transport.path) as conn:
                for table in ("shadow_order_intents", "shadow_submit_attempts", "shadow_intent_provenance"):
                    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                        conn.execute(f"DELETE FROM {table}")


def test_shadow_transport_requires_complete_isolation_and_independent_ledger(tmp_path, monkeypatch):
    with pytest.raises(PermissionError):
        with shadow_execution(run_id="outside", store=TradingMemory(":memory:")):
            pass
    with iv.isolated_verification("path", root=tmp_path / "iso"):
        monkeypatch.setenv("ATS_SHADOW_ORDER_DB", os.environ["ATS_DB_PATH"])
        with pytest.raises(PermissionError):
            with shadow_execution(run_id="aliased", store=get_store()):
                pass


def test_actual_shadow_entry_records_completion_but_never_authorizes_production(tmp_path, external_inputs):
    root = tmp_path / "iso"
    with iv.isolated_verification("prepare", root=root):
        state = ready()
    result = run_isolated_entry(run_id="shadow-entry", entry=chief.trader, root=root, shadow=True, state=state)
    assert result.output["gate_outcome"] == "shadow_refused" and not result.tradable
    with sqlite3.connect(result.record_path) as conn:
        record = json.loads(conn.execute("SELECT body FROM isolated_business_entries").fetchone()[0])
        assert record["status"] == "completed" and record["entry"] == "ats.graph.chief.trader"
    assert guard.active_grant() is None
    assert ledger.rebuild_attribution(state.cycle_id, path=root / "shadow_orders.sqlite")["refused_count"] == 1


def test_unexpected_unblocked_broker_result_stops_shadow_run(tmp_path, external_inputs, monkeypatch):
    root = tmp_path / "iso"
    with iv.isolated_verification("prepare", root=root):
        state = ready()
    monkeypatch.setattr("ats.broker.ibkr.IBKRBroker.place_orders", lambda *a, **k: [])
    with pytest.raises(ledger.ShadowLedgerWriteRefused, match="unexpected shadow"):
        def entry(state):
            result = chief.trader(state)
            chief.persist(state.model_copy(update=result))
        run_isolated_entry(run_id="unblocked", entry=entry, root=root, shadow=True, state=state)
    assert ledger.submit_attempts(path=root / "shadow_orders.sqlite")[0]["accepted"] == 1
    with sqlite3.connect(root / "memory.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    with sqlite3.connect(root / "business_entries.sqlite") as conn:
        record = json.loads(conn.execute("SELECT body FROM isolated_business_entries").fetchone()[0])
        assert record["status"] == "failed"


def test_clerk_publication_violation_cannot_be_swallowed_as_a_step_error(tmp_path, monkeypatch):
    from ats.execution import clerk

    with iv.isolated_verification("clerk-stop", root=tmp_path / "iso"):
        store = get_store()
        entry = TradeLogEntry(order_id="", cycle_id="bad", symbol="AAPL", action="buy", qty=1,
                              status="rejected", submitted_at=datetime.now(UTC))
        def wrong(store, **kwargs):
            store.save_trades([entry], cycle_id="bad", source="manual")
        calls = []
        monkeypatch.setattr(clerk, "_STEPS", {"wrong": wrong, "later": lambda *a, **k: calls.append(True)})
        with shadow_execution(run_id="clerk-stop", store=store):
            with pytest.raises(ledger.ShadowLedgerWriteRefused):
                clerk.clerk_run(store=store, broker=object(), steps=("wrong", "later"))
        assert not calls


def test_alias_root_rejected_before_any_database_write(tmp_path):
    from ats.workflow.isolation import isolated_run

    original = Path(os.environ["ATS_DATA_DB_PATH"])
    before = original.read_bytes() if original.exists() else None
    with pytest.raises(PermissionError, match="aliases_production"):
        with isolated_run("aliased", root=tmp_path):
            pytest.fail("aliased root entered")
    assert (original.read_bytes() if original.exists() else None) == before
    assert not (tmp_path / "memory.sqlite").exists()


def test_shadow_result_still_refuses_trades_after_context_exit(tmp_path, external_inputs):
    root = tmp_path / "iso"
    with iv.isolated_verification("prepare", root=root):
        state = ready()
    result = run_isolated_entry(run_id="escaped-shadow", entry=chief.trader, root=root, shadow=True, state=state)
    # Model JSON roundtrip must preserve the origin, not just an in-process flag.
    entry = TradeLogEntry.model_validate_json(result.output["order_results"][0].model_dump_json())
    production = TradingMemory(tmp_path / "production.sqlite")
    before = production.conn.total_changes
    with pytest.raises(ledger.ShadowLedgerWriteRefused):
        production.save_trades([entry], cycle_id=state.cycle_id, source="manual")
    assert production.conn.total_changes == before


def test_fake_receipts_cannot_be_published_to_production(tmp_path, external_inputs):
    from ats.execution.simulation import simulated_execution

    production = TradingMemory(tmp_path / "production.sqlite")
    before = production.conn.total_changes
    with iv.isolated_verification("simulation", root=tmp_path / "iso"):
        state = ready()
        guard.grant_write("shadow-candidate", 1, environment="paper", account="DU1")
        with simulated_execution(store=get_store(), account="DU1") as broker:
            result = chief.trader(state)
            entry = result["order_results"][0]
            assert entry.status == "submitted" and entry.isolation_root
            broker.simulate_fill(entry.order_id, shares=1, price=100)
            fill = dict(broker.fills[0])
    assert guard.active_grant() is None
    for callback in (
        lambda: production.save_trades([entry], cycle_id=state.cycle_id, source="chief"),
        lambda: production.upsert_fills([fill]),
    ):
        with pytest.raises(ledger.ShadowLedgerWriteRefused, match="original isolation root"):
            callback()
    assert production.conn.total_changes == before


def test_early_c3_rejection_return_keeps_isolated_origin(tmp_path):
    from ats.trader import execute

    result = run_isolated_entry(run_id="early-rejection", entry=execute.place_orders,
        root=tmp_path / "iso", to_place=[(TradeDecision(symbol="AAPL", action="buy", qty=1), 1)],
        cycle_id="early")
    entry = result.output[0][0]
    assert entry.status == "rejected" and entry.isolation_root == str((tmp_path / "iso").resolve())
    production = TradingMemory(tmp_path / "production.sqlite")
    with pytest.raises(ledger.ShadowLedgerWriteRefused, match="original isolation root"):
        production.save_trades([entry], cycle_id="early", source="manual")
