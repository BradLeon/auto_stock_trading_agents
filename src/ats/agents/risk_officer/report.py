"""Obsidian markdown for a risk-officer RiskMemo. Own file, never touches user notes.

Layout: narrative memo (LLM judgment) on top, then the deterministic 6-layer tables
(reused verbatim from risk/report.py) as the auditable appendix.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...schemas.risk import RiskMemo

log = logging.getLogger("ats.agents.risk_officer.report")


def render(memo: RiskMemo) -> str:
    lines = [
        f"# 🤖 风控评估 — {memo.as_of:%Y-%m-%d}",
        "",
        "> 由 `ats risk memo` 自动生成（risk_officer，组合风控官范式）。"
        "数值来自确定性 6 层引擎，叙事为风控官判断。",
        "",
        "## 总评",
        memo.assessment or "—",
    ]
    if memo.cash_equivalent_read:
        lines += ["", "## 现金等价物 · 真实可用弹药", memo.cash_equivalent_read]
    if memo.headroom:
        lines += ["", "## 距限额余量", memo.headroom]
    if memo.layer_conclusions:
        lines += ["", "## 逐层结论", "", "| 层 | 结论 |", "|---|---|"]
        for lc in memo.layer_conclusions:
            lines.append(f"| {lc.layer} | {lc.conclusion} |")
    if memo.recommended_actions:
        lines += ["", "## 建议动作", ""]
        lines += [f"- {a}" for a in memo.recommended_actions]
    if memo.top_risks:
        lines += ["", "## 重点风险", ""]
        lines += [f"- {r}" for r in memo.top_risks]

    if memo.review is not None:
        from ...risk.report import render as render_review
        lines += ["", "---", "", "## 附：确定性 6 层读数", "", render_review(memo.review)]

    lines += ["", "---", f"*数据截至 {memo.as_of:%Y-%m-%d %H:%M} UTC*", ""]
    return "\n".join(lines)


def write(memo: RiskMemo, out_dir: str) -> Path | None:
    if not out_dir:
        return None
    folder = Path(out_dir)
    if not folder.is_dir():
        log.warning("risk memo: output_dir missing — skipped: %s", folder)
        return None
    path = folder / f"风控评估-{memo.as_of:%Y-%m-%d}.md"
    path.write_text(render(memo), encoding="utf-8")
    return path
