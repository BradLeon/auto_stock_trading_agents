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
        "## 分层评审（需求沿 L1→L6 传导）",
        "",
        "| 层 | 景气度 | 供需 | 定价权 | 资金流(proxy) | 周期 | 信号 |",
        "|---|---|---|---|---|---|---|",
    ]
    by_key = {a.key: a for a in review.layers}
    for layer in cfg.layers:
        a = by_key.get(layer.key)
        if a is None:
            continue
        lines.append(f"| {a.label or layer.label} | **{a.boom_score:.0f}** | {a.supply_demand} "
                     f"| {a.pricing_power} | {a.capital_flow} | {a.cycle_position} | {a.signal} |")

    lines += ["", "## 个股观点", "", "| 层 | 代码 | 观点 | 信心 | 理由 |", "|---|---|---|---|---|"]
    layer_order = {layer.key: i for i, layer in enumerate(cfg.layers)}
    for c in sorted(review.company_calls, key=lambda c: layer_order.get(c.layer, 99)):
        sym = f"**{c.symbol}**" if c.symbol in pead else c.symbol
        label = next((la.label for la in cfg.layers if la.key == c.layer), c.layer)
        lines.append(f"| {label} | {sym} | {c.stance} | {c.conviction:.2f} | {c.rationale} |")

    lines += ["", "## 轮动建议", "", review.rotation_advice, "", "## 主要风险", ""]
    lines += [f"- {r}" for r in review.top_risks]
    lines += ["", "---",
              f"*universe {len(cfg.all_symbols())} 家（**加粗** = PEAD 活体档案标的）；"
              f"数据截至 {review.as_of:%Y-%m-%d %H:%M} UTC*", ""]
    return "\n".join(lines)


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
    from ...config import is_pead_target

    return {s for s in cfg.all_symbols() if is_pead_target(s)}
