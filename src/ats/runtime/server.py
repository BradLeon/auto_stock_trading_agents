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


def build_app():
    from fastapi import FastAPI, Request

    app = FastAPI(title="ats approval webhook")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/feishu/callback")
    async def feishu_callback(request: Request):
        return handle_callback(await request.json())

    return app


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(build_app(), host=host, port=port)
