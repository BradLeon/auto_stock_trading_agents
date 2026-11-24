"""Per-call LLM usage records, and the one fact a caller cannot see from the parsed object: truncation.

Why this exists (macro_strategist, 2026-11-12): every incomplete weekly review hit `max_tokens` exactly
(OpenRouter `native_finish_reason: length`), yet the response said `finish_reason: tool_calls` and carried a
closed JSON object holding only the parameters the model had finished. Pydantic defaults filled the rest, so a
truncated answer looked like a complete one. The relay rewrites the finish reason and langchain drops the native
one, so the reliable signal is the token count: OpenRouter reports usage in the model's native tokens, and output
that reaches the ceiling was cut off there.

The handler is attached to every model by `llm.gateway.get_model`, so no agent code changes to get the log line.
A caller that needs the verdict wraps its call in `capture_calls()`; the last record is the attempt whose answer
was used (earlier attempts of a `run_structured` retry returned nothing usable).

Every record is also written to the `llm_calls` ledger (ops-observability-dashboard): role, model, tokens, cost
and the workflow run it belongs to, so the dashboard can show what each run cost. The cost is the provider's own
figure when the response carries one (`cost_source="provider"`), else tokens × the configured unit price
(`"estimate"`), else unknown (NULL — never 0). Writing never raises and never sends a request.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

log = logging.getLogger("ats.llm.telemetry")

_ACTIVE: ContextVar[list[LLMCallRecord] | None] = ContextVar("ats_llm_calls", default=None)

# Finish reasons that mean "stopped by the output ceiling" across the OpenAI-compatible and Anthropic wire formats.
_LENGTH_REASONS = {"length", "max_tokens"}


@dataclass(frozen=True)
class LLMCallRecord:
    role: str
    response_id: str
    finish_reason: str
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    max_tokens: int | None
    model: str = ""
    provider: str = ""
    cost_usd: float | None = None          # the provider's own figure, when the response carried one

    @property
    def truncated(self) -> bool:
        if self.finish_reason in _LENGTH_REASONS:
            return True
        return bool(self.max_tokens and self.output_tokens is not None and self.output_tokens >= self.max_tokens)


@contextmanager
def capture_calls() -> Iterator[list[LLMCallRecord]]:
    """Collect the records of every model call made inside the block (this thread/task only)."""
    calls: list[LLMCallRecord] = []
    token = _ACTIVE.set(calls)
    try:
        yield calls
    finally:
        _ACTIVE.reset(token)


def record(call: LLMCallRecord) -> None:
    """Log one call and hand it to the active collector, if any."""
    log.info("llm %s: id=%s finish=%s in=%s out=%s reasoning=%s max=%s",
             call.role, call.response_id or "-", call.finish_reason or "-", call.input_tokens,
             call.output_tokens, call.reasoning_tokens, call.max_tokens)
    if call.truncated:
        log.warning("llm %s: output hit the max_tokens ceiling (%s/%s, id=%s) — the answer is truncated even if "
                    "it parsed", call.role, call.output_tokens, call.max_tokens, call.response_id or "-")
    calls = _ACTIVE.get()
    if calls is not None:
        calls.append(call)
    persist(call)


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #
_DDL = """CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, role TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', response_id TEXT NOT NULL DEFAULT '',
    finish_reason TEXT NOT NULL DEFAULT '', input_tokens INTEGER, output_tokens INTEGER,
    reasoning_tokens INTEGER, cost_usd REAL, cost_source TEXT NOT NULL DEFAULT '',
    workflow_run_id TEXT NOT NULL DEFAULT '', agent_run_id TEXT NOT NULL DEFAULT '',
    process TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_at ON llm_calls(at);"""
_READY: set[str] = set()
_READY_LOCK = threading.Lock()


def _ledger_path() -> str:
    from ..memory import _db_path

    return _db_path()


def _process_label() -> str:
    argv = " ".join(sys.argv[1:4])
    if " schedule" in f" {argv}":
        return "dispatcher"
    if "refresh" in argv or "ats.data.refresh" in " ".join(sys.argv):
        return "refresh"
    if "pytest" in sys.argv[0]:
        return "test"
    return ("cli " + argv)[:60].strip()


def _run_ids() -> tuple[str, str]:
    try:
        from ..agent.task_projection import _PROJECTION_CONTEXT

        ctx = _PROJECTION_CONTEXT.get() or {}
        return str(ctx.get("workflow_run_id") or ""), str(ctx.get("agent_run_id") or "")
    except Exception:  # noqa: BLE001
        return "", ""


def unit_cost(call: LLMCallRecord) -> float | None:
    """tokens × the configured price (USD per million tokens), or None without a price."""
    try:
        from ..config import get_config

        prices = getattr(get_config().app.llm, "prices", None) or {}
    except Exception:  # noqa: BLE001
        return None
    price = prices.get(call.model) if isinstance(prices, dict) else None
    if not price or call.input_tokens is None or call.output_tokens is None:
        return None
    price = price if isinstance(price, dict) else dict(price)
    # reasoning tokens are billed as output and already counted in output_tokens on both wires
    return (call.input_tokens * float(price.get("input", 0))
            + call.output_tokens * float(price.get("output", 0))) / 1_000_000


def persist(call: LLMCallRecord) -> None:
    """Append one call to the ledger. Never raises: a locked or missing database only logs."""
    try:
        from datetime import datetime, timezone

        cost, source = call.cost_usd, "provider"
        if cost is None:
            cost, source = unit_cost(call), "estimate"
        if cost is None:
            source = ""
        workflow_run_id, agent_run_id = _run_ids()
        path = _ledger_path()
        conn = sqlite3.connect(path, timeout=5)
        try:
            if path not in _READY:
                with _READY_LOCK:
                    conn.executescript(_DDL)
                    _READY.add(path)
            conn.execute(
                "INSERT INTO llm_calls (at, role, provider, model, response_id, finish_reason, input_tokens, "
                "output_tokens, reasoning_tokens, cost_usd, cost_source, workflow_run_id, agent_run_id, process) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), call.role, call.provider, call.model, call.response_id,
                 call.finish_reason, call.input_tokens, call.output_tokens, call.reasoning_tokens, cost, source,
                 workflow_run_id, agent_run_id, os.environ.get("ATS_PROCESS_LABEL") or _process_label()))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - the ledger must never fail a model call
        log.warning("llm %s: ledger write skipped: %s", call.role, exc)


def _first_message(response: Any):
    try:
        return response.generations[0][0]
    except (AttributeError, IndexError, TypeError):
        return None


class UsageCallback(BaseCallbackHandler):
    """Turns each finished chat call into an `LLMCallRecord`. Never raises: telemetry must not fail a call."""

    def __init__(self, role: str, max_tokens: int | None, *, model: str = "", provider: str = ""):
        self.role = role
        self.max_tokens = max_tokens
        self.model = model
        self.provider = provider

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            record(self._record(response))
        except Exception as exc:  # noqa: BLE001
            log.debug("llm %s: usage record skipped: %s", self.role, exc)

    def _record(self, response: Any) -> LLMCallRecord:
        generation = _first_message(response)
        message = getattr(generation, "message", None)
        info = getattr(generation, "generation_info", None) or {}
        meta = getattr(message, "response_metadata", None) or {}
        usage = getattr(message, "usage_metadata", None) or {}
        llm_output = getattr(response, "llm_output", None) or {}
        details = usage.get("output_token_details") or {}
        token_usage = meta.get("token_usage") or llm_output.get("token_usage") or {}
        cost = token_usage.get("cost") if isinstance(token_usage, dict) else None
        return LLMCallRecord(
            role=self.role,
            response_id=str(llm_output.get("id") or meta.get("id") or getattr(message, "id", "") or ""),
            finish_reason=str(info.get("finish_reason") or meta.get("finish_reason")
                              or meta.get("stop_reason") or ""),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            reasoning_tokens=details.get("reasoning"),
            max_tokens=self.max_tokens,
            model=str(meta.get("model_name") or self.model or ""),
            provider=self.provider,
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None)
