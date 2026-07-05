"""Obsidian markdown risk report from a RiskReview."""

from __future__ import annotations

import logging
from pathlib import Path

from ..schemas.risk import RiskReview

log = logging.getLogger("ats.risk.report")


def render(review: RiskReview) -> str:
    r = review
    lines = [
        f"# 🤖 组合风险报告 — {r.as_of:%Y-%m-%d}",
        "",
        f"**风险状态**: {r.risk_state}  ·  NetLiq ${r.net_liquidation:,.0f}  ·  现金 {r.cash_pct:.0%}  "
        f"·  组合 beta {r.portfolio_beta}  ·  回撤 {r.drawdown_pct}%  ·  日盈亏 {r.daily_pnl_pct}%",
        "",
        "## 破限（硬约束）" if r.breaches else "## 破限：无 ✅",
    ]
    for b in r.breaches:
        lines.append(f"- ⚠️ **{b.layer}** — 实际 {b.actual} vs 限额 {b.limit} → {b.action}")

    if r.chain_layers:
        lines += ["", "## 产业链层集中度", "", "| 层 | 权重 | 上限 |", "|---|---|---|"]
        for le in r.chain_layers:
            mark = " ⚠️" if le.breached else ""
            cap = f"{le.cap:.0%}" if le.cap is not None else "—"
            lines.append(f"| {le.label} | {le.weight:.1%}{mark} | {cap} |")

    if r.clusters:
        lines += ["", "## 相关簇（AI 主题拥挤度）", ""]
        for c in r.clusters:
            lines.append(f"- {c.weight:.0%} avgρ={c.avg_corr}: {', '.join(c.members)}")

    if r.stress:
        lines += ["", "## 情景压测", "", "| 情景 | 损失(%NAV) |", "|---|---|"]
        for s in r.stress:
            lines.append(f"| {s.scenario} | {s.loss_pct}% |")

    if r.event_risks:
        lines += ["", "## 财报事件风险", "", "| 标的 | 权重 | 预期波动 | 事件损失(%NAV) |", "|---|---|---|---|"]
        for e in r.event_risks:
            lines.append(f"| {e.symbol} | {e.weight:.1%} | {e.expected_move_pct}% | {e.event_loss_pct}% |")

    lines += ["", "---", f"*{r.notes}*", ""]
    return "\n".join(lines)


def write(review: RiskReview, out_dir: str) -> Path | None:
    if not out_dir:
        return None
    folder = Path(out_dir)
    if not folder.is_dir():
        log.warning("risk report: output_dir missing — skipped: %s", folder)
        return None
    path = folder / f"组合风险-{review.as_of:%Y-%m-%d}.md"
    path.write_text(render(review), encoding="utf-8")
    return path
