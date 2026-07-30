"""Macro review orchestration: assemble -> one Opus synthesis (equity-strategist)
-> persist. LLM failure never overwrites the stored latest review."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.macro_strategy import (
    SIGNALS,
    STANCES,
    MacroConfig,
    MacroReview,
    SectorTilt,
    ThemeAssess,
)
from ..base import run_structured
from . import assemble, indicators, regime
from .outputs import MacroReviewLLMView

log = logging.getLogger("ats.agents.macro.review")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def deterministic(cfg: MacroConfig, prior: MacroReview | None, *,
                  live_data: bool = True) -> dict:
    """Run the whole code-computed layer: readings, axes, quadrant, alerts.

    Computed here rather than inside assemble.build() on purpose — build() is
    monkeypatched in tests and its signature is depended on; keeping this out of
    it means the indicator layer can grow without touching that contract.

    `prior` supplies the hysteresis state: a quadrant flip stays provisional
    until a second consecutive review agrees (docs/MACRO_ANALYST.md §4.3).
    """
    if not live_data:
        return {}
    from ...data import macro as macro_data

    series = macro_data.fetch_series()
    if not series:
        log.warning("macro: no indicator series available; regime layer skipped")
        return {}
    readings = indicators.build_readings(series, macro_data.series_spec())
    return regime.assess(
        series, readings, overrides=cfg.regime,
        prior_quadrant=(prior.quadrant if prior else None),
        prior_state=(prior.quadrant_state if prior else "insufficient"),
        prior_weeks=(prior.quadrant_weeks if prior else 0))


def _det_block(det: dict) -> str:
    """Render the deterministic layer for the LLM prompt.

    Framed as "already computed, explain it" rather than as raw data: the model
    must not re-derive or restate these numbers differently (SKILL §8).
    """
    if not det:
        return ""
    lines = ["## 确定性读数（代码算出，**不得改写或重算**，你的工作是解释）", ""]
    lines.append(f"- 象限: **{det['quadrant']}**（{det['quadrant_state']}，"
                 f"连续 {det['quadrant_weeks']} 期） | 增长轴 {det['growth_axis']:+.2f}"
                 f" / 通胀轴 {det['inflation_axis']:+.2f}")
    if det.get("quadrant_reason"):
        lines.append(f"- 判定理由: {det['quadrant_reason']}")
    if det.get("focus_keys"):
        lines.append(f"- 本期重点指标: {', '.join(det['focus_keys'])}")
    if det.get("alerts"):
        lines.append("- ⚠️ 告警: " + "; ".join(det["alerts"]))

    dec = det.get("decomposition")
    if dec is not None:
        lines += ["", "### 名义利率分解（1 个月，恒等式：名义 = 实际 + 通胀补偿）",
                  f"- Δ名义 {dec.d_nominal_bp:+.0f}bp = Δ实际 {dec.d_real_bp:+.0f}bp"
                  f" + Δ通胀补偿 {dec.d_breakeven_bp:+.0f}bp",
                  f"- 判定: {dec.classification} → {dec.equity_read}"]
        if dec.real_yield_cause:
            lines.append(f"- 实际收益率下降成因: {dec.real_yield_cause}")

    if det.get("shock_vs_trend"):
        lines += ["", "### 趋势 vs 冲击（美联储会反应 or 看穿）"]
        lines += [f"- {s}" for s in det["shock_vs_trend"]]

    if det.get("axis_inputs"):
        lines += ["", "### 象限判定的逐项依据"]
        for a in det["axis_inputs"]:
            val = "n/a" if a.value is None else f"{a.value}"
            lines.append(f"- {a.label or a.key}: {val}（判据 {a.threshold}）"
                         f" → 得分 {a.score:+.2f}{'　' + a.note if a.note else ''}")

    live = [r for r in det.get("indicators", []) if r.level is not None]
    if live:
        lines += ["", "### 指标读数（水平 / Δ1m / Δ3m / z(3y) / 10y百分位）"]
        for r in live:
            unit = "bp" if r.unit == "pct" else "%"
            fmt = lambda v: "n/a" if v is None else f"{v:+.1f}{unit}"  # noqa: E731
            lines.append(
                f"- {r.label or r.key}: {r.level} | {fmt(r.d_1m)} / {fmt(r.d_3m)}"
                f" | z={r.z_3y if r.z_3y is not None else 'n/a'}"
                f" | {r.pct_10y if r.pct_10y is not None else 'n/a'}%"
                f"{'　⚠️数据过旧' if r.stale else ''}")
    return "\n".join(lines)


def run(name: str = "macro", *, use_llm: bool = True, live_data: bool = True) -> MacroReview:
    from ...config import load_macro_config
    from ...memory import get_store

    cfg = load_macro_config(name)
    store = get_store()
    prior = store.latest_macro_review(name)
    mc = assemble.build(cfg, live_data=live_data)
    det = deterministic(cfg, prior, live_data=live_data)
    log.info("macro %s: context %s | quadrant %s", name, mc.stats(),
             det.get("quadrant", "n/a"))

    if not use_llm:
        # The deterministic layer still runs and persists: verifying the numbers
        # before any model sees them is the whole point of the --no-llm path.
        review = MacroReview(name=name, as_of=_now(), regime="(no-llm)",
                             summary=f"context stats: {mc.stats()}", **det)
        store.save_macro_review(review)
        return review

    context = mc.as_context()
    block = _det_block(det)
    if block:
        context = f"{block}\n\n{context}"

    try:
        view: MacroReviewLLMView = run_structured("macro_strategist", MacroReviewLLMView,
                                                  context, skill_slug="macro-strategist")
    except Exception as exc:  # noqa: BLE001
        log.warning("macro review LLM failed for %s: %s", name, exc)
        return prior or MacroReview(name=name, as_of=_now(), regime="(LLM unavailable)")

    review = _to_review(name, cfg, view, det)
    store.save_macro_review(review)
    return review


def _to_review(name: str, cfg: MacroConfig, view: MacroReviewLLMView,
               det: dict | None = None) -> MacroReview:
    valid = {t.key: t.label for t in cfg.themes}
    themes = []
    for tv in view.themes:
        if tv.key not in valid:
            log.warning("macro %s: dropped unknown theme key %r", name, tv.key)
            continue
        themes.append(ThemeAssess(
            key=tv.key, label=valid[tv.key], direction=tv.direction,
            transmission=tv.transmission,
            signal=tv.signal if tv.signal in SIGNALS else "neutral", note=tv.note))

    tilts = [SectorTilt(sector=tv.sector.strip(),
                        stance=tv.stance if tv.stance in STANCES else "中性",
                        rationale=tv.rationale)
             for tv in view.sector_tilts if tv.sector.strip()]

    # `**det` last is deliberate: the deterministic fields are code-owned and must
    # win outright if a future view ever grows a same-named field.
    return MacroReview(
        name=name, as_of=_now(), regime=view.regime, summary=view.summary,
        rate_path=view.rate_path, sector_tilts=tilts,
        asset_implications=view.asset_implications, themes=themes,
        top_risks=view.top_risks, falsifier=view.falsifier, **(det or {}))
