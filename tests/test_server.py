"""Webhook callback handler — challenge + end-to-end resume on the chief graph."""

from datetime import datetime, timezone

from ats.graph.chief_state import ChiefDecisionState
from ats.runtime.cli import run_decision_graph
from ats.runtime.server import handle_callback
from ats.schemas.decision import TradeDecision

NOW = datetime.now(timezone.utc)


def test_url_verification_challenge():
    out = handle_callback({"type": "url_verification", "challenge": "xyz"})
    assert out == {"challenge": "xyz"}


def test_card_action_resumes_and_executes(async_channel, broker):
    # 1) Pause a decision run at the interrupt (checkpointed to the per-test sqlite).
    state = ChiefDecisionState(
        cycle_id="chief-server-test", as_of=NOW, source="chief", decide=False, dry_run=False,
        seed_decisions=[TradeDecision(symbol=s, action="buy", qty=1, rationale="r")
                        for s in ("NVDA", "MSFT", "AAPL")])
    run_decision_graph(state, channel=async_channel)
    thread_id = async_channel.thread_id

    # 2) Simulate the Feishu approve callback reaching the webhook handler.
    out = handle_callback({"event": {"operator": {"open_id": "ou_boss"},
                                     "action": {"value": {"action": "approve",
                                                          "thread_id": thread_id}}}})
    assert out["toast"]["type"] == "success"

    # 3) The run completed -> trades landed in Context Memory.
    from ats.memory import get_store

    trades = get_store().recent_trades(limit=10)
    assert len(trades) == 3 and all(t["status"] == "filled" for t in trades)


def test_duplicate_callback_executes_once(async_channel, broker):
    """Feishu prefetches approval links / users double-tap → the same cycle can hit
    the handler concurrently. It must execute AT MOST ONCE (else double orders)."""
    from ats.runtime import server

    server._RESUMED.clear()
    state = ChiefDecisionState(
        cycle_id="chief-idem-test", as_of=NOW, source="chief", decide=False, dry_run=False,
        seed_decisions=[TradeDecision(symbol=s, action="buy", qty=1, rationale="r")
                        for s in ("NVDA", "MSFT")])
    run_decision_graph(state, channel=async_channel)
    thread_id = async_channel.thread_id
    cb = {"event": {"operator": {"open_id": "ou_boss"},
                    "action": {"value": {"action": "approve", "thread_id": thread_id}}}}

    out1 = handle_callback(cb)
    out2 = handle_callback(cb)          # duplicate

    assert out1["toast"]["type"] == "success"
    assert "已处理" in out2["toast"]["content"]      # second is deduped, not re-executed

    from ats.memory import get_store
    trades = get_store().recent_trades(limit=20)
    assert len(trades) == 2              # 2 orders from ONE execution, not 4
