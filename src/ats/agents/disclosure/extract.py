"""One model call: copy one declared metric's printed values out of filing passages (checked by code afterwards)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..base import run_structured


class Reading(BaseModel):
    passage: int = Field(description="引用原文所在段落的编号")
    quote: str = Field(description="从段落逐字复制的句子或表格行，必须包含该数字和指标措辞")
    value_text: str = Field(description="原文印出的数字本身，例如 13.8 或 319,993，不带单位词")
    scale: Literal["none", "thousand", "million", "billion", "trillion"] = "none"
    scale_quote: str = Field(default="", description="数量级来自表头时，逐字复制含 in millions 等字样的片段")
    period_end: str = Field(description="覆盖期间的截止日 YYYY-MM-DD")
    period_basis: Literal["instant", "quarter", "ytd_6m", "ytd_9m", "annual", "trailing_12m"]
    period_quote: str = Field(default="", description="quote 里没有期间时，逐字复制写明期间的片段")
    header_quote: str = Field(default="", description="quote 是表格行时，逐字复制列标题那一段")
    column_index: int = Field(default=-1, description="quote 是表格行时，该数字是行内第几个数（从 0 起）")
    qualifier: str = Field(default="", description="原文的限定词，例如 approximately、more than")
    bucket: str = Field(default="", description="分维度指标的维度取值，照抄原文，例如 2027、Thereafter")


class DisclosureExtraction(BaseModel):
    disclosed: bool = Field(description="段落是否直接披露了这个指标")
    readings: list[Reading] = Field(default_factory=list)
    note: str = Field(default="", description="一句话说明，例如未披露时文件实际给出的是什么口径")


def extract(context: str) -> dict:
    return run_structured("disclosure_extract", DisclosureExtraction, context,
                          skill_slug="disclosure-extract").model_dump()
