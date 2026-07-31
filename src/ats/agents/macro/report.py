"""Obsidian markdown report for a MacroReview. Own file, never touches user notes."""

from __future__ import annotations

import logging
from pathlib import Path

from ...schemas.macro_strategy import MacroConfig, MacroReview

log = logging.getLogger("ats.agents.macro.report")


_QUADRANT_LABEL = {
    "goldilocks": "Goldilocks 温和降温（增长稳 + 通胀下行）",
    "reflation": "Reflation 经济过热（增长改善 + 通胀上行）",
    "stagflation": "Stagflation 滞胀（增长恶化 + 通胀上行）",
    "deflation": "Deflation 普通衰退（增长恶化 + 通胀下行）",
    "transition": "Transition 过渡（不强行四选一）",
}
_STATE_LABEL = {"confirmed": "已确认", "provisional": "暂定",
                "insufficient": "证据不足"}


def _deterministic_section(review: MacroReview) -> list[str]:
    """Code-computed facts, rendered ABOVE the narrative and labelled as such.

    Kept visually separate from the LLM's prose on purpose: a reader has to be
    able to tell at a glance which lines are arithmetic and which are a model's
    interpretation (docs/MACRO_ANALYST.md §4.4).
    """
    # The earnings backdrop comes from a different source than the indicator
    # layer, so it can be present when FRED is down — gating the whole section on
    # indicators alone would silently drop it.
    if not (review.axis_inputs or review.indicators or review.earnings_backdrop):
        return []                       # offline run or a pre-framework review

    lines = ["## 📐 确定性读数（代码算出，非模型判断）", ""]
    if review.axis_inputs or review.indicators:
        quad = _QUADRANT_LABEL.get(review.quadrant, review.quadrant)
        state = _STATE_LABEL.get(review.quadrant_state, review.quadrant_state)
        weeks = f"，连续 {review.quadrant_weeks} 期" if review.quadrant_weeks else ""
        lines += [f"**象限：{quad}** — {state}{weeks}", "",
                  f"- 增长轴 `{review.growth_axis:+.2f}` /"
                  f" 通胀轴 `{review.inflation_axis:+.2f}`"]
        if review.quadrant_reason:
            lines.append(f"- 判定理由：{review.quadrant_reason}")
        if review.focus_keys:
            lines.append(f"- 本期重点指标：{', '.join(review.focus_keys)}")

    if review.alerts:
        lines += ["", "### ⚠️ 告警"] + [f"- {a}" for a in review.alerts]

    dec = review.decomposition
    if dec is not None and dec.d_real_bp is not None:
        lines += ["", f"### 名义利率分解（{dec.window_days} 天）", "",
                  f"`Δ名义 {dec.d_nominal_bp:+.0f}bp = Δ实际 {dec.d_real_bp:+.0f}bp"
                  f" + Δ通胀补偿 {dec.d_breakeven_bp:+.0f}bp`", "",
                  f"- **{dec.classification}**", f"- {dec.equity_read}"]
        if dec.real_yield_cause:
            lines.append(f"- 实际收益率下降成因：{dec.real_yield_cause}")

    bd = review.earnings_backdrop
    if bd is not None:
        lines += ["", "### 总量盈利周期（S&P500，FactSet Earnings Insight）", ""]
        if bd.degraded:
            lines.append(f"⚠️ 结构化抽取失败（{'; '.join(bd.notes) or '未知原因'}）—— "
                         "本期仅有散文可用。")
        else:
            rows = []
            if bd.fwd_pe is not None:
                rows.append(("前瞻 12 个月 P/E", f"{bd.fwd_pe}"
                             f"（5年均值 {bd.fwd_pe_5y_avg} / 10年均值 {bd.fwd_pe_10y_avg}）"))
            if bd.fwd_pe_vs_5y_pct is not None:
                rows.append(("估值偏离", f"较 5 年均值 {bd.fwd_pe_vs_5y_pct:+.1f}%"))
            if bd.growth_pct is not None:
                rows.append((f"{bd.quarter} 盈利增速",
                             f"{bd.growth_pct:+.1f}%（{bd.growth_basis}）"))
            if bd.revision_pp is not None:
                rows.append(("盈利修正", f"较季初 {bd.prior_as_of} "
                             f"{bd.prior_growth_pct:+.1f}% → {bd.growth_pct:+.1f}%"
                             f"（{bd.revision_pp:+.1f}pp）"))
            if bd.sectors_higher is not None:
                way = {"upward": "上修", "downward": "下修"}.get(bd.revision_direction, "变动")
                rows.append(("修正广度", f"{bd.sectors_higher} 个板块{way}"))
            if bd.pct_reported is not None:
                rows.append(("披露进度", f"{bd.pct_reported:.0f}% 已报 · EPS 超预期 "
                             f"{bd.pct_eps_beat:.0f}% · 营收超预期 {bd.pct_revenue_beat:.0f}%"))
            if bd.guidance_negative is not None:
                rows.append((f"{bd.guidance_quarter} 指引",
                             f"负面 {bd.guidance_negative} / 正面 {bd.guidance_positive}"))
            lines += ["| 项 | 值 |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in rows]
            lines += ["", f"*来源：{bd.source}"
                      f"{f'（{bd.report_date}）' if bd.report_date else ''}；指数层面总量，"
                      "不含个股。*"]

    if review.shock_vs_trend:
        lines += ["", "### 趋势 vs 冲击（美联储会反应 or 看穿）", ""]
        lines += [f"- {s}" for s in review.shock_vs_trend]

    if review.axis_inputs:
        lines += ["", "### 象限判定的逐项依据", "",
                  "| 输入 | 取值 | 判据 | 得分 |", "|---|---|---|---|"]
        for a in review.axis_inputs:
            val = "n/a" if a.value is None else a.value
            lines.append(f"| {a.label or a.key} | {val} | {a.threshold} | `{a.score:+.2f}` |")
    return lines + [""]


def _indicator_appendix(review: MacroReview) -> list[str]:
    live = [r for r in review.indicators if r.level is not None]
    if not live:
        return []
    lines = ["## 附录：指标读数", "",
             "变化单位：收益率/利差为 bp，价格与指数为 %，零中心序列为绝对差。", "",
             "| 指标 | 水平 | Δ1w | Δ1m | Δ3m | z(3y) | 10y百分位 | 截至 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in live:
        u = "bp" if r.unit == "pct" else ("" if r.unit == "level" else "%")
        f = lambda v: "—" if v is None else f"{v:+.1f}{u}"  # noqa: E731
        flag = " ⚠️" if r.stale else ""
        lines.append(
            f"| {r.label or r.key}{flag} | {r.level} | {f(r.d_1w)} | {f(r.d_1m)} |"
            f" {f(r.d_3m)} | {r.z_3y if r.z_3y is not None else '—'} |"
            f" {r.pct_10y if r.pct_10y is not None else '—'} | {r.as_of or '—'} |")
    missing = [r.label or r.key for r in review.indicators if r.level is None]
    if missing:
        lines += ["", f"缺失数据源：{', '.join(missing)}"]
    return lines + [""]


def render(review: MacroReview, cfg: MacroConfig) -> str:
    lines = [
        f"# 🤖 宏观分析 — {cfg.label}（{review.as_of:%Y-%m-%d}）",
        "",
        "> 由 `ats macro review` 自动生成（macro_strategist，权益策略师范式，每周评审）。",
        "> 📐 标记的章节由确定性代码算出；其余为模型解读。",
        "",
    ]
    lines += _deterministic_section(review)
    lines += [
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

    # A macro call you cannot falsify is worth nothing, and this is next week's
    # checklist item — so it gets its own section rather than a footnote.
    if review.falsifier:
        lines += ["", "## 证伪条件（什么观察会推翻这次判断）", "", review.falsifier]

    lines += [""] + _indicator_appendix(review)
    lines += ["---", f"*数据截至 {review.as_of:%Y-%m-%d %H:%M} UTC*", ""]
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
