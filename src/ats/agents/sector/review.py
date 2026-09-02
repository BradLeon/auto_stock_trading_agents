"""Sector review orchestration: assemble context -> one Opus synthesis -> persist.

LLM failure never overwrites the stored latest review — it returns the prior one
(or a stub) and saves nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.sector import STANCES, CompanyCall, LayerAssessment, SectorConfig, SectorReview
from ..base import run_structured
from . import assemble
from .outputs import SectorReviewLLMView

log = logging.getLogger("ats.agents.sector.review")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(name: str = "ai_hardware", *, use_llm: bool = True, live_data: bool = True,
        layers: bool = True, write_reports: bool = True) -> SectorReview:
    """Weekly review. `layers=False` falls back to the pre-2026-08-20 single synthesis.

    New order (design D8), per layer: quant cross-section -> structure analyst ->
    LAYER analyst; then one cross-layer rotation pass over the eight verdicts. The
    rotation is last because it is the only judgement that needs every layer at once;
    everything before it is layer-local and therefore independent.
    """
    from ...config import load_sector_config
    from ...memory import get_store

    cfg = load_sector_config(name)
    store = get_store()

    if layers:
        return _run_layered(name, cfg, store, use_llm=use_llm, live_data=live_data,
                            write_reports=write_reports)

    sc = assemble.build(cfg, live_data=live_data, allow_llm_evidence=use_llm)
    log.info("sector %s: context %s", name, sc.stats())

    if not use_llm:
        review = SectorReview(sector=name, as_of=_now(), regime="(no-llm)",
                              summary=f"context stats: {sc.stats()}")
        store.save_sector_review(review)
        return review

    try:
        view: SectorReviewLLMView = run_structured("sector_analyst", SectorReviewLLMView,
                                                   sc.as_context(), skill_slug="sector-analyst")
    except Exception as exc:  # noqa: BLE001
        log.warning("sector review LLM failed for %s: %s", name, exc)
        prior = store.latest_sector_review(name)
        return prior or SectorReview(sector=name, as_of=_now(), regime="(LLM unavailable)")

    review = _to_review(name, cfg, view)
    store.save_sector_review(review)
    return review


def _run_layered(name: str, cfg, store, *, use_llm: bool, live_data: bool,
                 write_reports: bool = True) -> SectorReview:
    from . import assemble as sector_assemble, cross_section, layer_review, rotation

    prior = store.latest_sector_review(name)
    bind = _bind_layer_budget()
    verdicts, raw_baskets, failed = [], [], []
    allocations: dict[str, str | None] = {}
    pending_reports: list = []
    # Every layer's assessments, keyed by layer — the viz bundle's live-path input
    # (mirrors what the offline CLI reconstructs via store.claim_assessments_on()).
    assessments_by_layer: dict[str, list] = {}
    # Captured once so every layer's ClaimAssessment snapshots to the SAME moment,
    # not eight microseconds apart — mirrors chain/report.py's `render`.
    run_at = _now()

    for layer in cfg.layers:
        prior_v = _prior_verdict(cfg, prior, layer)
        basket, rows = None, None
        try:
            # Budget first at FULL cap: the ranking and the relative split are
            # independent of the verdict, and the verdict needs the ranking to answer
            # "who". The total is re-scaled below once the verdict exists.
            if live_data:
                # run_layer returns (rows, basket) — the rows are the working set (the
                # report's factor table renders from them), the basket is the persisted
                # shape that carries onward.
                rows, basket = cross_section.run_layer(name, layer.key, persist=False,
                                                       structure=use_llm)
        except Exception as exc:  # noqa: BLE001 - a layer without prices still gets a verdict
            log.warning("cross-section failed for %s: %s", layer.key, exc)

        # Computed ONCE and handed to both the prompt and the report: running the
        # engine twice would re-read the ledger and could disagree with itself.
        try:
            assessments = sector_assemble.layer_assessments(
                cfg, layer, as_of=run_at, allow_llm=use_llm)
        except Exception as exc:  # noqa: BLE001 - a layer without evidence still gets a verdict
            log.warning("claim assessment failed for %s: %s", layer.key, exc)
            assessments = []
        assessments_by_layer[layer.key] = assessments

        # Snapshot the verdicts so the viz bundle and `claim_assessment_history` can see
        # this run without re-invoking the judge — `_run_layered` used to compute these
        # and then drop them; `save_claim_assessment` was only ever called from
        # `ats evidence report`, so the stored snapshot was stuck on whatever layer keys
        # that command last ran under. Best-effort: one bad row must not cost the layer
        # its verdict.
        for a in assessments:
            try:
                store.save_claim_assessment(a)
            except Exception as exc:  # noqa: BLE001
                log.warning("claim assessment persist skipped for %s: %s", a.claim_id, exc)

        verdict, ok = layer_review.run(cfg, layer, basket=basket, prior=prior_v,
                                       use_llm=use_llm, assessments=assessments)
        if not ok:
            failed.append(layer.key)
        else:
            verdicts.append(verdict)

        if basket is not None:
            raw_baskets.append((layer, basket))
        allocations[layer.key] = verdict.allocation if (ok and bind) else None
        if ok:
            pending_reports.append((layer, verdict, assessments, rows))

    # Group ceilings can only be applied once EVERY member's ask is known — two 超配
    # halves of a split layer add up past the pre-split envelope, and neither half can
    # see the other. Ranks and the within-layer split are untouched; only totals move,
    # and only downward.
    budgets = cross_section.budgets_for(cfg, allocations)
    baskets = [_rescaled(b, budgets.get(ly.key, b.layer_cap)) for ly, b in raw_baskets]

    view = rotation.run(cfg, verdicts, use_llm=use_llm) if verdicts else None
    review = _assemble_review(name, cfg, verdicts, baskets, view, failed)

    # 一层一份报告，且只在这里写 —— `sector crosssection` 不再写文件，否则同一层会出现
    # 两份互相不同步的文档（其中一份的 layer_cap 还是没经过配置结论的半成品）。
    if write_reports and verdicts:
        from . import report as report_mod

        by_key = {b.layer_key: b for b in baskets}
        for layer, verdict, assessments, rows in pending_reports:
            try:
                path = report_mod.write_layer(verdict, layer, cfg,
                                              basket=by_key.get(layer.key),
                                              assessments=assessments, rows=rows)
                if path:
                    log.info("layer report: %s", path)
            except Exception as exc:  # noqa: BLE001 - a report must not fail the run
                log.warning("layer report failed for %s: %s", layer.key, exc)
    if not verdicts:
        # Nothing was actually assessed. Persisting would put an empty review in front
        # of every downstream reader as `latest` — the exact failure the single-synthesis
        # path was careful to avoid ("LLM 失败不落库，绝不用 stub 覆盖 latest").
        log.warning("sector %s: no layer produced a verdict — not persisting", name)
        return prior or review
    store.save_sector_review(review)

    if write_reports:
        try:
            from . import viz

            bundle = viz.build_bundle(cfg, review, assessments_by_layer=assessments_by_layer)
            html_path = viz.write_html(bundle, cfg.output_dir)
            if html_path:
                log.info("sector viz html: %s", html_path)
        except Exception as exc:  # noqa: BLE001 - the html dashboard must not fail the run
            log.warning("sector viz html failed for %s: %s", name, exc)
    return review


def _bind_layer_budget() -> bool:
    from ...config import load_pead_global

    try:
        return bool(load_pead_global()["sector_review"].get("bind_layer_budget", True))
    except Exception:  # noqa: BLE001 - a missing switch must not stop the run
        return True


def _prior_verdict(cfg, prior, layer):
    """This layer's previous verdict, resolving keys that predate a split/rename."""
    if prior is None:
        return None
    for key in [layer.key, *layer.legacy_keys]:
        v = prior.verdict_for(key)
        if v is not None:
            return v
    return None


