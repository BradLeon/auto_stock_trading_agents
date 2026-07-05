"""Obsidian audit report for a Chief run — decision + the exact context it saw."""

from __future__ import annotations

import logging
from pathlib import Path

from .decide import ChiefResult

log = logging.getLogger("ats.agents.chief.report")


def render(result: ChiefResult) -> str:
    lines = [
        f"# 🤖 首席决策 — {result.as_of:%Y-%m-%d %H:%M} UTC（{result.cycle_id}）",
        "",
        "## 决策",
        "",
        result.summary,
        "",
    ]
    if result.decisions:
        lines += ["| 动作 | 标的 | 规模 | 信心 | 理由 |", "|---|---|---|---|---|"]
        for d in result.decisions:
            size = f"${d.notional_usd:,.0f}" if d.notional_usd else (
                f"w={d.target_weight:.0%}" if d.target_weight else "—")
            lines.append(f"| {d.action} | {d.symbol} | {size} | {d.conviction:.2f} "
                         f"| {d.rationale} |")
    else:
        lines.append("**（无行动 — 零决策）**")
    if result.context_text:
        lines += ["", "---", "", "## 决策时所见的完整上下文（审计）", "",
                  "```", result.context_text, "```"]
    lines.append("")
    return "\n".join(lines)


def write(result: ChiefResult, out_dir: str) -> Path | None:
    if not out_dir:
        log.info("chief report: output_dir unset — skipped")
        return None
    folder = Path(out_dir)
    if not folder.is_dir():
        log.warning("chief report: output_dir missing — skipped: %s", folder)
        return None
    path = folder / f"首席决策-{result.as_of:%Y-%m-%d-%H%M}.md"
    path.write_text(render(result), encoding="utf-8")
    return path
