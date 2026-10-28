"""Phase 9: Feishu async approval — card build, callback parse, resume roundtrip."""

import pytest

from datetime import datetime, timezone

from ats.channel.feishu_channel import build_approval_card, parse_callback
from ats.graph.chief_state import ChiefDecisionState
from ats.runtime.cli import resume_cycle, run_decision_graph
from ats.schemas.channel import ApprovalRequest
from ats.schemas.decision import TradeDecision

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _an_active_trade_route(monkeypatch, tmp_path):
    """Install the trade route this test's orders execute against.

    Phase F 2.5 requires an authorization to name the route and generation it was
    issued under, so the graph refuses with `route_unbound` without one. These tests
    exist to prove the path DOES place orders, so they need a route to place them
    on; the refusal path is covered by the authorization unit tests.
    """
    from ats.execution.route_registry import install_route

    path = tmp_path / "routes.sqlite"
    install_route("A", generation=1, environment="paper", account="DU1",
                  actor="test", reason="fixture", path=str(path))
    monkeypatch.setenv("ATS_ROUTE_REGISTRY_PATH", str(path))
    yield


def test_card_carries_thread_id_and_buttons():
    req = ApprovalRequest(cycle_id="cycle-1", as_of=NOW,
                          decisions=[TradeDecision(symbol="NVDA", action="buy", notional_usd=10000)])
    card = build_approval_card(req, thread_id="cycle-1")
    actions = [e for e in card["elements"] if e["tag"] == "action"][0]["actions"]
    values = {a["value"]["action"]: a["value"]["thread_id"] for a in actions}
    assert values == {"approve": "cycle-1", "reject": "cycle-1"}


def test_parse_url_verification():
    out = parse_callback({"type": "url_verification", "challenge": "abc"})
    assert out == {"kind": "challenge", "challenge": "abc"}


def test_parse_card_action_approve_and_reject():
    payload = {"event": {"operator": {"open_id": "ou_x"},
                         "action": {"value": {"action": "approve", "thread_id": "cycle-7"}}}}
    out = parse_callback(payload)
    assert out["kind"] == "approval" and out["thread_id"] == "cycle-7"
    assert out["approval"].status == "approved" and out["approval"].reviewer == "ou_x"

    payload["event"]["action"]["value"]["action"] = "reject"
    assert parse_callback(payload)["approval"].status == "rejected"


def test_parse_ignores_unknown():
    assert parse_callback({"event": {"action": {"value": {}}}})["kind"] == "ignore"


def test_async_flow_checkpoint_then_resume(async_channel, broker):
    # The decision graph pauses at the interrupt (async channel captures the
    # request), then a separate resume_cycle() (the webhook's job) drives
    # execution to completion via the checkpointed thread.
    state = ChiefDecisionState(
        cycle_id="chief-feishu-test", as_of=NOW, source="chief", decide=False, dry_run=False,
        seed_decisions=[TradeDecision(symbol=s, action="buy", qty=1, rationale="r")
                        for s in ("NVDA", "MSFT", "AAPL")])
    paused = run_decision_graph(state, channel=async_channel)
    assert "__interrupt__" in paused
    assert async_channel.thread_id == "chief-feishu-test"
    assert async_channel.request and len(async_channel.request.decisions) == 3

    from ats.schemas.decision import BossApproval

    result = resume_cycle(async_channel.thread_id,
                          BossApproval(status="approved"), channel=async_channel)
    assert len(result["order_results"]) == 3
    assert all(o.status == "filled" for o in result["order_results"])
