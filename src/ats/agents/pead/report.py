"""Obsidian markdown reports for PEAD prep and score phases."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("ats.agents.pead.report")


def render_prep(dossier) -> str:
    """Render a prep-phase dossier to markdown."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sym = dossier.symbol
    fl = dossier.fiscal_label
    lines = [
        f"# 基本面分析 — {sym} {fl}",
        f"*更新: {now:%Y-%m-%d %H:%M} UTC | phase: prep*",
        "",
    ]

    es = dossier.expectation_set
    if es:
        if es.narrative:
            lines += ["## 核心叙事", "", es.narrative, ""]
        if getattr(es, "focus_ranking", None):
            lines += ["## 本季关键变量（优先级排序）", ""]
            for i, item in enumerate(es.focus_ranking, 1):
                lines.append(f"{i}. {item}")
            lines.append("")
        if es.expectations:
            lines += ["## 预期设置（悲观 / 基准 / 乐观）", "",
                      "| 维度 | 悲观 | 基准 | 乐观 |",
                      "|---|---|---|---|"]
            for r in es.expectations:
                lines.append(
                    f"| {r.metric or r.dim_key} | {r.conservative or '—'} "
                    f"| {r.neutral or '—'} | {r.optimistic or '—'} |")
            lines.append("")

    ms = dossier.market_setup
    if ms:
        lines += ["## 市场设置", ""]
        if ms.run_up_vs_sector_pct is not None:
            lines.append(f"- 抢跑 vs 板块 ETF: **{ms.run_up_vs_sector_pct:+.1f}%**")
        if ms.expected_move_pct is not None:
            lines.append(f"- 期权隐含预期波幅 (EM): **±{ms.expected_move_pct:.1f}%**")
        lines.append("")

    sc = dossier.signal_chain
    if sc:
        lines += ["## 信号链", "",
                  "| 标的 | 角色 | 描述 |",
                  "|---|---|---|"]
        for item in sc:
            desc = getattr(item, "description", "") or getattr(item, "rationale", "") or ""
            lines.append(f"| {item.symbol} | {item.role} | {desc[:80]} |")
        lines.append("")

    return "\n".join(lines)


def render_score(dossier) -> str:
    """Render a score-phase dossier to markdown."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sym = dossier.symbol
    fl = dossier.fiscal_label
    lines = [
        f"# PEAD 评分 — {sym} {fl}",
        f"*更新: {now:%Y-%m-%d %H:%M} UTC | phase: score*",
        "",
    ]

    if dossier.scorecard:
        sc = dossier.scorecard
        lines += [
            "## Scorecard",
            "",
            f"**总分**: {sc.total:+.2f} | **门槛**: {sc.threshold:+.1f} | **评级**: {sc.band}",
            "",
        ]
        if sc.lines:
            lines += ["| 维度 | 得分 | 说明 |", "|---|---|---|"]
            for ln in sc.lines:
                lines.append(f"| {ln.label} | {ln.score:+.2f} | {(ln.note or '')[:80]} |")
            lines.append("")

    if dossier.decision_summary:
        lines += ["## 分析师建议", "", dossier.decision_summary, ""]

    ms = dossier.market_setup
    if ms:
        lines += ["## 市场设置", ""]
        if ms.run_up_vs_sector_pct is not None:
            lines.append(f"- 抢跑 vs 板块 ETF: **{ms.run_up_vs_sector_pct:+.1f}%**")
        if ms.expected_move_pct is not None:
            lines.append(f"- 期权隐含预期波幅 (EM): **±{ms.expected_move_pct:.1f}%**")
        lines.append("")

    es = dossier.expectation_set
    if es and es.narrative:
        lines += ["## 叙事", "", es.narrative, ""]

    return "\n".join(lines)


def _out_dir() -> str:
    try:
        from ...config import load_macro_config
        return load_macro_config().output_dir
    except Exception:  # noqa: BLE001
        return ""


def write_prep(dossier) -> Path | None:
    out_dir = _out_dir()
    if not out_dir:
        return None
    folder = Path(out_dir)
    if not folder.is_dir():
        log.warning("pead report: output_dir missing — skipped: %s", folder)
        return None
    fl_safe = dossier.fiscal_label.replace(" ", "-").replace("/", "-")
    path = folder / f"基本面分析-{dossier.symbol}-{fl_safe}.md"
    path.write_text(render_prep(dossier), encoding="utf-8")
    log.info("pead prep report: %s", path)
    return path


def write_score(dossier) -> Path | None:
    out_dir = _out_dir()
    if not out_dir:
        return None
    folder = Path(out_dir)
    if not folder.is_dir():
        log.warning("pead report: output_dir missing — skipped: %s", folder)
        return None
    fl_safe = dossier.fiscal_label.replace(" ", "-").replace("/", "-")
    path = folder / f"PEAD评分-{dossier.symbol}-{fl_safe}.md"
    path.write_text(render_score(dossier), encoding="utf-8")
    log.info("pead score report: %s", path)
    return path
