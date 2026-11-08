"""Risk and Clerk read through the governed read API (switch-trade r5 task 6.4).

Risk's business scope is the book it judges, so a symbol read is made only for a book under review; Clerk
reads the broker one step at a time and keeps each step's own error; the scanner sees where Risk's reads
really are and treats a direct runtime read by a decision role as not yet moved.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ats.workflow import consumer_scan as cs

pytestmark = pytest.mark.governed_decision_reads


# --------------------------------------------------------------------------- #
# Risk
# --------------------------------------------------------------------------- #
def test_a_symbol_read_outside_a_book_under_review_is_refused():
    from ats.risk import inputs

    with pytest.raises(PermissionError, match="outside_book"):
        inputs.beta("QQQ")
    with pytest.raises(PermissionError, match="outside_book"):
        inputs.close_history(["QQQ"])
    with pytest.raises(PermissionError, match="outside_book"):
        inputs.atm_iv("QQQ")


def test_the_book_is_the_held_symbols_their_risk_symbols_and_underlyings():
    from ats.risk import inputs

    registry = SimpleNamespace(resolve=lambda s: SimpleNamespace(risk_symbol={"SKUU": "HY9H"}.get(s, s)))
    book = SimpleNamespace(positions=[SimpleNamespace(symbol="SKUU"),
                                      SimpleNamespace(symbol="QQQ", underlying="QQQ"),
                                      SimpleNamespace(symbol="AXTI", underlying="AXTI")])
    assert inputs.book_entities(book, registry) == ["AXTI", "HY9H", "QQQ", "SKUU"]


def test_a_symbol_read_inside_the_book_is_checked_against_it(monkeypatch):
    from ats.risk import inputs

    bound = SimpleNamespace(identity=SimpleNamespace(consumer_id="risk", scope={"entities": ["QQQ"]}),
                            invocation=None)
    monkeypatch.setattr("ats.workflow.runtime_reads.current_read_context", lambda: bound)
    inputs._in_book(["QQQ"])
    with pytest.raises(PermissionError, match="NVDA"):
        inputs._in_book(["QQQ", "NVDA"])


def test_risk_is_authorised_for_its_nav_history_and_the_full_ruleset(monkeypatch):
    from ats.data import consumer_api

    assert consumer_api.input_contract("risk", "HISTORY_DATA")["consumer"] == "risk"
    monkeypatch.setattr("ats.workflow.runtime_reads.guard_input", lambda *a, **k: None)
    packet = consumer_api.read_input("risk", "RISK_RULES", scope={})
    assert packet.status == "complete"
    assert set(packet.payload) == {"limits", "policy", "instruments"}


def test_the_ruleset_read_through_the_api_is_the_configured_one(monkeypatch):
    from ats.config import get_config, load_instrument_risk_registry, load_risk_policy
    from ats.risk import inputs

    monkeypatch.setattr("ats.workflow.runtime_reads.guard_input", lambda *a, **k: None)
    monkeypatch.setattr(inputs, "_bound", lambda entities=(): __import__("contextlib").nullcontext())
    rules = inputs.rules()
    assert rules.limits == get_config().app.risk
    assert rules.policy == load_risk_policy()
    assert rules.instruments == load_instrument_risk_registry()


# --------------------------------------------------------------------------- #
# Clerk
# --------------------------------------------------------------------------- #
def test_a_broker_step_reads_only_that_step():
    from ats.data.runtime.broker import broker_step

    calls = []
    broker = SimpleNamespace(get_fills=lambda: calls.append("fills") or [{"exec_id": "E1"}],
                             completed_orders=lambda: calls.append("orders") or [],
                             get_portfolio=lambda: calls.append("portfolio"))
    packet = broker_step(broker, "fills")
    assert calls == ["fills"] and packet["refs"] == ["E1"] and packet["status"] == "complete"
    with pytest.raises(ValueError, match="unknown_broker_step"):
        broker_step(broker, "everything")


def test_a_failed_step_comes_back_as_the_brokers_own_error():
    from ats.broker import IBKRUnavailable
    from ats.data.runtime.clerk_reads import _raise_read_failure

    down = SimpleNamespace(status="unavailable",
                           gaps=["input_unavailable:IBKRUnavailable:cannot reach IBKR at 127.0.0.1:7496"])
    with pytest.raises(IBKRUnavailable, match="cannot reach IBKR"):
        _raise_read_failure(down)
    odd = SimpleNamespace(status="unavailable", gaps=["input_unavailable:ValueError:broker_read_not_collection"])
    with pytest.raises(RuntimeError, match="broker_read_not_collection"):
        _raise_read_failure(odd)


def test_clerk_reads_each_step_and_each_decision_chain_through_the_api(monkeypatch):
    from ats.data.runtime import clerk_reads

    seen = []

    def read_input(consumer, product, *, scope, **kw):
        seen.append((consumer, product, scope.get("step") or scope.get("cycle_id")))
        return SimpleNamespace(status="complete", payload=[{"exec_id": "E1"}], gaps=[])

    monkeypatch.setattr(clerk_reads, "_read_input", read_input)
    rows = SimpleNamespace(execute=lambda *a: [{"cycle_id": "c-1"}])
    reads = clerk_reads.ClerkBrokerReads(broker=object(), store=SimpleNamespace(conn=rows))
    assert reads.get_fills() == [{"exec_id": "E1"}]
    assert seen == [("clerk", "BROKER_STATE", "fills"), ("clerk", "DECISION_APPROVAL_CONTEXT", "c-1")]


# --------------------------------------------------------------------------- #
# The scanner
# --------------------------------------------------------------------------- #
def test_risk_is_scanned_where_its_review_is_computed():
    entry = set(cs.CONSUMER_ENTRY_POINTS["risk"])
    assert {"ats.risk.assess", "ats.risk.checks", "ats.agents.risk_officer.daily", "ats.risk.inputs"} <= entry
    assert "ats.data.runtime.clerk_reads" in cs.CONSUMER_ENTRY_POINTS["clerk"]


@pytest.mark.parametrize("role", ["risk", "clerk"])
def test_the_decision_roles_have_no_direct_runtime_read(role):
    record = cs.scan_consumer_access(role)
    kinds = {v.kind for v in record.violations}
    assert "runtime_bypass" not in kinds and "governed_read_surface_not_reached" not in kinds, \
        [(v.kind, v.location) for v in record.violations]
    assert any(m.startswith("ats.data.consumer_api") for m in record.call_path)


def test_a_direct_runtime_read_by_a_decision_role_is_reported(monkeypatch):
    monkeypatch.setitem(cs.CONSUMER_ENTRY_POINTS, "risk",
                        (*cs.CONSUMER_ENTRY_POINTS["risk"], "ats.trader.portfolio"))
    record = cs.scan_consumer_access("risk")
    assert any(v.kind == "runtime_bypass" and v.location.startswith("ats.trader.portfolio:")
               for v in record.violations)


def test_runtime_bypass_keeps_the_role_from_clearing(monkeypatch):
    from ats.workflow import retirement_clearance as rc

    record = SimpleNamespace(violations=[SimpleNamespace(kind="runtime_bypass")],
                             call_path=("ats.data.consumer_api",))
    verdicts = rc.verify_consumer_zero("decision_roles.unwired_to_governed_read_api",
                                       ["agents.risk_officer.review"], scan=lambda role: record)
    assert verdicts[0].verdict == rc.NOT_ZERO
    assert "directly" in verdicts[0].reason
