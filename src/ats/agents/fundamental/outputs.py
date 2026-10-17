"""例行模式的 LLM 输出 schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClassificationItem(BaseModel):
    index: int
    classification: str = ""
    reason: str = ""          # at most 30 characters; long free text is what ran the output away (2026-10-16)


class ClassificationView(BaseModel):
    """每条事实变化一个归类标签（确认/否定/新增/待验证）。"""

    items: list[ClassificationItem] = Field(default_factory=list)
