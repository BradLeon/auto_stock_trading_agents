"""Feishu group-bot channel: card buttons, HMAC sign/verify, approve→resume."""

from datetime import datetime, timezone

from ats.channel import feishu_bot
from ats.schemas.channel import ApprovalRequest
from ats.schemas.decision import TradeDecision

NOW = datetime.now(timezone.utc)


def test_sign_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("FEISHU_APPROVE_SECRET", "s3cret")
    from ats.config import reset_config_cache
    reset_config_cache()
    sig = feishu_bot.sign_approval("pead:COHR:QFY2026", "approve", "s3cret")
    assert feishu_bot.verify_approval("pead:COHR:QFY2026", "approve", sig) is True
    assert feishu_bot.verify_approval("pead:COHR:QFY2026", "reject", sig) is False  # wrong verdict
    reset_config_cache()


def test_card_has_url_buttons_when_configured(monkeypatch):
    monkeypatch.setenv("FEISHU_BOT_WEBHOOK", "http://hook")
    monkeypatch.setenv("FEISHU_APPROVE_BASE", "https://x.ngrok.app")
    monkeypatch.setenv("FEISHU_APPROVE_SECRET", "s3cret")
    from ats.config import reset_config_cache
    reset_config_cache()

    ch = feishu_bot.FeishuBotChannel()
    req = ApprovalRequest(cycle_id="pead-COHR-QFY2026", as_of=NOW,
                          decisions=[TradeDecision(symbol="COHR", action="buy", notional_usd=3000)])
    card = ch._approval_card(req, "pead:COHR:QFY2026")
    actions = [e for e in card["elements"] if e["tag"] == "action"][0]["actions"]
    urls = [a["url"] for a in actions]
    assert any("verdict=approve" in u and "sig=" in u for u in urls)
    assert any("verdict=reject" in u for u in urls)
    reset_config_cache()


def test_handle_approve_resumes(monkeypatch):
    monkeypatch.setenv("FEISHU_APPROVE_SECRET", "s3cret")
    from ats.config import reset_config_cache
    reset_config_cache()

    from ats.runtime import cli, server
    calls = {}
    monkeypatch.setattr(cli, "resume_cycle",
                        lambda tid, appr, channel=None: calls.update(tid=tid, status=appr.status))

    sig = feishu_bot.sign_approval("pead:COHR:QFY2026", "approve", "s3cret")
    ok, msg = server.handle_approve("pead:COHR:QFY2026", "approve", sig)
    assert ok and calls == {"tid": "pead:COHR:QFY2026", "status": "approved"}

    bad_ok, _ = server.handle_approve("pead:COHR:QFY2026", "approve", "tampered")
    assert bad_ok is False         # bad signature rejected
    reset_config_cache()