def _rescaled(basket, target: float):
    """Scale a basket's weights to `target` in total. Ranks and ratios are untouched."""
    if sum(r.weight for r in basket.rows) <= 0 or not basket.layer_cap:
        return basket.model_copy(update={"layer_cap": target})
    factor = target / basket.layer_cap
    rows = [r.model_copy(update={"weight": r.weight * factor}) for r in basket.rows]
    return basket.model_copy(update={"layer_cap": target, "rows": rows})


def _assemble_review(name, cfg, verdicts, baskets, view, failed) -> SectorReview:
    labels = {ly.key: ly.label for ly in cfg.layers}
    calls = [CompanyCall(symbol=c.symbol, layer=v.layer_key, stance=c.stance,
                         conviction=v.confidence, rationale=c.rationale)
             for v in verdicts for c in v.name_calls]
    regime = view.regime if view else "(轮动合成不可用)"
    summary = view.summary if view else ""
    if failed:
        note = "本轮未产出结论的层：" + "、".join(labels.get(k, k) for k in failed)
        summary = f"{summary}\n⚠️ {note}".strip()
    return SectorReview(
        sector=name, as_of=_now(), regime=regime, summary=summary,
        layers=[], company_calls=calls, baskets=baskets, layer_verdicts=verdicts,
        rotation_advice=(view.rotation_advice if view else ""),
        top_risks=(list(view.top_risks) if view else []))


