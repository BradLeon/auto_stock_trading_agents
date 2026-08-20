"""Obsidian markdown report for a SectorReview.

Always creates its own file (行业分析-<label>-<date>.md) — never appends to the
user's own notes. Same-day reruns overwrite (idempotent). Unset/missing output
dir degrades to None.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...schemas.sector import SectorConfig, SectorReview

log = logging.getLogger("ats.agents.sector.report")


def render(review: SectorReview, cfg: SectorConfig) -> str:
    pead = _pead_symbols(cfg)
    lines = [
        f"# 🤖 行业分析 — {cfg.label}（{review.as_of:%Y-%m-%d}）",
        "",
        f"> 由 `ats sector review {cfg.name}` 自动生成（sector_analyst，每周评审）。",
        "",
        "## 行业状态",
        f"**Regime**: {review.regime}",
        "",
        review.summary,
        "",
    ]

    if review.layer_verdicts:
        lines += _layer_verdict_section(review, cfg, pead)
    elif review.layers:
        lines += _legacy_layer_section(review, cfg)

    if review.company_calls and not review.layer_verdicts:
        lines += ["", "## 个股观点", "", "| 层 | 代码 | 观点 | 信心 | 理由 |", "|---|---|---|---|---|"]
        layer_order = {layer.key: i for i, layer in enumerate(cfg.layers)}
        for c in sorted(review.company_calls, key=lambda c: layer_order.get(c.layer, 99)):
            sym = f"**{c.symbol}**" if c.symbol in pead else c.symbol
            label = next((la.label for la in cfg.layers if la.key == c.layer), c.layer)
            lines.append(f"| {label} | {sym} | {c.stance} | {c.conviction:.2f} | {c.rationale} |")

    lines += ["", "## 跨层轮动", "", review.rotation_advice or "（本轮未产出）",
              "", "## 主要风险", ""]
    lines += [f"- {r}" for r in review.top_risks]
    lines += ["", "---",
              f"*universe {len(cfg.all_symbols())} 家（**加粗** = PEAD 活体档案标的）；"
              f"数据截至 {review.as_of:%Y-%m-%d %H:%M} UTC*", ""]
    return "\n".join(lines)


def _layer_verdict_section(review: SectorReview, cfg: SectorConfig, pead: set) -> list[str]:
    """One section per layer: how much, why, what would flip it, and who within it."""
    budgets = _budgets(cfg)
    by_key = {v.layer_key: v for v in review.layer_verdicts}
    basket_by_key = {b.layer_key: b for b in review.baskets}
    order = {ly.key: i for i, ly in enumerate(cfg.layers)}

    lines = ["## 分层配置结论（需求沿 L1→L8 传导）", "",
             "| 层 | 配置 | 信心 | 预算 | 周期 | 说明 |", "|---|---|---|---|---|---|"]
    for key in sorted(by_key, key=lambda k: order.get(k, 99)):
        v = by_key[key]
        label = next((ly.label for ly in cfg.layers if ly.key == key), key)
        flags = []
        if not v.has_claims:
            flags.append("**无命题**（配置缺口）")
        if not v.cross_section_applicable:
            flags.append("截面不适用")
        budget = basket_by_key[key].layer_cap if key in basket_by_key else budgets.get(key)
        lines.append(f"| {label} | **{v.allocation}** | {v.confidence:.2f} | "
                     f"{budget:.1%} | {v.cycle_position or '—'} | {'；'.join(flags) or '—'} |")

    for key in sorted(by_key, key=lambda k: order.get(k, 99)):
        v = by_key[key]
        label = next((ly.label for ly in cfg.layers if ly.key == key), key)
        lines += ["", f"### {label} — {v.allocation}（信心 {v.confidence:.2f}）", ""]
        if not v.has_claims:
            # Never let this read as "the industry was quiet": it means the claims that
            # would have measured this layer were never written.
            lines += ["> ⚠️ **本层无命题**，结论仅来自快照与判据笔记。这是**配置缺口**"
                      "（该建的命题还没建），不是本季没人发声。", ""]
        if v.rationale:
            lines += [v.rationale, ""]
        if v.claim_attributions:
            lines += ["**议题归因**", ""] + [f"- {a}" for a in v.claim_attributions] + [""]
        if v.reversal_triggers:
            lines += ["**反转触发条件**（下一轮逐条核对）", ""]
            lines += [f"- [ ] {t}" for t in v.reversal_triggers] + [""]
        if v.name_calls:
            lines += ["| 代码 | 子层 | 观点 | 理由 |", "|---|---|---|---|"]
            for c in v.name_calls:
                sym = f"**{c.symbol}**" if c.symbol in pead else c.symbol
                mark = " ⚠️仅自述" if c.self_reported_only else ""
                lines.append(f"| {sym} | {c.subgroup or '—'} | {c.stance}{mark} "
                             f"| {c.rationale} |")
    return lines


def _legacy_layer_section(review: SectorReview, cfg: SectorConfig) -> list[str]:
    """Pre-2026-08-20 shape, kept so stored reviews still render."""
    lines = ["## 分层评审", "",
             "| 层 | 景气度 | 供需 | 定价权 | 资金流(proxy) | 周期 | 信号 |",
             "|---|---|---|---|---|---|---|"]
    for layer in cfg.layers:
        a = review.layer_assessment(layer.key)
        if a is None:
            continue
        lines.append(f"| {a.label or layer.label} | **{a.boom_score:.0f}** | {a.supply_demand} "
                     f"| {a.pricing_power} | {a.capital_flow} | {a.cycle_position} | {a.signal} |")
    return lines


def _budgets(cfg: SectorConfig) -> dict:
    return {ly.key: (ly.weight_cap or 0.0) for ly in cfg.layers}


def write(review: SectorReview, cfg: SectorConfig) -> Path | None:
    if not cfg.output_dir:
        log.info("sector report: output_dir unset — skipped")
        return None
    folder = Path(cfg.output_dir)
    if not folder.is_dir():
        log.warning("sector report: output_dir missing — skipped: %s", folder)
        return None
    path = folder / f"行业分析-{cfg.label}-{review.as_of:%Y-%m-%d}.md"
    path.write_text(render(review, cfg), encoding="utf-8")
    return path


def _pead_symbols(cfg: SectorConfig) -> set[str]:
    from ...config import is_pead_covered

    return {s for s in cfg.all_symbols() if is_pead_covered(s)}
