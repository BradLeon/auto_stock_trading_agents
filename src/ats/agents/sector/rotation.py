"""Cross-layer rotation — the one judgement that cannot be made inside a single layer.

Each layer analyst sees only its own evidence, so none of them can notice that L6's
tightness is L7's packaging schedule showing up one link downstream. That comparison
needs every layer's conclusion in one context, which is why this stage survives the
split into per-layer reviews (docs/SECTOR_ANALYST.md: "层间轮动本质是跨层比较").

What changed is the INPUT: eight compact verdicts instead of the whole sector's raw
material — and no macro (design D16).
"""

from __future__ import annotations

import logging

from ...schemas.sector import LayerVerdict, SectorConfig
from ..base import run_structured
from .outputs import LayerRotationView

log = logging.getLogger("ats.agents.sector.rotation")


def build_context(cfg: SectorConfig, verdicts: list[LayerVerdict]) -> str:
    """Eight verdicts, in chain order, plus an explicit note of who is missing."""
    by_key = {v.layer_key: v for v in verdicts}
    parts = [
        f"# {cfg.label} 跨层轮动（需求沿 L1→L{len(cfg.layers)} 传导）",
        "下面每一层的配置结论**已经产出**，由该层自己的证据支撑。"
        "你的任务是跨层比较，**不要重新判断任何一层**。",
    ]
    missing: list[str] = []
    for layer in cfg.layers:
        v = by_key.get(layer.key)
        if v is None:
            missing.append(layer.key)
            continue
        block = [
            f"## {layer.label}  [layer key = {layer.key}]",
            f"配置：**{v.allocation}**（confidence {v.confidence:.2f}）"
            f" · 周期：{v.cycle_position or '—'}",
        ]
        if not v.has_claims:
            block.append("> ⚠️ 本层**无命题**（配置缺口），结论仅来自快照与判据笔记——"
                         "把它当作「还没测过」，不是「测过且中性」。")
        if not v.cross_section_applicable:
            block.append("> ⚠️ 本层截面不适用（可比样本 <2），没有层内排序信息。")
        if v.claim_attributions:
            block.append("议题归因：\n" + "\n".join(f"  - {a}" for a in v.claim_attributions))
        if v.reversal_triggers:
            block.append("反转触发条件：\n" + "\n".join(f"  - {t}" for t in v.reversal_triggers))
        if v.rationale:
            block.append(f"本层总评：{v.rationale}")
        parts.append("\n".join(block))

    if missing:
        # Named, not silently dropped: a rotation call that leans on a layer nobody
        # assessed this round has to say so, or it reads as though it had evidence.
        parts.append("## ⚠️ 本轮缺失结论的层\n"
                     + "、".join(missing)
                     + "\n照常给轮动建议，但**任何涉及这些层的加减建议必须标注证据不足**，"
                       "并把它们的层键回填到 missing_layers。")
    return "\n\n".join(parts)


def run(cfg: SectorConfig, verdicts: list[LayerVerdict], *,
        use_llm: bool = True) -> LayerRotationView | None:
    """Returns None on failure — each layer verdict is already persisted on its own, so
    a failed rotation costs the report a section, not the week's work."""
    if not verdicts:
        log.warning("rotation skipped: no layer verdicts")
        return None
    if not use_llm:
        return None
    try:
        return run_structured("layer_rotation", LayerRotationView,
                              build_context(cfg, verdicts), skill_slug="sector-analyst")
    except Exception as exc:  # noqa: BLE001
        log.warning("rotation synthesis failed: %s", exc)
        return None
