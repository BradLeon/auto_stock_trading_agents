"""Obsidian markdown for a TechnicalReview. Own file, never touches user notes."""

from __future__ import annotations

import logging
from pathlib import Path

from ...schemas.technical import TechnicalConfig, TechnicalReview

log = logging.getLogger("ats.agents.technical.report")


def _flags(d: dict) -> str:
    """The seven score components as ✓/✗, in the published order."""
    order = [("above_sma20", "P>MA20"), ("above_sma50", "P>MA50"),
             ("above_sma200", "P>MA200"), ("sma20_gt_sma50", "MA20>MA50"),
             ("sma50_gt_sma200", "MA50>MA200"), ("above_20d_ago", "P>20日前"),
             ("above_60d_ago", "P>60日前")]
    return " ".join(f"{'✓' if d.get(k) else '✗'}{lbl}" for k, lbl in order)


def render(review: TechnicalReview, cfg: TechnicalConfig) -> str:
    lines = [
        f"# 📐 技术面读数 — {cfg.label}（{review.as_of:%Y-%m-%d}）",
        "",
        "> 由 `ats technical review` 自动生成。**全部为确定性计算，无 LLM 参与。**",
        "> `建议敞口` 是风险敞口上限建议，**不是方向判断、不是交易指令**——"
        "是否采纳由 Chief 综合其他证据决定。",
        "",
        f"策略 `{review.strategy}` · 指纹 `{review.fingerprint}`",
        "",
        "## 市场状态",
        "",
        f"- VIX {review.vix if review.vix is not None else 'n/a'} · "
        f"VIX3M {review.vix3m if review.vix3m is not None else 'n/a'}"
        + (f" · 期限结构 {review.vix / review.vix3m:.3f}"
           if review.vix and review.vix3m else ""),
        f"- Tier1 恐慌（期限结构倒挂）: {'**是 → 建议清仓**' if review.market_panic else '否'}",
        "",
        "## 逐标的读数",
        "",
        "| 标的 | 评分 | 建议敞口 | 较上次 | 触发 | 收盘 | MA20 | MA50 | MA200 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    live = [r for r in review.readings if not r.stale]
    for r in sorted(live, key=lambda x: (x.target_exposure, x.symbol)):
        chg = "—"
        if r.prev_exposure is not None:
            chg = ("持平" if not r.changed
                   else f"{r.prev_exposure:.0%} → {r.target_exposure:.0%}")
        trig = "/".join([t for t, on in (("恐慌", r.panic_fired),
                                         ("破MA200", r.bear_fired)) if on]) or "—"
        fmt = lambda v: f"{v:.2f}" if v is not None else "—"   # noqa: E731
        lines.append(f"| {r.symbol} | {r.score}/7 | **{r.target_exposure:.0%}** | {chg} "
                     f"| {trig} | {fmt(r.close)} | {fmt(r.sma20)} | {fmt(r.sma50)} "
                     f"| {fmt(r.sma200)} |")

    lines += ["", "## 评分分项（可核对）", ""]
    for r in sorted(live, key=lambda x: x.symbol):
        lines.append(f"- **{r.symbol}** ({r.score}/7): {_flags(r.score_detail)}")

    stale = [r for r in review.readings if r.stale]
    if stale:
        lines += ["", "## 未评估", ""]
        lines += [f"- {r.symbol}: {r.note}" for r in stale]
    if review.skipped:
        lines += ["", f"无价格数据：{', '.join(review.skipped)}"]
    if review.notes:
        lines += ["", "## 运行备注", ""] + [f"- {n}" for n in review.notes]

    lines += ["", "---", f"*数据截至 {review.as_of:%Y-%m-%d %H:%M} UTC*", ""]
    return "\n".join(lines)


def write(review: TechnicalReview, cfg: TechnicalConfig) -> Path | None:
    if not cfg.output_dir:
        log.info("technical report: output_dir unset — skipped")
        return None
    folder = Path(cfg.output_dir)
    if not folder.is_dir():
        log.warning("technical report: output_dir missing — skipped: %s", folder)
        return None
    path = folder / f"技术面读数-{cfg.label}-{review.as_of:%Y-%m-%d}.md"
    path.write_text(render(review, cfg), encoding="utf-8")
    return path
