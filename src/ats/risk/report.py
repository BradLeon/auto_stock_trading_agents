"""Obsidian markdown risk report from a RiskReview."""

from __future__ import annotations

import logging
from pathlib import Path

from ..schemas.risk import RiskReview

log = logging.getLogger("ats.risk.report")


def render(review: RiskReview, rc=None) -> str:
    r = review
    if rc is None:
        from ..config import get_config
        rc = get_config().app.risk
    dp_usd = (r.daily_pnl_pct / 100 * r.net_liquidation) if r.daily_pnl_pct is not None else None
    dp_txt = (f"{r.daily_pnl_pct}%（${dp_usd:,.0f}，仅盘中/不含盘前后）" if dp_usd is not None
              else f"{r.daily_pnl_pct}%")
    lines = [
        f"# 🤖 组合风险报告 — {r.as_of:%Y-%m-%d}",
        "",
        f"**风险状态**: {r.directive.state if r.directive else r.risk_state}  "
        f"·  NetLiq ${r.net_liquidation:,.0f}  ·  现金 {r.cash_pct:.0%}"
        f"（计入现金等价物后有效 {r.effective_cash_pct:.0%}）  ·  组合 beta {r.portfolio_beta}  "
        f"·  回撤 {r.drawdown_pct}%  ·  日盈亏 {dp_txt}",
        "",
        "## 破限（硬约束）" if r.breaches else "## 破限：无 ✅",
    ]
    for b in r.breaches:
        lines.append(f"- ⚠️ **{b.layer}** — 实际 {b.actual} vs 限额 {b.limit} → {b.action}")

    if r.directive:
        d = r.directive
        lines += ["", "## 给 Chief 的 RiskDirective", "",
                  f"- 状态 **{d.state}** · 可增加风险 **{d.can_increase_risk}**",
                  f"- 允许动作：{', '.join(d.allowed_actions) or '无'}"]
        if d.blocked_entities:
            lines.append(f"- 禁止增加实体：{', '.join(d.blocked_entities)}")
        if d.blocked_layers:
            lines.append(f"- 禁止增加产业链层：{', '.join(d.blocked_layers)}")
        if d.risk_budget_remaining:
            lines.append("- 剩余风险预算：" + "; ".join(
                f"{key}={value:.1%}" for key, value in d.risk_budget_remaining.items()))

    if r.cautions:
        lines += ["", "## 提示（不硬阻单）"]
        for c in r.cautions:
            lines.append(f"- · **{c.layer}** — {c.actual} vs {c.limit} → {c.action}")

    lines += _layer_overview_lines(r, rc)

    if r.margin:
        m = r.margin
        src = (
            "IBKR 权威" if m.source == "ibkr"
            else "IBKR基线+交易后估算" if m.source == "ibkr_projected_est"
            else "Reg-T 估算" if m.source == "regt_est" else "—")
        util = f"{m.margin_util:.0%}" if m.margin_util is not None else "—"
        elp = f"{m.excess_liq_pct:.0%}" if m.excess_liq_pct is not None else "—"
        im = f"${m.init_margin:,.0f}" if m.init_margin is not None else "—"
        el = f"${m.excess_liquidity:,.0f}" if m.excess_liquidity is not None else "—"
        lines += ["", f"## 保证金（{src}）", "",
                  f"- 初始保证金 {im} · 维持 "
                  f"{('$%s' % format(m.maint_margin, ',.0f')) if m.maint_margin is not None else '—'}"
                  f" · 剩余流动性 {el}",
                  f"- 保证金利用率 **{util}** · 剩余流动性占比 **{elp}**"]

    if r.option_survival and r.option_survival.assignments:
        s = r.option_survival
        lines += ["", "## 期权指派与资金生存性", "",
                  "> 全额指派是灾难上限；容量判断同时使用 BSM 到期ITM概率 N(-d2)、"
                  "到期结构和 P99 资金占用。美式期权仍可能提前指派。", "",
                  f"- 全额名义 **${s.total_full_assignment_notional:,.0f}** · "
                  f"概率加权 **${s.probability_weighted_notional:,.0f}** · "
                  f"P99 **${s.p99_assignment_notional:,.0f}**",
                  f"- 单到期日峰值 **${s.peak_expiry_full_notional:,.0f}** · "
                  f"可用流动性 **${s.available_liquidity:,.0f}** · "
                  f"P99资金缺口 **${s.p99_funding_gap:,.0f}**", "",
                  "| 标的 | 到期 | 天数 | 全额指派 | ITM概率 | 概率占用 | 概率来源 |",
                  "|---|---|---|---|---|---|---|"]
        for a in s.assignments:
            probability = (f"{a.assignment_probability:.1%}"
                           if a.assignment_probability is not None else "未知")
            lines.append(
                f"| {a.underlying} | {a.expiry} | {a.days_to_expiry} | "
                f"${a.full_assignment_notional:,.0f} | {probability} | "
                f"${a.probability_weighted_notional:,.0f} | {a.probability_source} |")
        lines += ["", "| 累计期限桶 | 涉及到期日 | 全额名义 | 期望占用 | P99占用 |",
                  "|---|---|---|---|---|"]
        for b in s.expiry_buckets:
            lines.append(
                f"| {b.label} | {', '.join(b.expiries) or '—'} | ${b.full_notional:,.0f} | "
                f"${b.expected_notional:,.0f} | ${b.p99_notional:,.0f} |")

    if r.portfolio_greeks:
        g = r.portfolio_greeks
        lines += ["", "## 组合 Greeks（期权敞口）", "",
                  f"- 净 Δ 名义 **${g.net_delta_notional:,.0f}** · 净 Vega **${g.net_vega:,.0f}**/1%vol"
                  f" · 净 Theta **${g.net_theta:,.0f}**/日 · 净 Gamma {g.net_gamma:,.2f}",
                  f"- Δ 调整杠杆（含期权）**{g.delta_adj_leverage:.2f}x**",
                  "",
                  "> 净Δ名义：期权折算成等价正股方向性敞口（多空互抵后的净值），本身不直接参与硬限额判断——"
                  "真正驱动 L1 单票/产业链层限额的是下方「每标的净敞口」里逐标的的 Δ 权重。",
                  "> 净Vega：隐含波动率每变动 1 个百分点，期权组合的浮盈亏变化（净值，多空可能相互对冲，"
                  "会掩盖单腿上的集中 vega 风险）。",
                  "> 净Theta：每日时间损耗带来的浮盈亏。",
                  "> 净Gamma：标的每变动 $1，Δ名义变化的速度——净空 gamma 意味着不利方向上敞口会非线性加速恶化；"
                  "**当前没有硬限额覆盖这一项，需人工盯梢**，尤其是临近到期、IV 偏高的空头期权。",
                  "> Δ调整杠杆：期权按 Δ 折算成正股等价敞口后的组合经济杠杆，区别于账面持仓市值杠杆。"]

    if r.cash_equivalents:
        lines += ["", "## 现金等价物（haircut 计入有效现金）", "",
                  "| 标的 | 市值 | haircut | 现金信用 |", "|---|---|---|---|"]
        for ce in r.cash_equivalents:
            lines.append(f"| {ce.symbol} | ${ce.market_value:,.0f} | {ce.haircut:.0%} | "
                         f"${ce.cash_credit:,.0f} |")
        eff_lev = f"{r.effective_leverage:.2f}x" if r.effective_leverage is not None else "—"
        lines.append(f"\n> 有效现金 {r.effective_cash_pct:.0%}（原始 {r.cash_pct:.0%}）· "
                     f"有效杠杆 {eff_lev}")

    if r.economic_exposures:
        lines += ["", "## 经济风险实体（跨证券代码汇总）", "",
                  "> 资本权重是基础货币市值/NAV；经济Δ再计入杠杆 ETF 倍数与期权 delta。",
                  "", "| 风险实体 | 成员证券 | 资本权重 | 正股经济Δ | 期权Δ | 净经济Δ | beta贡献 |",
                  "|---|---|---|---|---|---|---|"]
        for ee in sorted(r.economic_exposures, key=lambda x: abs(x.net_delta_weight), reverse=True):
            bc = f"{ee.beta_contribution:+.1%}" if ee.beta_contribution is not None else "—"
            lines.append(f"| {ee.label} | {', '.join(ee.members)} | {ee.capital_weight:.1%} | "
                         f"{ee.equity_delta_weight:+.1%} | {ee.option_delta_weight:+.1%} | "
                         f"{ee.net_delta_weight:+.1%} | {bc} |")

    if r.option_risks:
        lines += ["", "## 期权风险明细（已并入 6 层风控）", "",
                  "> Δ名义 = delta×合约数×乘数×现货（已计入单票/产业链层/beta/相关簇/压测）。"
                  "greeks 来源 ibkr=券商实时 / bsm=本地估算。", "",
                  "| 标的 | 策略 | 右/行权/到期 | 手数 | Δ | Δ名义 | IV | Vega | 保证金 | uPnL | 来源 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for o in r.option_risks:
            iv = f"{o.iv:.0%}" if o.iv is not None else "—"
            dlt = f"{o.delta:.2f}" if o.delta is not None else "—"
            vg = (f"${o.vega * o.qty * o.multiplier * o.fx_rate_to_base:,.0f}"
                  if o.vega is not None else "—")
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
        # 跨层上限单独一段。拆层把一个 cap 变成两个，group 上限把它们重新绑回拆分前的
        # 总量——**单层都没越限而合计越限**是它唯一存在的理由，所以它必须自己显示，
        # 不能折进上面那张按层的表里。
        if r.chain_layer_groups:
            lines += ["", "### 跨层上限（拆层不得放大总敞口）", "",
                      "| 组 | 成员层 | 合计 | 上限 |", "|---|---|---|---|"]
            for g in r.chain_layer_groups:
                mark = " ⚠️" if g.breached else ""
                cap = f"{g.cap:.0%}" if g.cap is not None else "—"
                lines.append(f"| {g.label} | {' + '.join(g.members)} | "
                             f"{g.weight:.1%}{mark} | {cap} |")

    if r.clusters:
        lines += ["", "## 相关簇（AI 主题拥挤度）", ""]
        for c in r.clusters:
            lines.append(f"- {c.weight:.0%} avgρ={c.avg_corr}: {', '.join(c.members)}")

    if r.stress:
        lines += ["", "## 情景压测", "",
                  "> 正股用 beta 线性近似（Σ风险权重×beta×市场冲击），期权用 Black-Scholes 全额重定价"
                  "（现货冲击与隐含波动率冲击联动，模拟崩盘时 IV 飙升），比线性 Δ 近似更能捕捉 "
                  "gamma/vega 的非线性尾部损失。",
                  "", "| 情景 | 损失(%NAV) |", "|---|---|"]
        for s in r.stress:
            lines.append(f"| {s.scenario} | {s.loss_pct}% |")

    if r.event_risks:
        lines += ["", "## 财报事件风险", "", "| 标的 | 权重 | 预期波动 | 事件损失(%NAV) |", "|---|---|---|---|"]
        for e in r.event_risks:
            lines.append(f"| {e.symbol} | {e.weight:.1%} | {e.expected_move_pct}% | {e.event_loss_pct}% |")

    lines += ["", "---", f"*{r.notes}*", ""]
    return "\n".join(lines)


def _layer_overview_lines(r: RiskReview, rc) -> list[str]:
    """One-glance L1~L6 status table. Breached/caution layers point back to the lists
    already rendered above (never re-states their text, to avoid the two copies drifting);
    clean layers show their actual reading vs the configured limit so headroom is visible
    even when nothing fired — this is what the rest of the report doesn't otherwise expose
    per-layer."""

    def status_for(prefix: str) -> tuple[str, str] | None:
        n_b = sum(1 for b in r.breaches if b.layer.startswith(prefix))
        n_c = sum(1 for c in r.cautions if c.layer.startswith(prefix))
        if n_b:
            return f"⚠️ {n_b} 项破限", "详见上方「破限」列表"
        if n_c:
            return f"· {n_c} 项提示", "详见上方「提示」列表"
        return None

    rows: list[tuple[str, str, str, str]] = []

    st = status_for("L1-")
    if st:
        status, reading = st
    else:
        status = "✅ 正常"
        parts = []
        if r.underlying_exposures:
            top = max(r.underlying_exposures, key=lambda ue: abs(ue.net_delta_weight))
            parts.append(f"单票最大 {top.symbol} {abs(top.net_delta_weight):.0%}"
                         f"（限{rc.max_position_pct:.0%}）")
        if r.chain_layers:
            top_l = max(r.chain_layers, key=lambda le: (le.weight / le.cap) if le.cap else 0.0)
            cap_txt = f"（限{top_l.cap:.0%}）" if top_l.cap is not None else ""
            parts.append(f"产业链层最高 {top_l.label} {top_l.weight:.0%}{cap_txt}")
        reading = " · ".join(parts) or "—"
    rows.append(("L1 标的", "单票/产业链层/止损", status, reading))

    st = status_for("L2-")
    if st:
        status, reading = st
    else:
        status = "✅ 正常"
        parts = []
        if r.effective_leverage is not None:
            parts.append(f"有效杠杆 {r.effective_leverage:.2f}x（限{rc.max_gross_leverage}x）")
        parts.append(f"有效现金 {r.effective_cash_pct:.0%}（限≥{rc.cash_floor_pct:.0%}）")
        if r.margin and r.margin.margin_util is not None:
            parts.append(f"保证金利用率 {r.margin.margin_util:.0%}（限{rc.max_margin_util_pct:.0%}）")
        reading = " · ".join(parts) or "—"
    rows.append(("L2 组合", "杠杆/现金/保证金", status, reading))

    st = status_for("L3-")
    if st:
        status, reading = st
    else:
        status = "✅ 正常"
        parts = []
        if r.portfolio_beta is not None:
            parts.append(f"组合beta {r.portfolio_beta}（限≤{rc.beta_cap}）")
        if r.clusters:
            parts.append(f"最大相关簇 {r.clusters[0].weight:.0%}（限≤{rc.cluster_weight_cap:.0%}）")
        else:
            parts.append("暂无≥2只标的的相关簇")
        reading = " · ".join(parts) or "—"
    rows.append(("L3 市场/因子", "beta/相关簇", status, reading))

    st = status_for("L4-")
    if st:
        status, reading = st
    else:
        status = "✅ 正常"
        parts = []
        if r.drawdown_pct is not None:
            parts.append(f"回撤 {r.drawdown_pct}%（限≥-{rc.max_drawdown_pct:.0%}）")
        if r.daily_pnl_pct is not None:
            parts.append(f"日盈亏 {r.daily_pnl_pct}%（限≥-{rc.daily_loss_limit_pct:.0%}）")
        reading = " · ".join(parts) or "—"
    rows.append(("L4 亏损/回撤", "回撤/日亏", status, reading))

    st = status_for("L5-")
    if st:
        status, reading = st
    else:
        status = "✅ 正常"
        if r.stress:
            worst = min(r.stress, key=lambda s: s.loss_pct)
            reading = f"最差情景「{worst.scenario}」{worst.loss_pct}%（限≥-{rc.max_stress_loss_pct:.0%}）"
        else:
            reading = "未跑压测"
    rows.append(("L5 尾部/压测", "情景压测", status, reading))

    st = status_for("L6-")
    if st:
        status, reading = st
    else:
        status = "✅ 正常"
        if r.event_risks:
            worst_e = max(r.event_risks, key=lambda e: e.event_loss_pct)
            reading = f"{worst_e.symbol} 事件损失 {worst_e.event_loss_pct:.1f}%（限≤{rc.max_event_loss_pct:.0%}）"
        else:
            reading = "近期无财报事件敞口"
    rows.append(("L6 事件", "财报事件", status, reading))

    lines = ["", "## 六层风控一览", "", "| 层 | 覆盖 | 状态 | 读数 |", "|---|---|---|---|"]
    for label, scope, status, reading in rows:
        lines.append(f"| {label} | {scope} | {status} | {reading} |")
    return lines


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
