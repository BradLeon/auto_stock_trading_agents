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
"""

from __future__ import annotations

import logging
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


def _first_message(response: Any):
    try:
        return response.generations[0][0]
    except (AttributeError, IndexError, TypeError):
        return None


class UsageCallback(BaseCallbackHandler):
    """Turns each finished chat call into an `LLMCallRecord`. Never raises: telemetry must not fail a call."""

    def __init__(self, role: str, max_tokens: int | None):
        self.role = role
        self.max_tokens = max_tokens

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
        return LLMCallRecord(
            role=self.role,
            response_id=str(llm_output.get("id") or meta.get("id") or getattr(message, "id", "") or ""),
            finish_reason=str(info.get("finish_reason") or meta.get("finish_reason")
                              or meta.get("stop_reason") or ""),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            reasoning_tokens=details.get("reasoning"),
            max_tokens=self.max_tokens)
