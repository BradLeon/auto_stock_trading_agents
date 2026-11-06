"""A Boss revise note is followed unless it would make the orders raise risk; a breach left standing is not a conflict."""
from datetime import UTC, datetime

from ats.agents.chief import decide
from ats.schemas.decision import TradeDecision


def test_the_revise_prompt_puts_the_boss_note_first_and_only_excepts_orders_that_worsen_risk(monkeypatch):
    seen = {}

    def fake(context_text, **kw):
        seen["context"] = context_text
        return decide.ChiefResult(cycle_id="c", as_of=datetime.now(UTC))

    monkeypatch.setattr(decide, "from_context", fake)
    decide.revise_with_note(
        "CTX", prior_summary="上一版", note="只处理 1-3, 4-7 保留原样", cycle_id="c", as_of=datetime.now(UTC),
        prior_decisions=[TradeDecision(symbol="AXTI", action="sell", qty=100)])
    text = seen["context"]
    assert "修改意见（Boss 原话）：只处理 1-3, 4-7 保留原样" in text
    assert "Boss 的意见优先，必须照做" in text
    assert "使风险变坏" in text and "仍然超限，不算冲突" in text
    assert "以风控为准" not in text                      # the old rule that let the Chief overrule the Boss
