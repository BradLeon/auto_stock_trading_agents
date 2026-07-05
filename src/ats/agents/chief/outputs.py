"""LLM-facing view for the Chief's decision output."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..outputs import DecisionView


class ChiefOutput(BaseModel):
    summary: str = Field(description="overall stance + how the artifacts were weighed")
    decisions: list[DecisionView] = Field(default_factory=list)
