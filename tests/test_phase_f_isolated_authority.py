"""11.2: actual signed revision, Trader and submit exits; no IBKR session."""
import json
import pytest
from test_phase_f_trader_a import execution, price_clock, ready_state
from ats.execution import broker_write_guard as guard
from ats.execution.authorization import build_authorization, bind_to_active_route, AuthorizationError
from ats.decision.repository import DecisionAuditRepository
from ats.graph import chief
from ats.broker.ibkr import IBKRBroker
from ats.trader import execute as trader


@pytest.mark.parametrize("fault",["none","grant","account","generation","approval","real_broker"])
def test_actual_authority_cannot_escape_simulation(execution,monkeypatch,fault,record_property):
    monkeypatch.setattr(IBKRBroker,"session",lambda *a,**k:pytest.fail("IBKR session reached"))
    state=ready_state(execution)
    repo=DecisionAuditRepository(execution.store)
    auth=bind_to_active_route(build_authorization(repo,state.cycle_id)).model_dump(mode="json")
    assert auth["approval_id"] and auth["decision_hash"]
    if fault=="grant":guard.revoke_grant("negative test")
    elif fault=="account":execution.broker.account="DU2"
    elif fault=="generation":guard.grant_write("simulation",2,environment="paper",account="DU1")
    elif fault=="approval":
        repo.conn.execute("DELETE FROM boss_approvals WHERE cycle_id=?",(state.cycle_id,));repo.conn.commit()
        with pytest.raises(AuthorizationError):build_authorization(repo,state.cycle_id)
    elif fault=="real_broker":
        # A mistaken selector must reach production C3 before any broker factory.
        monkeypatch.setattr("ats.execution.simulation.selected_simulation",lambda:None)
    if fault=="approval":
        with pytest.raises(AuthorizationError):
            trader.place_orders([(d,d.qty) for d in state.decisions],state.cycle_id,
                                revision_no=state.revision_no,authorization=auth)
        result=[]
    else:
        result=trader.place_orders([(d,d.qty) for d in state.decisions],state.cycle_id,
                              revision_no=state.revision_no,authorization=auth)[0]
    assert guard.is_prohibited()
    if fault=="none":assert len(execution.broker.accepted)==1 and result[0].status=="submitted"
    else:assert not execution.broker.accepted and (not result or result[0].status in {"rejected","error"})
    # Neither a real signed revision nor a valid isolated grant can unlock IBKR,
    # even Paper. This goes through the concrete facade before network creation.
    with pytest.raises(guard.BrokerWriteProhibited) as refused:
        IBKRBroker().place_orders([(d,d.qty) for d in state.decisions],state.cycle_id,revision_no=state.revision_no,
            chain={"decision_hash":auth["decision_hash"],"approval_id":auth["approval_id"]})
    record_property("actual_authority_case",json.dumps({"fault":fault,"authorization":auth,
        "orders":[r.model_dump(mode="json") for r in result],"refusal_id":refused.value.refusal_id,
        "reason_code":refused.value.reason_code}))


def test_simulator_and_authority_cannot_be_reused_after_exit(execution,monkeypatch,record_property):
    from ats.execution.simulation import simulated_execution
    state=ready_state(execution)
    auth=bind_to_active_route(build_authorization(DecisionAuditRepository(execution.store),state.cycle_id)).model_dump(mode="json")
    with simulated_execution(store=execution.store,account="DU1") as scoped:
        assert scoped is not execution.broker
    assert guard.active_grant() is None
    with pytest.raises(PermissionError,match="not_active"):
        scoped.place_orders([(d,d.qty) for d in state.decisions],state.cycle_id,revision_no=state.revision_no)
    # Simulate the caller retaining a result but having no active test transport.
    monkeypatch.setattr("ats.execution.simulation.selected_simulation",lambda:None)
    rows,_=trader.place_orders([(d,d.qty) for d in state.decisions],state.cycle_id,
                              revision_no=state.revision_no,authorization=auth)
    assert all(r.status=="rejected" and "C3" in r.error for r in rows)
    assert not scoped.accepted and not execution.broker.accepted
    record_property("exit_refusal",json.dumps([r.model_dump(mode="json") for r in rows]))
