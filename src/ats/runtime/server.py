"""Webhook server for async Boss approvals (Feishu card callbacks).

Receives Feishu `card.action.trigger` events, turns a button tap into a
BossApproval, and resumes the checkpointed cycle by thread_id. Run with
`ats serve`; expose it to Feishu via a public URL (tunnel in dev — see README).
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("ats.server")

# Serialize + dedupe cycle resumes. Feishu PREFETCHES approval links (link preview)
# and users may double-tap, so one approval can hit /feishu/approve several times
# concurrently. Without this, N concurrent resume_cycle calls (a) collide on the
# serve process's IBKR client_id (→ 326/1100, dropped orders) and (b) — worse — if
# the id ever became unique, would place the orders N times. The lock makes each
# cycle execute at most once and serializes executions within the serve process.
# Fail-closed: a failed resume is marked done (not auto-retried via the link) to
# avoid double orders after a partial fill — re-run chief for a fresh cycle instead.
_RESUME_LOCK = threading.Lock()
_RESUMED: dict[str, str] = {}


def _resume_once(thread_id: str, approval, channel, verdict: str) -> tuple[bool, str]:
    from .cli import resume_cycle

    with _RESUME_LOCK:
        if thread_id in _RESUMED:
            return True, f"{thread_id}: 已处理（{_RESUMED[thread_id]}）— 忽略重复请求"
        try:
            resume_cycle(thread_id, approval, channel=channel)
        except Exception as exc:  # noqa: BLE001 - never 500 back to Feishu
            _RESUMED[thread_id] = "failed"
            log.exception("resume failed for %s: %s", thread_id, exc)
            return False, f"resume failed: {exc}"
        _RESUMED[thread_id] = approval.status
    return (True, f"{thread_id}: {approval.status} — executing") if verdict == "approve" \
        else (True, f"{thread_id}: rejected")


def handle_callback(payload: dict) -> dict:
    """Pure handler: verify, parse, and (for approvals) resume the cycle.

    Returns the JSON body to send back to Feishu. Never raises — a failed resume
    becomes an error toast so Feishu does not retry-storm.
    """
    from ..channel.feishu_channel import FeishuChannel, parse_callback, verify_token

    if not verify_token(payload):
        log.warning("rejected callback: bad verification token")
        return {"code": -1, "msg": "invalid token"}

    parsed = parse_callback(payload)
    if parsed["kind"] == "challenge":
        return {"challenge": parsed["challenge"]}

    if parsed["kind"] == "approval":
        thread_id, approval = parsed["thread_id"], parsed["approval"]
        log.info("resuming %s -> %s by %s", thread_id, approval.status, approval.reviewer)
        verdict = "approve" if approval.status == "approved" else "reject"
        ok, msg = _resume_once(thread_id, approval, FeishuChannel(), verdict)
        return {"toast": {"type": "success" if ok else "error", "content": msg}}

    return {"code": 0}


def handle_approve(thread_id: str, verdict: str, sig: str) -> tuple[bool, str]:
    """Resolve a group-bot URL-button approval (signed GET). Returns (ok, message)."""
    from datetime import datetime, timezone

    from ..channel.feishu_bot import FeishuBotChannel, verify_approval
    from ..schemas.decision import BossApproval

    if verdict not in ("approve", "reject") or not thread_id:
        return False, "bad request"
    if not verify_approval(thread_id, verdict, sig):
        return False, "invalid signature"
    approval = BossApproval(status="approved" if verdict == "approve" else "rejected",
                            reviewer="feishu-bot", reviewed_at=datetime.now(timezone.utc))
    return _resume_once(thread_id, approval, FeishuBotChannel(), verdict)


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
