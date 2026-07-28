"""LLM-facing view for the weekly invalidation check (Stage B3)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class InvalidationView(BaseModel):
    """The only two things the LLM is allowed to produce here — a yes/no fact
    classification plus its citation, never a P&L-aware narrative (see
    invalidation.py's blind-context guard)."""

    triggered: bool = Field(
        description="预登记的失效条件（invalidation 原文）是否有确凿证据显示已经发生。"
                    "证据不足或存疑一律 False —— 宁可漏报，不可编造触发。")
    evidence: str = Field(
        default="",
        description="支持判断的具体依据：引用 invalidation 原文对应的观察，"
                    "或某条事件/纪要的一句话摘录。triggered=False 时可留空或说明"
                    "'期间无相关事件'。")
