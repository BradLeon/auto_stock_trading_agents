"""Webhook server for async Boss approvals (Feishu card callbacks).

Receives Feishu `card.action.trigger` events, turns a button tap into a
BossApproval, and resumes the checkpointed cycle by thread_id. Run with
`ats serve`; expose it to Feishu via a public URL (tunnel in dev — see README).
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("ats.server")

# Serialize cycle resumes. Feishu PREFETCHES approval links (link preview) and
# users may double-tap, so one approval can hit /feishu/approve several times
# concurrently. Without this, N concurrent resume_cycle calls (a) collide on the
# serve process's IBKR client_id (→ 326/1100, dropped orders) and (b) — worse —
# if the id ever became unique, would place the orders N times. The lock makes
# each cycle execute at most once per process and serializes executions.
#
# DEDUP IS PERSISTENT (task 6.3, design D3): the in-process `_RESUMED` map is
# gone. Dedup asks the decision audit store — terminal cycle / already-recorded
# approval for (revision, hash, channel) — so a duplicate callback after a
# process restart is still recognized. Replay safety for a crash mid-resume is
# carried by the graph itself: revisions dedup on content hash, approvals on
# their persistent idempotency key, and terminal transitions are journaled.
_RESUME_LOCK = threading.Lock()


def _resume_once(thread_id: str, approval, channel, verdict: str,
                 revision_no: int | None = None,
                 decision_hash: str | None = None) -> tuple[bool, str]:
    from ..decision.repository import DecisionAuditRepository
    from ..memory import get_store
    from .cli import resume_cycle

    with _RESUME_LOCK:
        try:
            repo = DecisionAuditRepository(get_store())
            ok, reason, deduped = repo.validate_callback(
                thread_id, revision_no=revision_no, decision_hash=decision_hash,
                channel=approval.channel)
        except Exception as exc:  # noqa: BLE001 - guard failure must not brick the card
            log.warning("callback guard failed for %s: %s", thread_id, exc)
            ok, reason, deduped = True, "", False
        if not ok:
            if deduped:
                return True, f"{thread_id}: 已处理 — 忽略重复请求"
            return False, f"{thread_id}: 回调被拒绝 — {reason}"
        try:
            resume_cycle(thread_id, approval, channel=channel)
        except Exception as exc:  # noqa: BLE001 - never 500 back to Feishu
            log.exception("resume failed for %s: %s", thread_id, exc)
            return False, f"resume failed: {exc}"
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
        ok, msg = _resume_once(thread_id, approval, FeishuChannel(), verdict,
                               revision_no=parsed.get("revision_no"),
                               decision_hash=parsed.get("decision_hash"))
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
                            reviewer="feishu-bot", reviewed_at=datetime.now(timezone.utc),
                            channel="feishu-bot")
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
    from uvicorn.config import LOGGING_CONFIG

    # Timestamp uvicorn's default + access logs (serve.out.log) to match ats logs.
    ts = "%Y-%m-%d %H:%M:%S"
    LOGGING_CONFIG["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    LOGGING_CONFIG["formatters"]["default"]["datefmt"] = ts
    LOGGING_CONFIG["formatters"]["access"]["fmt"] = (
        '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s')
    LOGGING_CONFIG["formatters"]["access"]["datefmt"] = ts
    uvicorn.run(build_app(), host=host, port=port, log_config=LOGGING_CONFIG)