def _to_review(name: str, cfg: SectorConfig, view: SectorReviewLLMView) -> SectorReview:
    valid_keys = {layer.key: layer.label for layer in cfg.layers}
    layers = []
    for lv in view.layers:
        if lv.key not in valid_keys:
            log.warning("sector %s: dropped unknown layer key %r", name, lv.key)
            continue
        layers.append(LayerAssessment(
            key=lv.key, label=valid_keys[lv.key],
            boom_score=max(0.0, min(100.0, float(lv.boom_score))),
            supply_demand=lv.supply_demand, pricing_power=lv.pricing_power,
            capital_flow=lv.capital_flow, cycle_position=lv.cycle_position,
            signal=lv.signal if lv.signal in ("bullish", "neutral", "bearish") else "neutral",
            note=lv.note))

    missing = set(valid_keys) - {a.key for a in layers}
    if missing:
        log.warning("sector %s: LLM omitted layer assessments for %s", name, sorted(missing))

    universe = set(cfg.all_symbols())
    calls = []
    for cv in view.company_calls:
        sym = cv.symbol.strip().upper()
        if sym not in universe:
            # allow lower/mixed-case echoes of KRX/TSE style symbols
            match = next((u for u in universe if u.upper() == sym), None)
            if match is None:
                log.warning("sector %s: dropped non-universe call %r", name, cv.symbol)
                continue
            sym = match
        calls.append(CompanyCall(
            symbol=sym, layer=cv.layer if cv.layer in valid_keys else (cfg.layer_of(sym) or ""),
            stance=cv.stance if cv.stance in STANCES else "持有",
            conviction=max(0.0, min(1.0, float(cv.conviction))),
            rationale=cv.rationale))

    # Carry forward baskets computed for the SAME day. A review always creates a new
    # row, so re-running it after the cross-section stranded that day's baskets on the
    # older row — `latest_sector_review` then returned a basket-less review and the
    # Chief saw no cross-section at all. Older days are not carried: a basket describes
    # one run's prices and factor values, and re-attaching last week's would be a lie
    # about when it was computed.
    now = _now()
    baskets = []
    try:
        from ...memory import get_store

        prior = get_store().latest_sector_review(name)
        if prior is not None and prior.as_of.date() == now.date():
            baskets = prior.baskets
    except Exception as exc:  # noqa: BLE001 - carry-forward is best-effort
        log.info("basket carry-forward skipped for %s: %s", name, exc)
    return SectorReview(sector=name, as_of=now, regime=view.regime, summary=view.summary,
                        layers=layers, company_calls=calls, baskets=baskets,
                        rotation_advice=view.rotation_advice, top_risks=view.top_risks)
