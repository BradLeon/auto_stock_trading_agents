"""Agent base: load a role's SKILL.md and run the LLM with structured output."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..llm import get_model

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger("ats.agents.base")

# OpenRouter's OpenAI-compat relay occasionally mangles Anthropic tool calls:
# the first parameter's value swallows the rest of the call as literal
# `</parameter>\n<parameter name="...">...` text, and those later parameters
# come back as their schema defaults. Detect and re-parse.
_PARAM_LEAK_RE = re.compile(r'<parameter name="(\w+)">\s*(.*?)\s*(?:</parameter>|\Z)', re.S)


def _repair_param_leak(obj: T, schema: type[T]) -> T:
    data = obj.model_dump()
    leaked = next((k for k, v in data.items()
                   if isinstance(v, str) and "</parameter>" in v), None)
    if leaked is None:
        return obj
    head, _, tail = data[leaked].partition("</parameter>")
    data[leaked] = head.strip()
    for m in _PARAM_LEAK_RE.finditer(tail):
        name, raw = m.group(1), m.group(2)
        if name not in schema.model_fields:
            continue
        try:
            data[name] = json.loads(raw)
        except ValueError:
            data[name] = raw
    log.warning("repaired leaked tool-call parameters in %s output", schema.__name__)
    return schema.model_validate(data)


@lru_cache(maxsize=None)
def load_skill(slug: str) -> str:
    """Return the SKILL.md body for a role, or '' if none exists yet."""
    path = SKILLS_DIR / slug / "SKILL.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def run_structured(role: str, schema: type[T], context: str, *, skill_slug: str | None = None) -> T:
    """Invoke the role's model, forcing output into `schema`.

    `role` selects the model (llm.gateway routing); `skill_slug` selects the
    SKILL.md system prompt (defaults to role). The model is bound to the schema
    via tool-calling, so the return value is already a validated instance.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    system = load_skill(skill_slug or role) or f"You are the {role}. Be rigorous and concise."
    # Tool-calling is the most portable structured-output method across the
    # providers OpenRouter fronts (Anthropic/OpenAI/Bedrock differ on json_schema).
    model = get_model(role).with_structured_output(schema, method="function_calling")
    messages = [SystemMessage(content=system), HumanMessage(content=context)]
    # Some providers (observed: gemini-2.5-pro via OpenRouter on large contexts)
    # intermittently return an empty tool call → None. It's flaky, not deterministic,
    # so retry a few times before failing loudly — otherwise callers silently degrade
    # on a cryptic 'NoneType has no model_dump' (and, e.g., serve a stale review).
    out = None
    for _ in range(4):
        out = model.invoke(messages)
        if out is not None:
            break
    if out is None:
        raise RuntimeError(
            f"structured output empty for role={role!r} schema={schema.__name__} "
            f"(context {len(context)} chars) after retries — provider returned no tool call")
    return _repair_param_leak(out, schema)
