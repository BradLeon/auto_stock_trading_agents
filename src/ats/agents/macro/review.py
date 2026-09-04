"""Macro review orchestration: assemble -> one Opus synthesis (equity-strategist)
-> persist. LLM failure never overwrites the stored latest review."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.macro_strategy import (
    SIGNALS,
    STANCES,
    FactSetDiagnosticSummary,
    FactSetMaterialSummary,
    FactSetObservationSummary,
    MacroConfig,
    MacroReview,
    SectorTilt,
    ThemeAssess,
)
from ..base import run_structured
from . import assemble, indicators, regime
from .outputs import MacroReviewLLMView

log = logging.getLogger("ats.agents.macro.review")

_FACTSET_JUDGMENT_METRICS = {
    "growth_quality": {
        "earnings.eps.yoy_growth", "earnings.revenue.yoy_growth"},
    "concentration": {
        "earnings.eps.yoy_growth", "earnings.reporting.coverage"},
    "surprise_drivers": {
        "earnings.eps.surprise_pct", "earnings.revenue.surprise_pct",
        "earnings.net_profit_margin"},
    "guidance_margin_consistency": {
        "earnings.guidance.positive_count", "earnings.guidance.negative_count",
        "earnings.net_profit_margin", "earnings.revision.improved_sector_count"},
    "valuation": {
        "valuation.forward_pe", "valuation.forward_pe.average_5y",
        "valuation.forward_pe.average_10y", "valuation.trailing_pe",
        "valuation.trailing_pe.average_5y", "valuation.trailing_pe.average_10y"},
    "analyst_expectations": {
        "consensus.rating.buy_share", "consensus.rating.hold_share",
        "consensus.rating.sell_share", "consensus.target.upside"},
    "conflicts_and_limitations": {
        "earnings.eps.yoy_growth", "earnings.revenue.yoy_growth",
        "earnings.net_profit_margin"},
    "market_and_sector_implications": {
        "earnings.eps.yoy_growth", "earnings.revision.improved_sector_count",
        "earnings.guidance.positive_count", "valuation.forward_pe"},
}

_FACTSET_JUDGMENT_TOPICS = {
    "concentration": {"earnings_concentration", "excluding_major_companies"},
    "surprise_drivers": {"gaap_non_gaap", "margin_drivers"},
    "guidance_margin_consistency": {"margin_drivers"},
    "valuation": {"valuation_and_sentiment"},
    "analyst_expectations": {"valuation_and_sentiment"},
    "conflicts_and_limitations": {
        "earnings_concentration", "excluding_major_companies", "gaap_non_gaap"},
    "market_and_sector_implications": {"sector_contribution"},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def deterministic(cfg: MacroConfig, prior: MacroReview | None, *,
                  live_data: bool = True, as_of: datetime | None = None) -> dict:
    """Run the whole code-computed layer: readings, axes, quadrant, alerts.

    Computed here rather than inside assemble.build() on purpose — build() is
    monkeypatched in tests and its signature is depended on; keeping this out of
    it means the indicator layer can grow without touching that contract.

    `prior` supplies the hysteresis state: a quadrant flip stays provisional
    until a second consecutive review agrees (docs/MACRO_ANALYST.md §4.3).
    """
    if not live_data:
        return {}
    # FRED/yfinance indicators are time-sensitive runtime inputs.  They are
    # intentionally kept behind the runtime facade instead of being treated as
    # governed, persisted structured observations.
    from ...data.runtime import macro as macro_data

    series = macro_data.fetch_series()
    if not series:
        log.warning("macro: no indicator series available; regime layer skipped")
        return {}
    readings = indicators.build_readings(series, macro_data.series_spec())
    out = regime.assess(
        series, readings, overrides=cfg.regime,
        prior_quadrant=(prior.quadrant if prior else None),
        prior_state=(prior.quadrant_state if prior else "insufficient"),
        prior_weeks=(prior.quadrant_weeks if prior else 0))
    if prior is not None:
        from ...config import load_events

        now = as_of or _now()
        out["comparison_as_of"] = prior.as_of
        out["data_deltas"] = indicators.detect_deltas(
            readings, prior, events=load_events(), through=now.date())
    return out


def _earnings_backdrop(mc):
    """Return the governed compatibility DTO, if the product supplied one.

    The former fallback parsed a downloaded PDF during Macro review.  That
    parser has been retired: a missing or unreleased DataProducts snapshot is
    represented as unavailable rather than reconstructed from local files.
    """
    return getattr(mc, "earnings_backdrop", None)


def _factset_material(mc) -> FactSetMaterialSummary | None:
    """Persist a compact manifest of exactly what the model received."""
    packet = getattr(mc, "earnings_packet", None)
    if packet is None or not packet.report.version_id:
        return None
    observations = []
    for items in packet.observation_groups.values():
        for item in items:
            observations.append(FactSetObservationSummary(
                observation_id=item.observation_id, metric_id=item.metric_id,
                period=item.period, value=item.value, unit=item.unit,
                estimate_state=item.estimate_state,
                page_numbers=sorted({anchor.page_number for anchor in item.evidence
                                     if anchor.page_number})))
    observations.sort(key=lambda item: (item.metric_id, item.period))
    pages: dict[str, list[int]] = {}
    for item in packet.narrative_evidence:
        pages.setdefault(item.topic, []).append(item.page_number)
    return FactSetMaterialSummary(
        report_date=packet.report.report_date, version_id=packet.report.version_id,
        freshness=packet.status.freshness, warnings=list(packet.status.warnings),
        observations=observations,
        diagnostics=[FactSetDiagnosticSummary(
            diagnostic_id=item.diagnostic_id, label=item.label, value=item.value,
            unit=item.unit, input_observation_ids=item.input_observation_ids)
            for item in packet.diagnostics],
        narrative_pages={key: sorted(set(value)) for key, value in pages.items()})


def _det_block(det: dict) -> str:
    """Render the deterministic layer for the LLM prompt.

    Framed as "already computed, explain it" rather than as raw data: the model
    must not re-derive or restate these numbers differently (SKILL §8).
    """
    if not det:
        return ""
    lines = ["## 确定性读数（代码算出，**不得改写或重算**，你的工作是解释）", ""]
    if "quadrant" in det:
        lines.append(f"- 象限: **{det['quadrant']}**（{det['quadrant_state']}，"
                     f"连续 {det['quadrant_weeks']} 期） | 增长轴 {det['growth_axis']:+.2f}"
                     f" / 通胀轴 {det['inflation_axis']:+.2f}")
    if det.get("quadrant_reason"):
        lines.append(f"- 判定理由: {det['quadrant_reason']}")
    if det.get("focus_keys"):
        lines.append(f"- 本期重点指标: {', '.join(det['focus_keys'])}")
    if det.get("alerts"):
        lines.append("- ⚠️ 告警: " + "; ".join(det["alerts"]))

    deltas = det.get("data_deltas") or []
    if deltas:
        lines += ["", "## 距上次正式评审的新增/修订数据（最高优先级）"]
        for d in deltas:
            published = f"发布 {d.release_date}" if d.release_date else "本次检测到"
            observed = f"数据期 {d.observation_date}" if d.observation_date else ""
            before = "n/a" if d.previous_level is None else str(d.previous_level)
            after = "n/a" if d.current_level is None else str(d.current_level)
            kind = {"new_release": "新发布", "revision": "修订",
                    "newly_tracked": "新增跟踪"}.get(d.change_kind, d.change_kind)
            lines.append(f"- **{d.label or d.key}** [{kind}] {published} {observed}: "
                         f"{before} → {after}")
        lines.append("- 必须在 conclusion_delta 中逐项说明这些变化是否改变上次结论；"
                     "不能只复述当前水平。")

    dec = det.get("decomposition")
    if dec is not None:
        lines += ["", "### 名义利率分解（1 个月，恒等式：名义 = 实际 + 通胀补偿）",
                  f"- Δ名义 {dec.d_nominal_bp:+.0f}bp = Δ实际 {dec.d_real_bp:+.0f}bp"
                  f" + Δ通胀补偿 {dec.d_breakeven_bp:+.0f}bp",
                  f"- 判定: {dec.classification} → {dec.equity_read}"]
        if dec.real_yield_cause:
            lines.append(f"- 实际收益率下降成因: {dec.real_yield_cause}")

    bd = det.get("earnings_backdrop")
    if bd is not None and not bd.degraded:
        lines += ["", "### 总量盈利周期（S&P500 指数层面，FactSet 抽取）"]
        if bd.fwd_pe is not None:
            vs5 = (f"，相对 5 年均值 {bd.fwd_pe_5y_avg} 偏离 {bd.fwd_pe_vs_5y_pct:+.1f}%"
                   if bd.fwd_pe_vs_5y_pct is not None else "")
            lines.append(f"- 前瞻 12 个月 P/E: {bd.fwd_pe}{vs5}"
                         f"（10 年均值 {bd.fwd_pe_10y_avg}）")
        if bd.growth_pct is not None:
            lines.append(f"- {bd.quarter} 盈利增速: {bd.growth_pct:+.1f}%"
                         f"（{bd.growth_basis}）")
        if bd.revision_pp is not None:
            lines.append(f"- 盈利修正: 较季初（{bd.prior_as_of}）"
                         f"{bd.prior_growth_pct:+.1f}% → {bd.growth_pct:+.1f}%，"
                         f"**{bd.revision_pp:+.1f}pp**")
        if bd.sectors_higher is not None:
            way = {"upward": "上修", "downward": "下修"}.get(bd.revision_direction, "变动")
            lines.append(f"- 修正广度: {bd.sectors_higher} 个板块{way}")
        if bd.pct_reported is not None:
            lines.append(f"- 披露进度: {bd.pct_reported:.0f}% 已报，"
                         f"EPS 超预期 {bd.pct_eps_beat:.0f}%、营收超预期 {bd.pct_revenue_beat:.0f}%")
        if bd.guidance_negative is not None:
            lines.append(f"- {bd.guidance_quarter} 指引: 负面 {bd.guidance_negative} 家 / "
                         f"正面 {bd.guidance_positive} 家")
        lines.append("- ⚠️ 以上均为**指数层面总量**，不得据此推断任何单个公司的盈利。")
    elif bd is not None and bd.degraded:
        lines += ["", "### 总量盈利周期",
                  f"- ⚠️ 结构化抽取失败（{'; '.join(bd.notes) or '未知原因'}），"
                  "本期只有下方 FactSet 散文可用，请勿凭空补数字。"]

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


def _prior_block(prior: MacroReview | None) -> str:
    if prior is None:
        return ""
    tilts = "; ".join(f"{t.sector}={t.stance}" for t in prior.sector_tilts) or "—"
    return (
        f"## 上次正式评审（比较基准：{prior.as_of:%Y-%m-%d}）\n"
        f"- 象限: {prior.quadrant}（{prior.quadrant_state}） | "
        f"增长 {prior.growth_axis:+.2f} / 通胀 {prior.inflation_axis:+.2f}\n"
        f"- regime: {prior.regime}\n- 利率路径: {prior.rate_path or '—'}\n"
        f"- 板块倾斜: {tilts}\n"
        "请只把它用于比较变化，不得把旧数字当作本期事实。")


def _fallback_conclusion_delta(prior: MacroReview | None, regime_text: str,
                               det: dict) -> str:
    if prior is None:
        return "首次建立比较基准；下次评审起将逐项展示数据与结论变化。"
    q = det.get("quadrant", "transition")
    g = det.get("growth_axis", 0.0)
    inf = det.get("inflation_axis", 0.0)
    changed = "结论不变" if q == prior.quadrant and regime_text == prior.regime else "结论已更新"
    return (f"{changed}：象限 {prior.quadrant} → {q}；"
            f"增长轴 {prior.growth_axis:+.2f} → {g:+.2f}；"
            f"通胀轴 {prior.inflation_axis:+.2f} → {inf:+.2f}。")


def run(name: str = "macro", *, use_llm: bool = True, live_data: bool = True) -> MacroReview:
    from ...config import load_macro_config
    from ...memory import get_store

    cfg = load_macro_config(name)
    store = get_store()
    started = _now()
    latest = store.latest_macro_review(name)
    # A same-day retry still compares with the previous formal period.  This is
    # essential for catch-up: an incomplete first rerun must not consume the
    # 1-Aug→8-Aug delta before the corrected report is produced.
    prior = store.latest_macro_review_before(name, started.date()) or (
        latest if latest is not None and latest.as_of.date() < started.date() else None)
    mc = assemble.build(cfg, live_data=live_data)
    det = deterministic(cfg, prior, live_data=live_data, as_of=started)
    # Parse the aggregate earnings backdrop out of the FactSet text assemble
    # already fetched — no second PDF read. Index level only (§2 role boundary).
    backdrop = _earnings_backdrop(mc)
    if backdrop is not None:
        det["earnings_backdrop"] = backdrop
    material = _factset_material(mc)
    if material is not None:
        det["factset_material"] = material
    log.info("macro %s: context %s | quadrant %s", name, mc.stats(),
             det.get("quadrant", "n/a"))

    if not use_llm:
        # The deterministic layer still runs and persists: verifying the numbers
        # before any model sees them is the whole point of the --no-llm path.
        review = MacroReview(
            name=name, as_of=started, regime="(no-llm)",
            summary=f"context stats: {mc.stats()}",
            conclusion_delta=_fallback_conclusion_delta(prior, "(no-llm)", det), **det)
        store.save_macro_review(review)
        return review

    context = mc.as_context()
    block = _det_block(det)
    comparison = _prior_block(prior)
    if block or comparison:
        context = "\n\n".join(x for x in (comparison, block, context) if x)

    try:
        view: MacroReviewLLMView = run_structured("macro_strategist", MacroReviewLLMView,
                                                  context, skill_slug="macro-strategist")
    except Exception as exc:  # noqa: BLE001
        log.warning("macro review LLM failed for %s: %s", name, exc)
        return latest or prior or MacroReview(name=name, as_of=started, regime="(LLM unavailable)")

    review = _to_review(name, cfg, view, det, prior=prior, as_of=started)
    store.save_macro_review(review)
    return review


def _to_review(name: str, cfg: MacroConfig, view: MacroReviewLLMView,
               det: dict | None = None, *, prior: MacroReview | None = None,
               as_of: datetime | None = None) -> MacroReview:
    valid = {t.key: t.label for t in cfg.themes}
    label_to_key = {t.label: t.key for t in cfg.themes}
    themes = []
    for tv in view.themes:
        key = tv.key if tv.key in valid else label_to_key.get(tv.key, "")
        if not key:
            log.warning("macro %s: dropped unknown theme key %r", name, tv.key)
            continue
        themes.append(ThemeAssess(
            key=key, label=valid[key], direction=tv.direction,
            transmission=tv.transmission,
            signal=tv.signal if tv.signal in SIGNALS else "neutral", note=tv.note))

    tilts = [SectorTilt(sector=tv.sector.strip(),
                        stance=tv.stance if tv.stance in STANCES else "中性",
                        rationale=tv.rationale)
             for tv in view.sector_tilts if tv.sector.strip()]

    assessment = view.factset_earnings_assessment
    material = (det or {}).get("factset_material")
    if assessment is not None and material is not None:
        valid_metrics = {item.metric_id for item in material.observations}
        valid_pages = {page for pages in material.narrative_pages.values() for page in pages}
        updates = {}
        for field_name, judgment in assessment:
            required_metrics = _FACTSET_JUDGMENT_METRICS.get(field_name, set())
            metric_ids = [
                metric for metric in judgment.metric_ids if metric in valid_metrics]
            metric_ids += sorted(
                metric for metric in required_metrics
                if metric in valid_metrics and metric not in metric_ids)
            page_numbers = [
                page for page in judgment.page_numbers if page in valid_pages]
            for topic in _FACTSET_JUDGMENT_TOPICS.get(field_name, set()):
                page_numbers += [
                    page for page in material.narrative_pages.get(topic, [])
                    if page not in page_numbers]
            updates[field_name] = judgment.model_copy(update={
                "metric_ids": metric_ids,
                "page_numbers": sorted(page_numbers),
            })
        assessment = assessment.model_copy(update=updates)
    elif material is None:
        assessment = None

    # `**det` last is deliberate: the deterministic fields are code-owned and must
    # win outright if a future view ever grows a same-named field.
    return MacroReview(
        name=name, as_of=as_of or _now(), regime=view.regime, summary=view.summary,
        conclusion_delta=(view.conclusion_delta or
                          _fallback_conclusion_delta(prior, view.regime, det or {})),
        rate_path=view.rate_path, sector_tilts=tilts,
        asset_implications=view.asset_implications, themes=themes,
        top_risks=view.top_risks, falsifier=view.falsifier,
        factset_earnings_assessment=assessment, **(det or {}))
