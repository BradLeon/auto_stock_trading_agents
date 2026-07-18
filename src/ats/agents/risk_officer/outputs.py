"""LLM-facing view for the risk-officer memo (no min/max — figures come from the
deterministic engine, the LLM only narrates)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return v


class LayerConclusionView(BaseModel):
    layer: str = Field(description="层名，如 L1 单票/止损、L2 杠杆/现金、L3 beta/相关簇、L5 压测")
    conclusion: str = Field(default="", description="一句结论：是否可放心 / 需关注什么，引确定性数值")


class RiskMemoLLMView(BaseModel):
    assessment: str = Field(description="5-10 行总评：当前组合风险画像、是否可继续建仓/需 de-risk")
    cash_equivalent_read: str = Field(
        default="", description="现金等价物解读：SGOV/SHV/BRK-B 折算后真实可用弹药 vs 账户现金")
    layer_conclusions: list[LayerConclusionView] = Field(
        default_factory=list, description="逐层结论（L1..L6），每层一句是否可放心")
    headroom: str = Field(default="", description="距各硬限额（单票/杠杆/现金地板/beta/压测）的余量判断")
    recommended_actions: list[str] = Field(
        default_factory=list, description="可操作建议，按优先级；无破限则给维持/观察项")
    top_risks: list[str] = Field(default_factory=list, description="最值得盯的 2-4 个风险点")

    @field_validator("recommended_actions", "top_risks", mode="before")
    @classmethod
    def _c(cls, v):
        return _as_list(v)
