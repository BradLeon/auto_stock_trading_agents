"""Feishu (Lark) BossChannel — async approval via interactive card + webhook.

Flow (see DESIGN.md §11.2):
  run process  : cycle hits the interrupt → checkpoint to sqlite → send an
                 approval card carrying the thread_id → EXIT (no blocking wait).
  webhook proc : Boss taps Approve/Reject in Feishu → card.action.trigger
                 callback → parse_callback() → resume the graph by thread_id.

This adapter is "async": the runtime calls `send_approval_request` instead of a
blocking `request_approval`. Live use needs a Feishu app (app_id/secret), a chat
id, and a public callback URL (see README). All API calls degrade with a clear
error if credentials are missing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import get_config
from ..schemas.channel import ApprovalRequest, Notification, ReportBundle
from ..schemas.decision import BossApproval, TradeDecision

log = logging.getLogger("ats.channel.feishu")


class FeishuChannel:
    is_async = True
    kind = "feishu"

    def __init__(self) -> None:
        s = get_config().secrets
        self.app_id = s.feishu_app_id
        self.app_secret = s.feishu_app_secret
        self.chat_id = s.feishu_chat_id
        self.base_url = s.feishu_base_url.rstrip("/")
        self._token: tuple[str, datetime] | None = None  # (token, expiry)

    # --- auth ------------------------------------------------------------ #
    def _tenant_token(self) -> str:
        import httpx

        now = datetime.now(timezone.utc)
        if self._token and self._token[1] > now:
            return self._token[0]
        if not (self.app_id and self.app_secret):
            raise RuntimeError("Feishu app_id/app_secret not configured (.env)")
        r = httpx.post(f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
                       json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu auth failed: {data}")
        from datetime import timedelta

        token = data["tenant_access_token"]
        self._token = (token, now + timedelta(seconds=data.get("expire", 7200) - 120))
        return token

    # --- send ------------------------------------------------------------ #
    def _send(self, msg_type: str, content: dict, chat_id: str | None = None) -> None:
        import httpx

        receive_id = chat_id or self.chat_id
        if not receive_id:
            raise RuntimeError("Feishu chat_id not configured (.env FEISHU_CHAT_ID)")
        import json as _json

        r = httpx.post(
            f"{self.base_url}/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {self._tenant_token()}"},
            json={"receive_id": receive_id, "msg_type": msg_type, "content": _json.dumps(content)},
            timeout=15,
        )
        r.raise_for_status()
        if r.json().get("code") != 0:
            raise RuntimeError(f"Feishu send failed: {r.json()}")

    def push(self, msg: Notification) -> None:
        try:
            self._send("text", {"text": f"[{msg.kind}] {msg.title}\n{msg.body}"})
        except Exception as exc:  # noqa: BLE001 - notifications must not break the cycle
            log.warning("feishu push failed: %s", exc)

    def send_approval_request(self, req: ApprovalRequest, thread_id: str) -> None:
        """Send the interactive approval card. Async: returns immediately."""
        self._send("interactive", build_approval_card(req, thread_id))

    # --- context pull ---------------------------------------------------- #
    def fetch_report_context(self, query: str) -> ReportBundle:
        from .context import build_report_bundle

        return build_report_bundle(query)

    # request_approval is intentionally not the entry point for async channels;
    # provided for protocol completeness (raises to flag misuse).
    def request_approval(self, req: ApprovalRequest) -> BossApproval:  # pragma: no cover
        raise NotImplementedError("FeishuChannel is async; use send_approval_request + webhook")


# --------------------------------------------------------------------------- #
# Card construction (pure)
# --------------------------------------------------------------------------- #
def _decision_line(d: TradeDecision) -> str:
    size = (f"${d.notional_usd:,.0f}" if d.notional_usd
            else f"{d.qty} sh" if d.qty
            else f"target {d.target_weight:.0%}" if d.target_weight is not None else "?")
    px = f"@ {d.limit_price}" if d.limit_price else "(mkt)"
    return f"**{d.action.upper()} {d.symbol}** {size} {px} · conv {d.conviction:.2f}\n{d.rationale}"


def build_approval_card(req: ApprovalRequest, thread_id: str) -> dict:
    """Feishu interactive card with Approve / Reject buttons carrying the thread_id."""
    body = req.context_summary or ""
    decisions_md = "\n\n".join(_decision_line(d) for d in req.decisions) or "_No trades proposed._"
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text", "content": f"Approval — {req.cycle_id}"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body[:1500]}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": decisions_md[:3000]}},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "✅ Approve"},
                 "type": "primary",
                 "value": {"action": "approve", "thread_id": thread_id}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "❌ Reject"},
                 "type": "danger",
                 "value": {"action": "reject", "thread_id": thread_id}},
            ]},
        ],
    }


# --------------------------------------------------------------------------- #
# Callback parsing (pure, unit-tested)
# --------------------------------------------------------------------------- #
def parse_callback(payload: dict) -> dict:
    """Interpret a Feishu webhook body.

    Returns one of:
      {"kind": "challenge", "challenge": "..."}      url verification handshake
      {"kind": "approval", "thread_id": ..., "approval": BossApproval}
      {"kind": "ignore"}                              anything else
    """
    # URL verification handshake (event subscription setup).
    if payload.get("type") == "url_verification" and "challenge" in payload:
        return {"kind": "challenge", "challenge": payload["challenge"]}

    # Card action callback (schema 2.0: card.action.trigger).
    event = payload.get("event", payload)
    action = event.get("action") or {}
    value = action.get("value") or {}
    if isinstance(value, str):
        import json as _json

        try:
            value = _json.loads(value)
        except Exception:  # noqa: BLE001
            value = {}

    verdict = value.get("action")
    thread_id = value.get("thread_id")
    if verdict in ("approve", "reject") and thread_id:
        operator = (event.get("operator") or {}).get("open_id", "feishu")
        approval = BossApproval(
            status="approved" if verdict == "approve" else "rejected",
            reviewer=operator,
            reviewed_at=None,  # stamped on resume
        )
        return {"kind": "approval", "thread_id": thread_id, "approval": approval}

    return {"kind": "ignore"}


def verify_token(payload: dict) -> bool:
    """Check the event verification token if one is configured."""
    expected = get_config().secrets.feishu_verification_token
    if not expected:
        return True  # not configured -> accept (note in README to harden)
    token = payload.get("token") or (payload.get("header") or {}).get("token")
    return token == expected
