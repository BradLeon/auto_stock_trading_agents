"""Sector review orchestration: assemble context -> one Opus synthesis -> persist.

LLM failure never overwrites the stored latest review — it returns the prior one
(or a stub) and saves nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.sector import (
    STANCES,
    CompanyCall,
    LayerAssessment,
    SectorConfig,
    SectorReview,
    TopDownComparison,
    allocation_for_status,
)
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
    from ..layer import layer_review
    from . import assemble as sector_assemble, cross_section, rotation

    prior = store.latest_sector_review(name)
    bind = _bind_layer_budget()
    verdicts, raw_baskets, failed = [], [], []
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
                                       use_llm=use_llm, assessments=assessments,
                                       store=store)
        if not ok:
            failed.append(layer.key)
        else:
            verdicts.append(verdict)
            pending_reports.append((layer, verdict, assessments, rows))

        if basket is not None:
            raw_baskets.append((layer, basket))

    # Phase D（agent/sector-allocation）：三级配置的唯一依据是 LayerAnalysis 投影。
    # 行业评审经 task_projection_envelopes 读取各层最新可用投影（这是守卫显式允许的
    # 唯一跨角色读取）；缺投影 / 过期 / schema 不兼容的层按缺失保守处理并显式留痕。
    allocations, projection_refs, projection_missing = (
        _sector_allocations_from_projections(store, cfg, bind))

    # Group ceilings can only be applied once EVERY member's ask is known — two 超配
    # halves of a split layer add up past the pre-split envelope, and neither half can
    # see the other. Ranks and the within-layer split are untouched; only totals move,
    # and only downward.
    budgets = cross_section.budgets_for(cfg, allocations)
    baskets = [_rescaled(b, budgets.get(ly.key, b.layer_cap)) for ly, b in raw_baskets]

    # Only now—after all layer verdicts and budgets are fixed—may top-down
    # context enter. It is loaded once and supplied only to the final comparison.
    top_down = _load_top_down_context(store)
    verdict_fingerprint = [item.model_dump_json() for item in verdicts]
    view = (rotation.run(
        cfg, verdicts, use_llm=use_llm,
        macro_context=top_down["macro_text"],
        factset_material=top_down["factset"])
        if verdicts else None)
    if verdict_fingerprint != [item.model_dump_json() for item in verdicts]:
        raise RuntimeError("final top-down synthesis mutated a layer verdict")
    review = _assemble_review(
        name, cfg, verdicts, baskets, view, failed, top_down=top_down,
        missing_projections=projection_missing)

    # 3.9：证据冲突必须分列保留——层级状态与标的观点相反时逐条标注待人工裁决，
    # 不通过加权或总分消解。
    conflicts = _evidence_conflicts(cfg, verdicts)
    # 3.6：轮动发现的相邻层矛盾原样带入评审输出，统一加「待人工裁决」标记；
    # 层级判断本身不被轮动改写（上方指纹校验保证）。
    for c in (view.conflicts if view else []):
        text = c.strip()
        conflicts.append(text if "待人工裁决" in text else f"[待人工裁决] {text}")
    if conflicts:
        review.summary = f"{review.summary}\n" + "\n".join(conflicts) if review.summary \
            else "\n".join(conflicts)

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

    # 3.7：配置结论以 SectorAllocation 投影发布，input_refs 记录本轮消费的全部
    # 层级投影标识，使配置可追溯到具体的层级判断。
    _publish_allocation_projection(store, name, review, budgets, projection_refs)

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


def _sector_allocations_from_projections(store, cfg, bind: bool):
    """行业评审的三级配置依据：各层最新可用的 LayerAnalysis 投影（3.2/3.5/3.10）。

    Returns `(allocations, projection_refs, missing)`：

    * `allocations` — {layer key: 配置结论 | None}。None 只在预算绑定关闭时出现
      （语义是「不缩预算」，不是「标配」）。
    * `projection_refs` — {layer key: projection_id}，本轮真实消费的层级投影。
    * `missing` — [(layer key, reason)]。评审失败、投影缺失、过期与 schema 不兼容
      一律按缺失处理：退回「标配」的保守使用率并显式留痕，SHALL NOT 标注为景气中性，
      也 SHALL NOT 字段兜底或猜测映射。
    """
    from ...agent.task_projection import ProjectionScope
    from ...schemas.sector import LAYER_STATUSES

    allocations: dict[str, str | None] = {}
    refs: dict[str, str] = {}
    missing: list[tuple[str, str]] = []
    read = getattr(store, "reusable_task_projection", None)
    for layer in cfg.layers:
        env = None
        if read is not None:
            try:
                # 不带 schema_version 过滤地取最新可用投影：版本不匹配的投影要
                # 显式判为 schema_incompatible（3.10），而不是被读取端静默筛掉、
                # 与「投影缺失」混为一谈。
                env = read(agent_role="layer_analysis",
                           scope=ProjectionScope(kind="layer", id=layer.key),
                           schema_name="LayerAnalysis")
            except Exception as exc:  # noqa: BLE001 - 读投影失败按缺失处理
                log.warning("layer projection read failed for %s: %s", layer.key, exc)
        if env is None:
            allocations[layer.key] = "标配" if bind else None
            missing.append((layer.key, "missing_or_unusable"))
            continue
        if env.schema_version != "v1":
            # schema/payload 不兼容：与缺失同等保守处理，但原因分列留痕，禁止兜底解析
            allocations[layer.key] = "标配" if bind else None
            missing.append((layer.key, "schema_incompatible"))
            continue
        status = (env.payload or {}).get("status")
        if status not in LAYER_STATUSES:
            # schema/payload 不兼容：与缺失同等处理，禁止兜底解析（3.10）
            allocations[layer.key] = "标配" if bind else None
            missing.append((layer.key, "schema_incompatible"))
            continue
        allocations[layer.key] = allocation_for_status(status)
        refs[layer.key] = env.projection_id
    return allocations, refs, missing


def _evidence_conflicts(cfg, verdicts) -> list[str]:
    """层级状态与标的层面证据相反的冲突清单（3.9）。两侧依据分列，不合并为单一分数。"""
    out: list[str] = []
    labels = {ly.key: ly.label for ly in cfg.layers}
    for v in verdicts:
        if v.layer_status == "contracting":
            for c in v.name_calls:
                if c.stance == "增持":
                    out.append(f"⚠️ [待人工裁决] {labels.get(v.layer_key, v.layer_key)}："
                               f"层级状态=收缩，但标的 {c.symbol} 观点=增持"
                               f"（两侧依据分列保留，未合并）")
        elif v.layer_status == "expanding":
            for c in v.name_calls:
                if c.stance == "减持":
                    out.append(f"⚠️ [待人工裁决] {labels.get(v.layer_key, v.layer_key)}："
                               f"层级状态=扩张，但标的 {c.symbol} 观点=减持"
                               f"（两侧依据分列保留，未合并）")
    return out


def _publish_allocation_projection(store, name, review, budgets, projection_refs) -> str | None:
    """把本轮配置结论发布为 `sector_allocation` 投影（3.7）。Best-effort。"""
    from ...agent.task_projection import ProjectionScope, build_envelope

    save = getattr(store, "save_task_projection_envelope", None)
    if save is None:
        return None
    drivers = [f"{v.layer_key}: {v.layer_status}" for v in review.layer_verdicts]
    overweight = sum(1 for v in review.layer_verdicts if v.layer_status == "expanding")
    underweight = sum(1 for v in review.layer_verdicts if v.layer_status == "contracting")
    stance = ("overweight" if overweight > underweight
              else "underweight" if underweight > overweight else "neutral")
    target_weight = max(0.0, min(1.0, sum(budgets.values())))
    rationale = (review.summary or review.regime or "").strip().splitlines()[0][:300] \
        if (review.summary or review.regime) else "sector allocation from layer projections"
    try:
        envelope = build_envelope(
            role="sector_allocation",
            payload={"sector": name, "stance": stance, "target_weight": target_weight,
                     "rationale": rationale, "drivers": drivers},
            scope=ProjectionScope(kind="sector", id=name),
            as_of=review.as_of.isoformat(timespec="seconds"),
            input_refs=sorted(projection_refs.values()))
        return save(envelope)
    except Exception as exc:  # noqa: BLE001 - 发布失败留痕，不影响评审落库
        log.warning("sector %s: allocation projection publish failed: %s", name, exc)
        return None


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


def _load_top_down_context(store) -> dict:
    """Final-comparison material for the cross-layer pass.

    Phase D（agent/sector-allocation）：行业分析师不再读取宏观评审——轮动上下文
    与配置结论都不得含宏观 regime；宏观视角只能经主理人的汇总进入提案。本函数
    现在只装载 FactSet 行业数据（共享数据产品），并显式记录宏观不可用。
    """
    from ...data.products import sector_inputs

    macro_note = "没有可用的最新正式宏观报告。"
    try:
        factset_material = sector_inputs.factset_sector_material()
    except Exception as exc:  # noqa: BLE001 - final comparison must degrade explicitly
        factset_material = {
            "text": "", "state": "unavailable", "mode": "unknown",
            "reason": f"读取 FactSet 十一行业背景失败：{exc}",
            "report_date": "", "version_id": "", "freshness": "unavailable"}
    return {
        "macro_text": "", "macro_date": "", "macro_note": macro_note,
        "factset": factset_material,
    }


def _assemble_review(name, cfg, verdicts, baskets, view, failed, *,
                     top_down=None, missing_projections=None) -> SectorReview:
    labels = {ly.key: ly.label for ly in cfg.layers}
    calls = [CompanyCall(symbol=c.symbol, layer=v.layer_key, stance=c.stance,
                         conviction=v.confidence, rationale=c.rationale)
             for v in verdicts for c in v.name_calls]
    regime = view.regime if view else "(轮动合成不可用)"
    summary = view.summary if view else ""
    if failed:
        note = "本轮未产出结论的层：" + "、".join(labels.get(k, k) for k in failed)
        summary = f"{summary}\n⚠️ {note}".strip()
    for key, reason in (missing_projections or []):
        if key in failed and reason == "missing_or_unusable":
            continue  # 失败层已列过，避免同一层两条重复标注
        why = ("层级投影缺失或不可用" if reason == "missing_or_unusable"
               else "层级投影 schema 不兼容（禁止字段兜底）")
        summary = (f"{summary}\n⚠️ {labels.get(key, key)}：{why}，"
                   "配置按标配保守处理（这是缺口留痕，不是景气中性）").strip()
    top_down = top_down or {"macro_text": "", "macro_date": "", "macro_note": "",
                            "factset": {}}
    factset = top_down.get("factset") or {}
    availability_notes = [
        note for note in (top_down.get("macro_note", ""), factset.get("reason", ""))
        if note]
    comparison = TopDownComparison(
        macro_background=(view.macro_background if view and top_down.get("macro_text")
                          else top_down.get("macro_note", "")),
        factset_background=(view.factset_background if view and factset.get("text")
                            else factset.get("reason", "")),
        agreements=list(view.agreements) if view else [],
        divergences=list(view.divergences) if view else [],
        recommendation_impact=(view.recommendation_impact if view else ""),
        availability_notes=availability_notes,
        macro_review_date=top_down.get("macro_date", ""),
        factset_report_date=str(factset.get("report_date") or ""),
        factset_version_id=str(factset.get("version_id") or ""),
    )
    return SectorReview(
        sector=name, as_of=_now(), regime=regime, summary=summary,
        layers=[], company_calls=calls, baskets=baskets, layer_verdicts=verdicts,
        rotation_advice=(view.rotation_advice if view else ""),
        top_risks=(list(view.top_risks) if view else []),
        top_down_comparison=comparison)


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
