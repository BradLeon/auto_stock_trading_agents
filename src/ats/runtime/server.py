"""Webhook server for async Boss approvals (Feishu card callbacks).

Receives Feishu `card.action.trigger` events, turns a button tap into a
BossApproval, and resumes the checkpointed cycle by thread_id. Run with
`ats serve`; expose it to Feishu via a public URL (tunnel in dev — see README).
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.server")


def handle_callback(payload: dict) -> dict:
    """Pure handler: verify, parse, and (for approvals) resume the cycle.

    Returns the JSON body to send back to Feishu. Never raises — a failed resume
    becomes an error toast so Feishu does not retry-storm.
    """
    from ..channel.feishu_channel import FeishuChannel, parse_callback, verify_token
    from .cli import resume_cycle

    if not verify_token(payload):
        log.warning("rejected callback: bad verification token")
        return {"code": -1, "msg": "invalid token"}

    parsed = parse_callback(payload)
    if parsed["kind"] == "challenge":
        return {"challenge": parsed["challenge"]}

    if parsed["kind"] == "approval":
        thread_id, approval = parsed["thread_id"], parsed["approval"]
        log.info("resuming %s -> %s by %s", thread_id, approval.status, approval.reviewer)
        try:
            resume_cycle(thread_id, approval, channel=FeishuChannel())
        except Exception as exc:  # noqa: BLE001 - never 500 back to Feishu
            log.exception("resume failed for %s: %s", thread_id, exc)
            return {"toast": {"type": "error", "content": f"resume failed: {exc}"}}
        verb = "approved — executing" if approval.status == "approved" else "rejected"
        return {"toast": {"type": "success", "content": verb}}

    return {"code": 0}


def handle_approve(thread_id: str, verdict: str, sig: str) -> tuple[bool, str]:
    """Resolve a group-bot URL-button approval (signed GET). Returns (ok, message)."""
    from datetime import datetime, timezone

    from ..channel.feishu_bot import FeishuBotChannel, verify_approval
    from ..schemas.decision import BossApproval
    from .cli import resume_cycle

    if verdict not in ("approve", "reject") or not thread_id:
        return False, "bad request"
    if not verify_approval(thread_id, verdict, sig):
        return False, "invalid signature"
    approval = BossApproval(status="approved" if verdict == "approve" else "rejected",
                            reviewer="feishu-bot", reviewed_at=datetime.now(timezone.utc))
    try:
        resume_cycle(thread_id, approval, channel=FeishuBotChannel())
    except Exception as exc:  # noqa: BLE001
        log.exception("approve resume failed for %s: %s", thread_id, exc)
        return False, f"resume failed: {exc}"
    return True, f"{thread_id}: {approval.status} — executing" if verdict == "approve" \
        else f"{thread_id}: rejected"


def build_app():
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="ats approval webhook")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/feishu/callback")
    async def feishu_callback(request: Request):
        return handle_callback(await request.json())

    @app.get("/feishu/approve", response_class=HTMLResponse)
    def feishu_approve(thread_id: str = "", verdict: str = "", sig: str = ""):
        ok, msg = handle_approve(thread_id, verdict, sig)
        color = "#1f883d" if ok else "#cf222e"
        return f"<html><body style='font-family:sans-serif;text-align:center;padding:40px'>" \
               f"<h2 style='color:{color}'>{'✅' if ok else '⚠️'} {msg}</h2>" \
               f"<p>You can close this page.</p></body></html>"

    return app


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(build_app(), host=host, port=port)
