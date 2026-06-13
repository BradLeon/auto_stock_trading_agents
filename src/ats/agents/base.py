"""Agent base: load a role's SKILL.md and run the LLM with structured output."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..llm import get_model

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

T = TypeVar("T", bound=BaseModel)


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
    return model.invoke([SystemMessage(content=system), HumanMessage(content=context)])
