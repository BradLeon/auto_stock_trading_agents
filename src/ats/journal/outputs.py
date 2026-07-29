"""LLM-facing views for the journal's LLM-touching stages: the weekly invalidation
check (Stage B3) and the quarterly critic agent (Stage E)."""

from __future__ import annotations

from typing import Literal

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


# --------------------------------------------------------------------------- #
# Stage E — critic agent
# --------------------------------------------------------------------------- #
class ProposedChangeView(BaseModel):
    """Only render this if you can point at something that actually appeared in the
    context above — a specific config key, a specific number. If you can't name the
    exact file/key, leave the whole ProposedChange out of the finding rather than
    inventing a locator; a vague suggestion cannot be adopted anyway (see
    CriticFinding.proposed_change: 必须具体到"改哪个文件的哪个值"，否则无法采纳)."""

    target: Literal["config", "skill", "playbook"] = Field(
        description="改动对象类型：配置文件 / skill 提示词 / 操作手册")
    locator: str = Field(description='具体改哪里，如 "config/pead/COHR.yaml: long_threshold"')
    current: str = Field(description="现在的值（照抄上下文里出现过的原始值，不要臆造）")
    proposed: str = Field(description="建议改成的值")
    expected_effect: str = Field(
        description="预期效果，必须带 n，如'已观测的 14 次中会多捕获 3 次，少踩 1 次'")


class FindingItemView(BaseModel):
    """The only part of a CriticFinding the LLM is allowed to author. `observation`/
    `n`/`evidence_ref` are filled by deterministic code before this is ever called —
    you are given the observation as read-only context, not asked to restate it."""

    hypothesis: str = Field(
        description="为什么会这样——一句话假设，明确承认可能错，不要用断言式口吻")
    falsifier: str = Field(
        description="什么样的后续观察会推翻这条假设——必须具体到可观察的事，不能是"
                    "'如果情况变了'这种空话")
    proposed_change: ProposedChangeView | None = Field(
        default=None, description="仅当能指向上下文里出现过的具体配置项时才填，否则留空")
    confidence: Literal["强", "弱", "仅供观察"] = Field(
        default="仅供观察", description="这条假设本身有多可信——多数情况下证据有限，"
                                   "默认'仅供观察'，只有反复出现的强模式才用'强'")
