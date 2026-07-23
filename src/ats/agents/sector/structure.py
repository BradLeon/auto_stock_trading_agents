"""Structure analyst — KB-grounded qualitative overlay for the cross-section.

The quant cross-section ranks by financial factors; it cannot judge a name's
position on the technology curve (光进铜退) or its competitive moat / pricing
power (vertical integration, share, customer concentration) — none of which are
in the financials. This analyst reads the curated sub-layer knowledge-base notes
plus the quant basket and emits per-name tech_tenor / moat_pricing scores that
get blended into the composite. Mirrors the L1-L6 sector analyst (KB → LLM →
structured scores). Degrades to empty (no overlay) on failure.
"""

from __future__ import annotations

import logging

from ..base import run_structured
from .outputs import StructureView

log = logging.getLogger("ats.agents.sector.structure")


def _clamp(v: float | None) -> float:
    try:
        return max(-2.0, min(2.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def assess(rows, structure_notes: dict[str, str],
           pead_narratives: dict[str, str] | None = None):
    """Run the structure analyst over a layer's cohort. Returns
    (scores{symbol: (tech_tenor, moat_pricing, rationale)}, subgroup_notes{name: note}).
    Empty on missing KB or LLM failure (caller keeps the pure-quant ranking)."""
    from ...data import industry

    note_paths = list(dict.fromkeys(structure_notes.values()))   # dedupe, keep order
    kb = industry.fetch_named(note_paths) if note_paths else []
    if not kb:
        log.info("structure: no KB notes for this layer — skipping overlay")
        return {}, {}

    # Compact quant-basket summary so the analyst sees what it's correcting.
    lines = ["## 量化 basket（供参考，你的任务是用结构/技术视角修正它）",
             "sym | 子层 | 量化rank | 营收增速 | 毛利 | PEG | 60d动量"]
    for r in sorted(rows, key=lambda x: x.rank):
        def p(v, s=1.0, suf=""):
            return f"{v * s:.0f}{suf}" if v is not None else "—"
        lines.append(f"{r.symbol} | {r.subgroup or '-'} | {r.rank} | "
                     f"{p(r.rev_growth, 100, '%')} | {p(r.gross_margin, 100, '%')} | "
                     f"{p(r.peg())} | {p(r.mom_60d, 1, '%')}")
    basket_block = "\n".join(lines)

    ctx_parts = [
        "对下列同层标的做结构/技术定性评审。事实以知识库笔记为准。",
        "## 知识库（策展产业笔记 — 事实来源）",
        industry.as_context(kb),
        basket_block,
    ]
    if pead_narratives:
        ctx_parts.append("## 部分标的 PEAD 叙事（参考）\n" + "\n".join(
            f"- {s}: {t[:400]}" for s, t in pead_narratives.items() if t))
    ctx_parts.append("请给每个标的 tech_tenor/moat_pricing/rationale，并给每个子层一条 tech_curve_note。")

    try:
        view: StructureView = run_structured("structure_analyst", StructureView,
                                             "\n\n".join(ctx_parts), skill_slug="structure-analyst")
    except Exception as exc:  # noqa: BLE001 - overlay is best-effort
        log.warning("structure analyst failed: %s", exc)
        return {}, {}

    cohort = {r.symbol for r in rows}
    scores = {n.symbol.upper(): (_clamp(n.tech_tenor), _clamp(n.moat_pricing), n.rationale)
              for n in view.names if n.symbol.upper() in cohort}
    subgroup_notes = {s.subgroup: s.tech_curve_note for s in view.subgroups if s.tech_curve_note}
    return scores, subgroup_notes
