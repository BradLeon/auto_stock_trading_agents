"""Feishu group custom-bot channel (incoming webhook + URL-button approval).

A group custom bot is one-way: it can PUSH cards/messages into the group but
cannot receive native button callbacks (that needs a full app). So approval uses
URL buttons: each button links to a signed GET endpoint on `ats serve`
(`/feishu/approve`), which resumes the checkpointed run. Push needs only the
webhook; approval also needs a public base URL (tunnel) + an HMAC secret.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

from ..config import get_config
from ..schemas.channel import ApprovalRequest, Notification, ReportBundle
from ..schemas.decision import BossApproval, TradeDecision

log = logging.getLogger("ats.channel.feishu_bot")


def sign_approval(thread_id: str, verdict: str, secret: str) -> str:
    msg = f"{thread_id}:{verdict}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]


def verify_approval(thread_id: str, verdict: str, sig: str) -> bool:
    secret = get_config().secrets.feishu_approve_secret
    if not secret:
        return False  # refuse approvals if no secret configured
    return hmac.compare_digest(sign_approval(thread_id, verdict, secret), sig or "")


class FeishuBotChannel:
    is_async = True
    kind = "feishu_bot"

    def __init__(self) -> None:
        s = get_config().secrets
        self.webhook = s.feishu_bot_webhook
        self.secret = s.feishu_bot_secret
        self.approve_base = s.feishu_approve_base.rstrip("/")
        self.approve_secret = s.feishu_approve_secret

    # --- transport ------------------------------------------------------- #
    def _post(self, payload: dict) -> None:
        import httpx

        if not self.webhook:
            raise RuntimeError("FEISHU_BOT_WEBHOOK not configured")
        if self.secret:  # optional 签名校验
            ts = str(int(time.time()))
            sign = hmac.new(f"{ts}\n{self.secret}".encode(), b"", hashlib.sha256).digest()
            import base64

            payload = {**payload, "timestamp": ts, "sign": base64.b64encode(sign).decode()}
        r = httpx.post(self.webhook, json=payload, timeout=15)
        r.raise_for_status()
        body = r.json()
        if body.get("StatusCode") not in (0, None) and body.get("code") not in (0, None):
            raise RuntimeError(f"feishu bot send failed: {body}")

    def push(self, msg: Notification) -> None:
        try:
            self._post({"msg_type": "text",
                        "content": {"text": f"[{msg.kind}] {msg.title}\n{msg.body}"}})
        except Exception as exc:  # noqa: BLE001 - notifications must not break the cycle
            log.warning("feishu bot push failed: %s", exc)

    def send_approval_request(self, req: ApprovalRequest, thread_id: str) -> None:
        self._post({"msg_type": "interactive", "card": self._approval_card(req, thread_id)})

    def fetch_report_context(self, query: str) -> ReportBundle:
        from .context import build_report_bundle

        return build_report_bundle(query)

    def request_approval(self, req: ApprovalRequest) -> BossApproval:  # pragma: no cover
        raise NotImplementedError("FeishuBotChannel is async; approval is via URL buttons + webhook")

    # --- card ------------------------------------------------------------ #
    def _approve_url(self, thread_id: str, verdict: str) -> str:
        q = urlencode({"thread_id": thread_id, "verdict": verdict,
                       "sig": sign_approval(thread_id, verdict, self.approve_secret)})
        return f"{self.approve_base}/feishu/approve?{q}"

    def _approval_card(self, req: ApprovalRequest, thread_id: str) -> dict:
        decisions = "\n\n".join(_decision_line(d) for d in req.decisions) or "_No trades proposed._"
        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": (req.context_summary or "")[:1500]}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": decisions[:3000]}},
        ]
        if self.approve_base and self.approve_secret:
            elements.append({"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "✅ Approve"},
                 "type": "primary", "url": self._approve_url(thread_id, "approve")},
                {"tag": "button", "text": {"tag": "plain_text", "content": "❌ Reject"},
                 "type": "danger", "url": self._approve_url(thread_id, "reject")},
            ]})
        else:
            elements.append({"tag": "note", "elements": [{"tag": "plain_text",
                "content": "审批按钮未启用：配置 FEISHU_APPROVE_BASE + FEISHU_APPROVE_SECRET 并运行 ats serve"}]})
        return {"config": {"wide_screen_mode": True},
                "header": {"template": "blue",
                           "title": {"tag": "plain_text", "content": f"Approval — {req.cycle_id}"}},
                "elements": elements}


def _decision_line(d: TradeDecision) -> str:
    size = (f"${d.notional_usd:,.0f}" if d.notional_usd
            else f"{d.qty} sh" if d.qty
            else f"target {d.target_weight:.0%}" if d.target_weight is not None else "?")
    px = f"@ {d.limit_price}" if d.limit_price else "(mkt)"
    return f"**{d.action.upper()} {d.symbol}** {size} {px} · conv {d.conviction:.2f}\n{d.rationale}"
