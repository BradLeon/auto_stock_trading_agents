"""Obsidian markdown report for PEAD — one full-lifecycle document per (symbol, fiscal).

The document STRUCTURE lives in skills/pead-report/TEMPLATE.md (the gold-standard
七节 layout with {{slot}} placeholders) — edit that file to change the report
format, not this module. This module only converts dossier data into markdown
fragments and fills the slots: prep fills 一/二/六/七, score fills 三/四/五.
Sections whose data isn't available yet render an explicit ⏳ placeholder so the
whole lifecycle skeleton is always visible, and the score phase re-renders the
SAME file in place.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("ats.agents.pead.report")

TEMPLATE_PATH = (Path(__file__).resolve().parents[2]
                 / "skills" / "pead-report" / "TEMPLATE.md")

_NA = "（暂无数据）"

_BULLET_RE = re.compile(r"^(\d+[.、]|[-*•])")


def _iv_pct(v: float) -> float:
    """Options sources are inconsistent: fraction (0.55) vs percent (55.0)."""
    return v * 100 if v <= 3 else v


def _bullets(items: list[str]) -> str:
    """LLMs sometimes return one newline-joined string instead of a list —
    flatten to one line per bullet, keeping existing numbering/bullets."""
    lines = [seg.strip() for item in items for seg in item.splitlines() if seg.strip()]
    return "\n".join(seg if _BULLET_RE.match(seg) else f"- {seg}" for seg in lines)


def _pending(dossier) -> str:
    return (f"> ⏳ 待财报后填写（运行 `pead score {dossier.symbol}` 后自动回填本节）")


def _dim_labels(dossier) -> dict[str, str]:
    return {d.key: (d.label or d.key) for d in (dossier.scorecard_dims or [])}


# --------------------------------------------------------------------------- #
# 一、背景
# --------------------------------------------------------------------------- #
def _background(dossier) -> str:
    fb = dossier.fundamental_background
    return (fb.background if fb and fb.background else _NA)


def _peer_comparison(dossier) -> str:
    fb = dossier.fundamental_background
    return (fb.peer_comparison if fb and fb.peer_comparison else _NA)


def _focus_ranking(dossier) -> str:
    es = dossier.expectation_set
    if not es or not es.focus_ranking:
        return _NA
    return "\n".join(f"{i}. {item}" for i, item in enumerate(es.focus_ranking, 1))


def _watch_metrics(dossier) -> str:
    fb = dossier.fundamental_background
    return (fb.watch_metrics if fb and fb.watch_metrics else _NA)


def _financial_snapshot(dossier) -> str:
    fd = dossier.fundamentals_context or ""
    if not fd or fd == "(offline)":
        return _NA
    return "```\n" + fd.strip() + "\n```"


def _valuation(dossier) -> str:
    fb_val = dossier.fundamental_background.valuation if dossier.fundamental_background else ""
    es_val = dossier.expectation_set.valuation if dossier.expectation_set else ""
    parts = []
    if fb_val:
        parts.append(fb_val)
    if es_val and es_val != fb_val:
        parts.append("**PEAD 视角**: " + es_val)
    return "\n\n".join(parts) if parts else _NA


def _catalysts_risks(dossier) -> str:
    fb = dossier.fundamental_background
    parts = []
    if fb and fb.catalysts:
        parts.append("**催化剂**\n" + _bullets(fb.catalysts))
    if fb and fb.key_risks:
        parts.append("**尾部风险（按严重程度）**\n" + _bullets(fb.key_risks))
    return "\n\n".join(parts) if parts else _NA


# --------------------------------------------------------------------------- #
# 二、市场预期
# --------------------------------------------------------------------------- #
def _expectations_table(dossier) -> str:
    es = dossier.expectation_set
    if not es or not es.expectations:
        return _NA
    weights = dossier.scorecard_weights or {}
    labels = _dim_labels(dossier)
    lines = ["| 维度 (权重) | 指标 | 保守 | 中性（基准） | 乐观 | 依据/来源 |",
             "|---|---|---|---|---|---|"]
    for r in es.expectations:
        label = labels.get(r.dim_key, r.dim_key)
        w = weights.get(r.dim_key)
        if w:
            label = f"{label} ({w:.0%})"
        lines.append(f"| {label} | {r.metric or '—'} | {r.conservative or '—'} "
                     f"| {r.neutral or '—'} | {r.optimistic or '—'} | {r.source or '—'} |")
    return "\n".join(lines)


def _consensus(dossier) -> str:
    es = dossier.expectation_set
    if not es or not any([es.consensus_eps, es.consensus_revenue,
                          es.consensus_target_price, es.consensus_rating_summary]):
        return _NA
    lines = ["| 指标 | 共识 | 低端 | 高端 |", "|---|---|---|---|"]
    if es.consensus_eps is not None:
        low = f"${es.consensus_eps_low:.2f}" if es.consensus_eps_low else "—"
        high = f"${es.consensus_eps_high:.2f}" if es.consensus_eps_high else "—"
        lines.append(f"| EPS | **${es.consensus_eps:.2f}** | {low} | {high} |")
    if es.consensus_revenue is not None:
        # yfinance returns revenue in local currency for ADRs (e.g. TSM = NTD, not USD)
        rev_b = es.consensus_revenue / 1e9
        low_b = f"{es.consensus_revenue_low/1e9:.1f}B" if es.consensus_revenue_low else "—"
        high_b = f"{es.consensus_revenue_high/1e9:.1f}B" if es.consensus_revenue_high else "—"
        lines.append(f"| Revenue (本地货币) | **{rev_b:.1f}B** | {low_b} | {high_b} |")
    if es.consensus_target_price is not None:
        lines.append(f"| 目标价 (PT均值) | **${es.consensus_target_price:.1f}** | — | — |")
    out = ["\n".join(lines)]
    if es.consensus_rating_summary:
        out.append(f"**评级分布**: {es.consensus_rating_summary}")
    if es.consensus_recent_actions:
        out.append("**近期评级变动**:\n"
                   + "\n".join(f"- {a}" for a in es.consensus_recent_actions))
    return "\n\n".join(out)


def _market_setup(dossier) -> str:
    ms = dossier.market_setup
    if not ms:
        return _NA
    lines = []
    if ms.pre_earnings_close is not None:
        lines.append(f"- 当前价: **${ms.pre_earnings_close:.2f}**")
    if ms.run_up_vs_sector_pct is not None:
        lines.append(f"- 抢跑 vs 板块 ETF: **{ms.run_up_vs_sector_pct:+.1f}%** (20日超额)")
    if ms.run_up_vs_bench_pct is not None:
        lines.append(f"- 抢跑 vs 大盘基准: **{ms.run_up_vs_bench_pct:+.1f}%** (20日超额)")
    if ms.dist_to_ath_pct is not None:
        lines.append(f"- 距 ATH: **{ms.dist_to_ath_pct:+.1f}%**")
    if ms.expected_move_pct is not None:
        lines.append(f"- 期权隐含预期波幅 (EM): **±{ms.expected_move_pct:.1f}%**")
    if ms.atm_iv is not None:
        lines.append(f"- ATM IV: **{_iv_pct(ms.atm_iv):.0f}%**")
    if ms.iv_skew is not None:
        skew_note = ("（正 = put premium 偏高，看跌倾向）" if ms.iv_skew > 0
                     else "（负 = call premium 偏高，看涨偏斜）")
        lines.append(f"- IV Skew (25Δ put-call): **{ms.iv_skew:+.1f}pts** {skew_note}")
    lines += [f"- _{note}_" for note in ms.notes]
    return "\n".join(lines) if lines else _NA


def _narrative(dossier) -> str:
    es = dossier.expectation_set
    return (es.narrative if es and es.narrative else _NA)


# --------------------------------------------------------------------------- #
# 三、业绩实际（财报后）
# --------------------------------------------------------------------------- #
def _actuals(dossier) -> str:
    a = dossier.actuals
    if not a:
        return _pending(dossier)
    labels = _dim_labels(dossier)
    parts = []
    headline = []
    if a.reported_eps is not None:
        headline.append(f"EPS **${a.reported_eps:.2f}**")
    if a.reported_revenue is not None:
        headline.append(f"Revenue **{a.reported_revenue/1e9:.1f}B**")
    if headline:
        parts.append("头条数字: " + " ｜ ".join(headline))
    if a.metrics:
        lines = ["| 维度 | 指标 | 实际 | vs 预期 | 说明 |", "|---|---|---|---|---|"]
        for m in a.metrics:
            lines.append(f"| {labels.get(m.dim_key, m.dim_key)} | {m.metric or '—'} "
                         f"| {m.actual or '—'} | {m.vs_expected or '—'} | {m.note or '—'} |")
        parts.append("\n".join(lines))
    if a.guidance:
        parts.append("**前瞻指引**: " + a.guidance)
    if a.transcript_signals:
        parts.append("**电话会关键信号**\n" + _bullets(a.transcript_signals))
    if a.transcript_source:
        parts.append(f"_纪要来源: {a.transcript_source}_")
    return "\n\n".join(parts) if parts else _pending(dossier)


# --------------------------------------------------------------------------- #
# 四、Surprise Scorecard
# --------------------------------------------------------------------------- #
def _scorecard(dossier) -> str:
    sc = dossier.scorecard
    if sc and sc.lines:
        lines = ["| 维度 | 权重 | 评分 (-2..+2) | 加权得分 | 备注 |",
                 "|---|---|---|---|---|"]
        for ln in sc.lines:
            lines.append(f"| {ln.label or ln.dim_key} | {ln.weight:.0%} | {ln.score:+.2f} "
                         f"| {ln.weighted:+.2f} | {ln.note or '—'} |")
        lines.append(f"| **总分** |  |  | **{sc.total:+.2f}** | 门槛 {sc.threshold:+.1f} |")
        return "\n".join(lines) + f"\n\n**评级**: {sc.band}"
    # prep 阶段：骨架表 — 维度和权重已定，得分留空
    dims = dossier.scorecard_dims or []
    if not dims:
        return _pending(dossier)
    lines = ["| 维度 | 权重 | 评分 (-2..+2) | 加权得分 | 备注 |",
             "|---|---|---|---|---|"]
    for d in dims:
        lines.append(f"| {d.label or d.key} | {d.weight:.0%} | — | — | — |")
    lines.append(f"| **总分** |  |  | — | 门槛 {dossier.long_threshold:+.1f} |")
    return "\n".join(lines) + "\n\n" + _pending(dossier)


# --------------------------------------------------------------------------- #
# 五、交易 Decision 框架
# --------------------------------------------------------------------------- #
def _decision_tree(dossier) -> str:
    t = dossier.long_threshold
    w = dossier.run_up_warn_pct
    return "\n".join([
        "| Scorecard 总分 | 超额 run-up（vs 板块） | 标准决策 |",
        "|---|---|---|",
        f"| ≥ {t:+.1f} | < {w:.0f}% | 正常入场，财报后顺 drift 建仓 |",
        f"| ≥ {t:+.1f} | ≥ {w:.0f}% | 谨慎：利好可能已被抢跑定价——减半仓位或等回踩 |",
        f"| 0 ~ {t:+.1f} | 任意 | 观望——意外幅度不足以支撑 drift |",
        "| < 0 | 任意 | 不做多；负面意外考虑反向 |",
    ])


def _decision(dossier) -> str:
    return dossier.decision_summary or _pending(dossier)


# --------------------------------------------------------------------------- #
# 六、执行检查清单
# --------------------------------------------------------------------------- #
def _checklist(dossier) -> str:
    def mark(done: bool) -> str:
        return "✅" if done else "⬜"

    es = dossier.expectation_set
    ms = dossier.market_setup
    has_options = bool(ms and ms.expected_move_pct is not None)
    return "\n".join([
        "| 阶段 | 任务 | 状态 |",
        "|---|---|---|",
        f"| 财报前 | 预期基准表（prep） | {mark(bool(es and es.expectations))} |",
        f"| 财报前 | 期权 / 量价设置采集 | {mark(has_options)} |",
        f"| 财报前 | 跨标的信号链解读 | {mark(bool(dossier.signal_chain))} |",
        f"| 财报后 | 实际结果提取 | {mark(dossier.actuals is not None)} |",
        f"| 财报后 | Surprise Scorecard | {mark(bool(dossier.scorecard and dossier.scorecard.lines))} |",
        f"| 财报后 | 交易判断（供 Chief 决策） | {mark(bool(dossier.decision_summary))} |",
    ])


# --------------------------------------------------------------------------- #
# 七、跨标的信号链
# --------------------------------------------------------------------------- #
def _signal_chain(dossier) -> str:
    sc = dossier.signal_chain
    summary = dossier.signal_chain_summary or ""
    if not sc and not summary:
        return _NA
    parts = []
    if summary:
        parts.append("**链路综述**: " + summary)
    if sc:
        role_cn = {"upstream": "上游", "peer": "同业", "downstream": "下游"}
        lines = ["| 标的 | 角色 | 20日涨跌 | 财报日 | 信号/含义 |",
                 "|---|---|---|---|---|"]
        for item in sc:
            chg = f"{item.price_chg_pct:+.1f}%" if item.price_chg_pct is not None else "—"
            ed = item.earnings_date.isoformat() if item.earnings_date else "—"
            lines.append(f"| {item.symbol} | {role_cn.get(item.role, item.role)} "
                         f"| {chg} | {ed} | {item.signal or '—'} |")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Render + write
# --------------------------------------------------------------------------- #
def render_dossier(dossier) -> str:
    """Fill skills/pead-report/TEMPLATE.md slots from the dossier."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    phase_cn = {"prep": "财报前（prep）", "score": "财报后（score）"}
    slots = {
        "symbol": dossier.symbol,
        "fiscal_label": dossier.fiscal_label,
        "updated_at": f"{now:%Y-%m-%d %H:%M} UTC",
        "phase": phase_cn.get(dossier.phase, dossier.phase),
        "earnings_date": dossier.earnings_date or "待定",
        "background": _background(dossier),
        "peer_comparison": _peer_comparison(dossier),
        "focus_ranking": _focus_ranking(dossier),
        "watch_metrics": _watch_metrics(dossier),
        "financial_snapshot": _financial_snapshot(dossier),
        "valuation": _valuation(dossier),
        "catalysts_risks": _catalysts_risks(dossier),
        "expectations_table": _expectations_table(dossier),
        "consensus": _consensus(dossier),
        "market_setup": _market_setup(dossier),
        "narrative": _narrative(dossier),
        "actuals": _actuals(dossier),
        "scorecard": _scorecard(dossier),
        "decision_tree": _decision_tree(dossier),
        "decision": _decision(dossier),
        "checklist": _checklist(dossier),
        "signal_chain": _signal_chain(dossier),
    }
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: slots.get(m.group(1), _NA), template)


def _out_dir() -> str:
    try:
        from ...config import load_macro_config
        return load_macro_config().output_dir
    except Exception:  # noqa: BLE001
        return ""


def write_report(dossier) -> Path | None:
    """Write (or re-write, at score time) the single lifecycle document."""
    out_dir = _out_dir()
    if not out_dir:
        return None
    folder = Path(out_dir)
    if not folder.is_dir():
        log.warning("pead report: output_dir missing — skipped: %s", folder)
        return None
    from ...data.fiscal import canonical_tag

    # Canonical tag surfaces the exact fiscal quarter ('2026Q2') so each company's
    # documents sort/browse by quarter; falls back to the sanitized label.
    path = folder / f"基本面分析-{dossier.symbol}-{canonical_tag(dossier.fiscal_label)}.md"
    path.write_text(render_dossier(dossier), encoding="utf-8")
    log.info("pead report (%s): %s", dossier.phase, path)
    return path
