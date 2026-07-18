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
        f"**风险状态**: {r.risk_state}  ·  NetLiq ${r.net_liquidation:,.0f}  ·  现金 {r.cash_pct:.0%}"
        f"（有效 {r.effective_cash_pct:.0%}）  ·  组合 beta {r.portfolio_beta}  ·  回撤 {r.drawdown_pct}%  "
        f"·  日盈亏 {r.daily_pnl_pct}%",
        "",
        "## 破限（硬约束）" if r.breaches else "## 破限：无 ✅",
    ]
    for b in r.breaches:
        lines.append(f"- ⚠️ **{b.layer}** — 实际 {b.actual} vs 限额 {b.limit} → {b.action}")

    if r.cash_equivalents:
        lines += ["", "## 现金等价物（haircut 计入有效现金）", "",
                  "| 标的 | 市值 | haircut | 现金信用 |", "|---|---|---|---|"]
        for ce in r.cash_equivalents:
            lines.append(f"| {ce.symbol} | ${ce.market_value:,.0f} | {ce.haircut:.0%} | "
                         f"${ce.cash_credit:,.0f} |")
        eff_lev = f"{r.effective_leverage:.2f}x" if r.effective_leverage is not None else "—"
        lines.append(f"\n> 有效现金 {r.effective_cash_pct:.0%}（原始 {r.cash_pct:.0%}）· "
                     f"有效杠杆 {eff_lev}")

    if r.options:
        lines += ["", "## 期权持仓（暂豁免风控）", "",
                  "> 期权非线性，止盈止损/回撤/保证金/单票/beta/集中度等正股规则暂不适用；"
                  "长期需单独的期权风控规则（greeks/到期/名义敞口）。此处仅列示。", "",
                  "| 标的(标的资产) | 类型 | 权重 | 未实现盈亏 |", "|---|---|---|---|"]
        for o in r.options:
            lines.append(f"| {o.symbol} | {o.sec_type} | {o.weight:.1%} | ${o.unrealized_pnl:,.0f} |")

    if r.symbol_layers:
        lines += ["", "## 标的 → 产业链层映射（明文对照）", "",
                  "| 标的 | 类型 | 产业链层 | 风险权重 |", "|---|---|---|---|"]
        for sl in r.symbol_layers:
            layer = sl.label if sl.layer else "— 未分层"
            lines.append(f"| {sl.symbol} | {sl.sec_type} | {layer} | {sl.weight:.1%} |")

    if r.chain_layers:
        lines += ["", "## 产业链层集中度", "",
                  "> 仅统计正股风险权重；期权与未分层标的不占该层上限。", "",
                  "| 层 | 权重 | 上限 |", "|---|---|---|"]
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
