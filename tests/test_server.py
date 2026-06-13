"""Phase 9: webhook callback handler — challenge + end-to-end resume."""

from ats.runtime.cli import run_cycle
from ats.runtime.server import handle_callback


def test_url_verification_challenge():
    out = handle_callback({"type": "url_verification", "challenge": "xyz"})
    assert out == {"challenge": "xyz"}


def test_card_action_resumes_and_executes(async_channel):
    # 1) Pause a cycle at the interrupt (checkpointed to the per-test sqlite).
    run_cycle(dry_run=True, offline=True, use_llm=False, channel=async_channel)
    thread_id = async_channel.thread_id

    # 2) Simulate the Feishu approve callback reaching the webhook handler.
    out = handle_callback({"event": {"operator": {"open_id": "ou_boss"},
                                     "action": {"value": {"action": "approve",
                                                          "thread_id": thread_id}}}})
    assert out["toast"]["type"] == "success"

    # 3) The cycle ran to completion -> trades landed in Context Memory.
    from ats.memory import get_store

    trades = get_store().recent_trades(limit=10)
    assert len(trades) == 3 and all(t["status"] == "filled" for t in trades)
