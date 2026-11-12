"""One request pace for every process that talks to sec.gov.

SEC fair access allows 10 requests per second per client and answers an excess with 403 ("Request Rate Threshold
Exceeded") or 429, then blocks the IP for a while. Several jobs reach SEC (filing discovery, filing bodies, Company
Facts, issuer 6-K exhibits) from separate processes, so a per-process sleep does not bound the total. This module keeps
the time of the last request in a file under an exclusive lock: every caller waits for the shared slot, which caps the
machine at `ATS_SEC_MAX_RPS` (default 4, well below the limit). A throttling answer starts a cooldown that every process
honours: `wait()` raises `SECRateLimited` until it has passed, so nothing keeps knocking while SEC is blocking us.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

from ..config import REPO_ROOT

DEFAULT_MAX_RPS = 4.0
COOLDOWN_SECONDS = 600
_THROTTLE_MARKERS = (b"request rate threshold", b"undeclared automated tool")


class SECRateLimited(RuntimeError):
    """SEC throttled this machine recently; no request may be sent before the cooldown ends."""


def _state_path() -> Path:
    return Path(os.environ.get("ATS_SEC_RATE_STATE") or REPO_ROOT / "var/run/sec_rate.json")


def _interval() -> float:
    try:
        rps = float(os.environ.get("ATS_SEC_MAX_RPS") or DEFAULT_MAX_RPS)
    except ValueError:
        rps = DEFAULT_MAX_RPS
    return 1.0 / min(max(rps, 0.1), 8.0)       # never closer to the 10/s limit than 8/s, whatever the setting


def _locked(update):
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.seek(0)
            try:
                state = json.loads(handle.read() or "{}")
            except ValueError:
                state = {}
            result = update(state)
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(state))
            handle.flush()
            return result
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def wait(*, sleep=time.sleep, clock=time.time) -> None:
    """Block until this process may send one SEC request; raise while a throttle cooldown is running."""
    def take(state: dict) -> float:
        now = clock()
        blocked_until = float(state.get("blocked_until") or 0)
        if now < blocked_until:
            raise SECRateLimited(f"sec_rate_cooldown_until:{blocked_until:.0f}")
        slot = max(now, float(state.get("next_slot") or 0))
        state["next_slot"] = slot + _interval()
        return slot - now

    delay = _locked(take)
    if delay > 0:
        sleep(delay)


def is_throttled(status_code: int, body: bytes = b"") -> bool:
    if status_code == 429:
        return True
    return status_code == 403 and any(marker in body[:4096].lower() for marker in _THROTTLE_MARKERS)


def observe(status_code: int, body: bytes = b"", *, clock=time.time) -> bool:
    """Record a throttling answer (start the shared cooldown); return whether it was one."""
    if not is_throttled(status_code, body):
        return False

    def block(state: dict) -> None:
        state["blocked_until"] = max(float(state.get("blocked_until") or 0), clock() + COOLDOWN_SECONDS)

    _locked(block)
    return True
