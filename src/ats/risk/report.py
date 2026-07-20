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

    if r.cautions:
        lines += ["", "## 提示（不硬阻单）"]
        for c in r.cautions:
            lines.append(f"- · **{c.layer}** — {c.actual} vs {c.limit} → {c.action}")

    if r.margin:
        m = r.margin
        src = "IBKR 权威" if m.source == "ibkr" else ("Reg-T 估算" if m.source == "regt_est" else "—")
        util = f"{m.margin_util:.0%}" if m.margin_util is not None else "—"
        elp = f"{m.excess_liq_pct:.0%}" if m.excess_liq_pct is not None else "—"
        im = f"${m.init_margin:,.0f}" if m.init_margin is not None else "—"
        el = f"${m.excess_liquidity:,.0f}" if m.excess_liquidity is not None else "—"
        lines += ["", f"## 保证金（{src}）", "",
                  f"- 初始保证金 {im} · 维持 "
                  f"{('$%s' % format(m.maint_margin, ',.0f')) if m.maint_margin is not None else '—'}"
                  f" · 剩余流动性 {el}",
                  f"- 保证金利用率 **{util}** · 剩余流动性占比 **{elp}**"]

    if r.portfolio_greeks:
        g = r.portfolio_greeks
        lines += ["", "## 组合 Greeks（期权敞口）", "",
                  f"- 净 Δ 名义 **${g.net_delta_notional:,.0f}** · 净 Vega **${g.net_vega:,.0f}**/1%vol"
                  f" · 净 Theta **${g.net_theta:,.0f}**/日 · 净 Gamma {g.net_gamma:,.2f}",
                  f"- Δ 调整杠杆（含期权）**{g.delta_adj_leverage:.2f}x**"]

    if r.cash_equivalents:
        lines += ["", "## 现金等价物（haircut 计入有效现金）", "",
                  "| 标的 | 市值 | haircut | 现金信用 |", "|---|---|---|---|"]
        for ce in r.cash_equivalents:
            lines.append(f"| {ce.symbol} | ${ce.market_value:,.0f} | {ce.haircut:.0%} | "
                         f"${ce.cash_credit:,.0f} |")
        eff_lev = f"{r.effective_leverage:.2f}x" if r.effective_leverage is not None else "—"
        lines.append(f"\n> 有效现金 {r.effective_cash_pct:.0%}（原始 {r.cash_pct:.0%}）· "
                     f"有效杠杆 {eff_lev}")

    if r.option_risks:
        lines += ["", "## 期权风险明细（已并入 6 层风控）", "",
                  "> Δ名义 = delta×合约数×乘数×现货（已计入单票/产业链层/beta/相关簇/压测）。"
                  "greeks 来源 ibkr=券商实时 / bsm=本地估算。", "",
                  "| 标的 | 策略 | 右/行权/到期 | 手数 | Δ | Δ名义 | IV | Vega | 保证金 | uPnL | 来源 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for o in r.option_risks:
            iv = f"{o.iv:.0%}" if o.iv is not None else "—"
            dlt = f"{o.delta:.2f}" if o.delta is not None else "—"
            vg = f"${o.vega * o.qty * o.multiplier:,.0f}" if o.vega is not None else "—"
            mg = f"${o.margin:,.0f}" if o.margin is not None else "—"
            src = o.greeks_source or ("未定价" if not o.priced else "—")
            lines.append(
                f"| {o.underlying} | {o.strategy} | {o.right}/{o.strike:g}/{o.expiry} | "
                f"{o.qty:g} | {dlt} | ${o.delta_notional:,.0f} | {iv} | {vg} | {mg} | "
                f"${o.unrealized_pnl:,.0f} | {src} |")

    if r.underlying_exposures and any(ue.option_delta_weight for ue in r.underlying_exposures):
        lines += ["", "## 每标的净敞口（正股权重 + 期权 Δ名义）", "",
                  "| 标的 | 正股权重 | 期权Δ权重 | 净Δ权重 | 产业链层 |", "|---|---|---|---|---|"]
        for ue in r.underlying_exposures:
            lines.append(f"| {ue.symbol} | {ue.equity_weight:.1%} | {ue.option_delta_weight:+.1%} | "
                         f"{ue.net_delta_weight:+.1%} | {ue.layer or '—'} |")

    if r.symbol_layers:
        lines += ["", "## 标的 → 产业链层映射（明文对照）", "",
                  "| 标的 | 类型 | 产业链层 | 风险权重 |", "|---|---|---|---|"]
        for sl in r.symbol_layers:
            layer = sl.label if sl.layer else "— 未分层"
            lines.append(f"| {sl.symbol} | {sl.sec_type} | {layer} | {sl.weight:.1%} |")

    if r.chain_layers:
        lines += ["", "## 产业链层集中度", "",
                  "> 含期权 Δ名义净敞口（正股风险权重 + 期权 delta 名义，long put/空 call 净抵）。", "",
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
