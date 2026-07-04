"""Obsidian markdown report for a MacroReview. Own file, never touches user notes."""

from __future__ import annotations

import logging
from pathlib import Path

from ...schemas.macro_strategy import MacroConfig, MacroReview

log = logging.getLogger("ats.agents.macro.report")


def render(review: MacroReview, cfg: MacroConfig) -> str:
    lines = [
        f"# 🤖 宏观分析 — {cfg.label}（{review.as_of:%Y-%m-%d}）",
        "",
        f"> 由 `ats macro review` 自动生成（macro_strategist，权益策略师范式，每周评审）。",
        "",
        "## 行业状态（regime）",
        f"**{review.regime}**",
        "",
        review.summary,
        "",
        "## 利率路径",
        review.rate_path or "—",
        "",
        "## 板块倾斜（核心）",
        "",
        "| 板块/行业 | 观点 | 理由 |",
        "|---|---|---|",
    ]
    for t in review.sector_tilts:
        lines.append(f"| {t.sector} | **{t.stance}** | {t.rationale} |")

    lines += ["", "## 资产含义", review.asset_implications or "—", "",
              "## 主题评估", "", "| 主题 | 方向 | 对市场传导 | 信号 |", "|---|---|---|---|"]
    for a in review.themes:
        lines.append(f"| {a.label} | {a.direction} | {a.transmission} | {a.signal} |")

    lines += ["", "## 主要风险", ""]
    lines += [f"- {r}" for r in review.top_risks]
    lines += ["", "---", f"*数据截至 {review.as_of:%Y-%m-%d %H:%M} UTC*", ""]
    return "\n".join(lines)


def write(review: MacroReview, cfg: MacroConfig) -> Path | None:
    if not cfg.output_dir:
        log.info("macro report: output_dir unset — skipped")
        return None
    folder = Path(cfg.output_dir)
    if not folder.is_dir():
        log.warning("macro report: output_dir missing — skipped: %s", folder)
        return None
    path = folder / f"宏观分析-{cfg.label}-{review.as_of:%Y-%m-%d}.md"
    path.write_text(render(review, cfg), encoding="utf-8")
    return path
