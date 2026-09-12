"""Governed L1 Evidence Observer consumer for Claude work-adoption data.

This module deliberately consumes only DataProducts. It does not expose repository
handles, provider URLs, or physical-table queries to the Observer.
"""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise
import json
from typing import Any

CONSUMER = "evidence_observer"
PRODUCTION_CLAIM_ID = "ai_core_production_workflow_penetration"
PRODUCTION_CLAIM_DEFINITION_VERSION = "v2"
PRODUCTION_CLAIM_TEXT = "AI 的企业采用广度、员工持续使用和任务生产化深度是否同步扩大，从局部试验走向可重复的生产工作流？"
RAMP_CLAIM_ID = "ai_paid_business_adoption_diffusion"
RAMP_CLAIM_TEXT = "AI 是否从自报使用和试验，转向真实的企业付费采购，并在行业、企业规模和模型供应商之间扩散？"
SEMANTIC_GUARDRAILS = {
    "usage_share": "职业或任务占对应 Claude 产品总使用量的份额，不是从业者采用率。",
    "industry": "官网 Industry 是 SOC occupational major group，不是企业所属行业。",
    "observed_exposure": "Observed Exposure 是研究快照，不是月度使用序列。",
    "allowed_claims": (
        "Claude 使用量在职业间的分布",
        "同口径月份间自动化占比的变化",
        "公开 task-cell 的可见覆盖",
    ),
    "prohibited_claims": (
        "员工采用率",
        "企业席位渗透率",
        "岗位替代数量",
        "交易信号或证据权重",
    ),
}


def _usage_fact(job: dict[str, Any], *, source_product: str, period: str) -> dict[str, Any] | None:
    value = (job.get("metrics") or {}).get("ai.work_adoption.usage_share")
    if value is None:
        return None
    name = job.get("name") or job.get("entity_id", "")
    return {
        "kind": "claude_usage_share",
        "entity_id": job.get("entity_id", ""),
        "period": period,
        "source_product": source_product,
        "value": value,
        "unit": "percent",
        "statement": f"{period}，{name} 占 {source_product} 总使用量的 {value:g}%（不是该职业从业者采用率）。",
        "lineage": job.get("lineage", {}),
    }


def observe_work_adoption(
    *,
    source_product: str,
    period: str = "",
    occupation: str = "",
    as_of: datetime | None = None,
    top_n: int = 10,
    products=None,
) -> dict[str, Any]:
    """Return a bounded, replayable L1 observation packet.

    The generated statements use closed deterministic templates so provider metrics
    cannot be relabelled as worker adoption or employer-industry statistics.
    """
    if top_n < 0:
        raise ValueError("top_n must be non-negative")
    if products is None:
        from ...data.products import get_platform_data_products

        products = get_platform_data_products()

    snapshot = products.ai_work_adoption_snapshot(
        source_product=source_product,
        period=period,
        as_of=as_of,
        snapshot_consumer=CONSUMER,
        snapshot_purpose=f"l1_work_adoption:{source_product}:{period or 'latest'}",
    )
    selected_period = snapshot.get("period", period)
    facts = [
        fact
        for job in snapshot.get("jobs", [])[:top_n]
        if (fact := _usage_fact(job, source_product=source_product, period=selected_period))
        is not None
    ]
    profile = None
    if occupation and snapshot.get("status") == "ok":
        profile = products.ai_job_profile(
            occupation, source_product=source_product, period=selected_period, as_of=as_of
        )
    return {
        "status": snapshot.get("status", "no_coverage"),
        "consumer": CONSUMER,
        "source_access": "data_products_only",
        "source_product": source_product,
        "period": selected_period,
        "as_of": as_of.isoformat() if as_of else None,
        "facts": facts,
        "snapshot": snapshot,
        "job_profile": profile,
        "manifest": snapshot.get("manifest"),
        "semantic_guardrails": SEMANTIC_GUARDRAILS,
    }


def _is_adjacent(previous: str, current: str) -> bool:
    try:
        year, month = map(int, current.split("-"))
        expected = f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"
        return previous == expected
    except ValueError:
        return False


def _trend_status(rows: list[dict[str, Any]], field: str) -> str:
    ordered = sorted(rows, key=lambda row: row["period"])
    if len(ordered) < 3 or any(
        not _is_adjacent(left["period"], right["period"]) for left, right in pairwise(ordered)
    ):
        return "insufficient_history"
    values = [row.get(field) for row in ordered]
    if any(value is None for value in values):
        return "insufficient_history"
    changes = [right - left for left, right in pairwise(values)]
    if all(change == 0 for change in changes):
        return "flat"
    if all(change >= 0 for change in changes):
        return "directional_up"
    if all(change <= 0 for change in changes):
        return "directional_down"
    return "mixed"


def _combined_status(statuses: list[str], *, positive: str, mixed: str) -> str:
    if any(status == "insufficient_history" for status in statuses):
        return "insufficient_history"
    return positive if all(status == "directional_up" for status in statuses) else mixed


def _production_methodology_card(
    result: dict[str, Any], *, history_status: str, quality_status: str
) -> dict[str, Any]:
    coverage = next(
        (
            row
            for row in result.get("coverage_diagnostics", [])
            if row.get("period") == result.get("latest_period")
        ),
        {},
    )
    summary = {
        row["grain"]: row
        for row in result.get("period_rows", [])
        if row.get("period") == result.get("latest_period")
    }
    releases = sorted(
        {row.get("release_date") for row in summary.values() if row.get("release_date")}
    )
    published = sorted(
        {row.get("published_at") for row in summary.values() if row.get("published_at")}
    )
    return {
        "title": "AI 应用层生产化渗透：方法卡",
        "provider": "Anthropic Economic Index",
        "source_product": "1p_api",
        "geography": "GLOBAL",
        "periods": result.get("periods", []),
        "latest_period": result.get("latest_period", ""),
        "release_and_published_at": {
            "release_date": releases[-1] if releases else None,
            "published_at": published[-1] if published else None,
        },
        "visible_samples": {
            grain: summary.get(grain, {}).get("visible_count") for grain in ("occupation", "task")
        },
        "qualified_samples": {
            grain: summary.get(grain, {}).get("qualified_count") for grain in ("occupation", "task")
        },
        "taxonomy": {
            "version": coverage.get("taxonomy_version"),
            "mapped_tasks": coverage.get("mapped_task_count"),
            "unmapped_tasks": coverage.get("unmapped_task_count"),
            "mapping_coverage_pct": coverage.get("mapping_coverage_pct"),
        },
        "threshold": result.get("proxy", {}),
        "threshold_version": result.get("threshold_version"),
        "methodology_versions": sorted(
            {row.get("methodology_version", "") for row in result.get("period_rows", [])}
        ),
        "derivation_version": result.get("derivation_version"),
        "history_status": history_status,
        "quality_status": quality_status,
        "manifest_id": (result.get("manifest") or {}).get("snapshot_id"),
        "limitations": [
            "Usage Share 是 Anthropic 1P API 产品流量份额，不是员工或企业采用率。",
            "qualified 是用户定义的生产化代理，不能确认持续运行的工作流。",
            "未发布或隐私过滤的 cell 不等于零；职业内任务覆盖是 taxonomy-dependent 下限。",
            "不能由此推断岗位替代、生产率、ROI 或交易结论。",
        ],
    }


def observe_ai_production_penetration(
    *,
    periods: list[str] | None = None,
    as_of: datetime | None = None,
    top_n: int = 10,
    chart_dir: str = "",
    workflow_scope: dict[str, str] | None = None,
    claim_definition_version: str = "v2",
    products=None,
) -> dict[str, Any]:
    """Dedicated, read-only L1 packet for the production-workflow proxy.

    This is intentionally separate from the historical Usage observer and from every
    other Observer contract; only this packet contains a methodology card.
    """
    if products is None:
        from ...data.products import get_platform_data_products

        products = get_platform_data_products()
    # v2 is the governed multi-source path.  The compatibility branch below remains
    # temporarily available to replay legacy v1 fixtures and snapshots.
    if claim_definition_version == "v2" and hasattr(products, "ai_adoption_evidence_bundle"):
        return _observe_ai_production_diffusion_v2(
            as_of=as_of, top_n=top_n, chart_dir=chart_dir,
            workflow_scope=workflow_scope, products=products)
    scope_suffix = ""
    if workflow_scope:
        scope_suffix = ":" + ":".join(
            str(workflow_scope.get(key, "")) for key in ("sector", "layer")
        )
    result = products.ai_production_penetration(
        periods=periods,
        as_of=as_of,
        top_n=top_n,
        snapshot_consumer=CONSUMER,
        snapshot_purpose=(f"{PRODUCTION_CLAIM_ID}{scope_suffix}:{','.join(periods or ['latest'])}"),
        as_frame=bool(chart_dir),
    )
    if result.get("status") != "ok":
        return {
            "status": result.get("status", "no_coverage"),
            "claim_id": PRODUCTION_CLAIM_ID,
            "claim_text": PRODUCTION_CLAIM_TEXT,
            "source_access": "data_products_only",
            "warnings": [result.get("reason", "受治理生产化数据不可用。")],
        }
    summary = result.get("period_rows", [])
    by_grain = {
        grain: [row for row in summary if row["grain"] == grain] for grain in ("occupation", "task")
    }
    series_statuses = {
        "occupation_visible_production_rate": _trend_status(
            by_grain["occupation"], "visible_production_rate_pct"
        ),
        "task_visible_production_rate": _trend_status(
            by_grain["task"], "visible_production_rate_pct"
        ),
        "occupation_production_traffic_share": _trend_status(
            by_grain["occupation"], "production_traffic_share_pct"
        ),
        "task_production_traffic_share": _trend_status(
            by_grain["task"], "production_traffic_share_pct"
        ),
    }
    breadth = _combined_status(
        [
            series_statuses["occupation_visible_production_rate"],
            series_statuses["task_visible_production_rate"],
        ],
        positive="breadth_expanding",
        mixed="mixed",
    )
    depth = _combined_status(
        [
            series_statuses["occupation_production_traffic_share"],
            series_statuses["task_production_traffic_share"],
        ],
        positive="depth_deepening",
        mixed="mixed",
    )
    overall = (
        "penetration_expanding"
        if breadth == "breadth_expanding" and depth == "depth_deepening"
        else ("insufficient_history" if "insufficient_history" in {breadth, depth} else "mixed")
    )
    latest = result.get("latest_period", "")
    coverage_rows = [
        row for row in result.get("occupation_task_coverage", []) if row.get("period") == latest
    ]
    high_coverage_occupations = sorted(
        (
            row
            for row in coverage_rows
            if (
                row.get("confirmed_production_task_coverage_pct")
                if row.get("confirmed_production_task_coverage_pct") is not None
                else row.get("coverage_lower_bound_pct", 0)
            )
            >= 50
        ),
        key=lambda row: (
            -(
                row.get("confirmed_production_task_coverage_pct")
                if row.get("confirmed_production_task_coverage_pct") is not None
                else row.get("coverage_lower_bound_pct", 0)
            ),
            row.get("occupation_id", ""),
        ),
    )
    comparison = {}
    latest_rows = {row["grain"]: row for row in summary if row["period"] == latest}
    prior = sorted({row["period"] for row in summary if row["period"] < latest})
    facts, warnings = [], list(result.get("cell_diagnostics", []))
    if prior and all(grain in latest_rows for grain in ("occupation", "task")):
        prior_rows = {row["grain"]: row for row in summary if row["period"] == prior[-1]}
        occupation_breadth = (
            latest_rows["occupation"]["visible_production_rate_pct"]
            - prior_rows["occupation"]["visible_production_rate_pct"]
        )
        task_breadth = (
            latest_rows["task"]["visible_production_rate_pct"]
            - prior_rows["task"]["visible_production_rate_pct"]
        )
        occupation_depth = (
            latest_rows["occupation"]["production_traffic_share_pct"]
            - prior_rows["occupation"]["production_traffic_share_pct"]
        )
        task_depth = (
            latest_rows["task"]["production_traffic_share_pct"]
            - prior_rows["task"]["production_traffic_share_pct"]
        )
        comparison = {
            "from_period": prior[-1],
            "to_period": latest,
            "occupation_breadth_change_pp": occupation_breadth,
            "task_breadth_change_pp": task_breadth,
            "occupation_depth_change_pp": occupation_depth,
            "task_depth_change_pp": task_depth,
            "breadth": "mixed" if occupation_breadth * task_breadth < 0 else "aligned",
            "depth": "increased" if occupation_depth > 0 and task_depth > 0 else "mixed",
        }
        facts.append(
            {
                "kind": "monthly_comparison",
                "period": latest,
                "statement": (
                    f"{prior[-1]} 至 {latest} 的月度比较中，生产化广度表现混合；"
                    f"职业和任务的生产化流量份额均提高。"
                ),
            }
        )
    history_status = (
        "sufficient"
        if all(status != "insufficient_history" for status in series_statuses.values())
        else "insufficient_history"
    )
    if history_status == "insufficient_history":
        warnings.append("只有不足三个连续可比月：可展示月度比较，但不能判断持续趋势。")
    unnamed_high_coverage = [
        row
        for row in high_coverage_occupations
        if row.get("occupation_name_status") == "stable_id_only"
    ]
    if unnamed_high_coverage:
        warnings.append(
            "当前 Anthropic taxonomy artifact 未为 "
            f"{len(unnamed_high_coverage)} 个高覆盖旧 O*NET-SOC 代码提供职业标题；报告保留稳定代码，"
            "不擅自补写外部名称。"
        )
    warnings.extend(
        [
            "Usage Share 是产品流量份额，不是员工或企业采用率。",
            "生产化资格是代理，不证明持续工作流、生产率、ROI 或岗位替代。",
        ]
    )
    quality_status = (
        "warning"
        if any(
            row.get("quality_status") == "warning" for row in result.get("coverage_diagnostics", [])
        )
        else "accepted"
    )
    visualizations = {"descriptors": []}
    if chart_dir:
        from .production_visualization import render_production_charts

        visualizations = render_production_charts(
            frames=result["frames"],
            output_dir=chart_dir,
            manifest=result.get("manifest"),
            metadata={
                "source_product": "1p_api",
                "threshold_version": result.get("threshold_version"),
                "derivation_version": result.get("derivation_version"),
            },
        )
        if visualizations.get("visualization_warning"):
            warnings.append(visualizations["visualization_warning"])
    packet = {
        "status": "ok",
        "consumer": CONSUMER,
        "source_access": "data_products_only",
        "claim_id": PRODUCTION_CLAIM_ID,
        "claim_text": PRODUCTION_CLAIM_TEXT,
        "source_scope": {
            "provider": "Anthropic Economic Index",
            "source_product": "1p_api",
            "geography": "GLOBAL",
        },
        "threshold_definition": result.get("proxy"),
        "period_range": result.get("periods", []),
        "latest_period": latest,
        "history_status": history_status,
        "four_series_statuses": series_statuses,
        "breadth_status": breadth,
        "depth_status": depth,
        "overall_status": overall,
        "monthly_comparison": comparison,
        "summary_table": summary,
        "top_occupations": result.get("top_occupations", []),
        "top_tasks": result.get("top_tasks", []),
        "coverage_diagnostics": result.get("coverage_diagnostics", []),
        "occupation_task_coverage": result.get("occupation_task_coverage", []),
        "high_coverage_occupations": high_coverage_occupations,
        "occupation_coverage_distribution": result.get("occupation_coverage_distribution", {}),
        "methodology_card": _production_methodology_card(
            result, history_status=history_status, quality_status=quality_status
        ),
        "facts": facts,
        "warnings": warnings,
        "snapshot_manifest": result.get("manifest"),
        "visualization_descriptors": visualizations.get("descriptors", []),
        "semantic_guardrails": {
            **SEMANTIC_GUARDRAILS,
            "production_proxy": result.get("semantic_boundary"),
        },
    }
    if workflow_scope:
        packet["workflow_scope"] = dict(workflow_scope)
        packet["methodology_card"]["workflow_scope"] = dict(workflow_scope)
    return packet


def _observe_ai_production_diffusion_v2(*, as_of, top_n: int, chart_dir: str,
                                        workflow_scope: dict[str, str] | None,
                                        products) -> dict[str, Any]:
    scope = workflow_scope or {}
    bundle = products.ai_adoption_evidence_bundle(
        as_of=as_of, snapshot_consumer=CONSUMER,
        snapshot_purpose=f"{PRODUCTION_CLAIM_ID}:v2:{scope.get('sector','')}:{scope.get('layer','')}")
    axes = bundle.get("axes", {})
    task_detail = axes.get("task_production", {}).get("detail", {})
    manifest = bundle.get("manifest")
    axis_rows = []
    facts, warnings = [], []
    for axis_id, axis in axes.items():
        headline = axis.get("headline") or {}
        value = headline.get("value")
        if value is None and axis_id == "task_production":
            value = headline.get("production_traffic_share_pct")
        row = {"axis_id": axis_id, "axis_label": axis.get("label"),
               "source_id": axis.get("source_id"), "period": axis.get("period"),
               "headline_value": value, "headline_unit": headline.get("unit", "percent"),
               "trend_status": axis.get("trend", {}).get("status"),
               "statistical_unit": axis.get("statistical_unit"),
               "denominator": axis.get("denominator"), "geography": axis.get("geography"),
               "input_observation_ids": axis.get("trend", {}).get("input_observation_ids", []),
               "trend_explanation": axis.get("trend", {}).get("steps", []),
               "trend_net_change_pp": axis.get("trend", {}).get("net_change_pp"),
               "trend_slope_pp_per_period": axis.get("trend", {}).get("linear_slope_pp_per_period"),
               "comparable_period_count": len(axis.get("trend", {}).get("periods", []))}
        axis_rows.append(row)
        facts.append({"kind": "axis_status", "axis_id": axis_id, "period": axis.get("period"),
                      "statement": f"{axis.get('label')}：{row['trend_status']}（{axis.get('period') or '无可用期间'}）。",
                      "input_observation_ids": row["input_observation_ids"]})
        if axis.get("source_status") == "unavailable":
            warnings.append(f"{axis.get('label')}当前不可用；其他轴未用旧值补齐该轴。")
    conflicts = []
    statuses = {row["axis_id"]: row["trend_status"] for row in axis_rows}
    directional = {key: value for key, value in statuses.items()
                   if value in {"expanding", "contracting"}}
    if len(set(directional.values())) > 1:
        conflicts.append({"type": "directional_conflict", "axes": directional,
                          "interpretation": "来源方向不一致；保留分歧，不合成统一指标。"})
    if bundle.get("periods_are_asynchronous"):
        warnings.append("三个来源期间不同步；报告保留各自最新期间，不前向填充或插值。")
    warnings.append("不同来源的统计主体和分母不同，只用于方向性相互印证，不合并为统一渗透率。")
    methodology_card = {
        "title": "L1 AI 应用层生产化与扩散：固定方法卡", "claim_definition_version": "v2",
        "sources": [{key: axis.get(key) for key in (
            "axis_id", "label", "source_id", "statistical_unit", "denominator", "geography",
            "technology_scope", "reference_period", "frequency", "methodology_regimes",
            "period", "published_at", "known_at", "age_days")} | {
                "lineage_summary": bundle.get("detail_lineage", {}).get(axis.get("axis_id"), {})
            } for axis in axes.values()],
        "anthropic_threshold": task_detail.get("proxy", {}),
        "quality_and_missingness": ["抑制、隐私过滤或未发布 cell 均按缺失处理，不按零处理。",
                                    "方法口径变化会中断趋势，不跨 regime 计算变化。"],
        "prohibited_inferences": ["不能推断统一全球采用率、员工替代数、生产率、ROI 或交易信号。"],
        "lineage": {"manifest_id": (manifest or {}).get("snapshot_id"),
                    "bundle_version": bundle.get("bundle_version")},
    }
    packet = {
        "status": bundle.get("status"), "consumer": CONSUMER, "source_access": "data_products_only",
        "claim_id": PRODUCTION_CLAIM_ID, "claim_definition_version": "v2",
        "claim_text": PRODUCTION_CLAIM_TEXT, "overall_status": bundle.get("overall", {}).get("status"),
        "overall_interpretation": bundle.get("overall", {}).get("interpretation"),
        "overall_reasoning": bundle.get("overall", {}).get("steps", []), "axis_overview": axis_rows,
        "axes": axes, "corroboration_and_conflicts": conflicts, "comparability": bundle.get("comparability"),
        "periods": {row["axis_id"]: row["period"] for row in axis_rows},
        "methodology_card": methodology_card, "facts": facts, "warnings": warnings,
        "summary_table": task_detail.get("period_rows", []),
        "occupation_task_coverage": task_detail.get("occupation_task_coverage", []),
        "occupation_coverage_distribution": task_detail.get("occupation_coverage_distribution", {}),
        "top_occupations": task_detail.get("top_occupations", [])[:top_n],
        "top_tasks": task_detail.get("top_tasks", [])[:top_n],
        "snapshot_manifest": manifest, "manifest": manifest,
        "bundle_content_hash": bundle.get("content_hash"), "visualization_descriptors": [],
        "detail_lineage": bundle.get("detail_lineage", {}),
    }
    # Ramp is a supplemental paid-business / spend signal.  It is deliberately
    # loaded after the three governed axes and never enters ``overall`` or any
    # of the trend-status calculations above.  A missing/failed slice is kept as
    # an explicit warning rather than treated as zero adoption.
    ramp_signal = _ramp_supplement(products, as_of=as_of)
    packet["supplemental_signals"] = {"ramp_paid_adoption": ramp_signal}
    packet["supplemental_claims"] = [ramp_signal.get("claim", {
        "claim_id": RAMP_CLAIM_ID, "claim_text": RAMP_CLAIM_TEXT,
        "role": "L1 第四个补充证据轴；不改变三轴主命题状态",
    })]
    packet["methodology_card"]["ramp"] = {
        "claim_id": RAMP_CLAIM_ID,
        "claim_text": RAMP_CLAIM_TEXT,
        "role": "第四个补充证据轴（付费企业采购），不进入三轴 overall_status",
        "title": "Ramp 付费企业采用与 AI 支出（L1 补充）",
        "statistical_unit": "Ramp 网络中相关付款企业 cohort",
        "adoption_definition": "当月通过 Ramp corporate card、invoice 或 ACH 等渠道，对 AI 产品/服务发生正向交易的企业占比。",
        "denominator": "Ramp 相关企业 cohort；不是全美企业、员工或席位总数。",
        "scopes": ["adoption_overall", "adoption_overall_models", "adoption_sector",
                   "spend_per_employee_overall", "model_market_share_overall"],
        "sector_definition": "Ramp 页面行业下钻使用 NAICS 分组；不将其与 Anthropic SOC 职业大类混同。",
        "model_share_cohort": "model_market_share_overall 仅覆盖 Token Spend Management 连接企业的模型归因 API spend。",
        "coverage_bias": ["免费工具/个人账户、非 Ramp 付款不会被观察，可能低估。",
                          "Ramp 客户偏向使用企业支付平台的成长型/技术型公司，存在选择偏差。"],
        "out_of_scope": ["business_size", "geographies"],
        "interpretation_boundary": "Ramp 只作为付费企业采用与支出补充证据，不改变 BTOS/RPS/Anthropic 三轴状态，也不跨源平均、相减或补值。",
        "lineage_pointer": "supplemental_signals.ramp_paid_adoption.slices.<scope>.lineage",
    }
    if ramp_signal.get("status") not in {"ok", "partial"}:
        packet["warnings"].append("ramp_unavailable: Ramp 补充信号未通过网页/API访问或质量门；不影响三轴判断。")
    elif ramp_signal.get("warnings"):
        packet["warnings"].extend(ramp_signal["warnings"])
    if scope:
        packet["workflow_scope"] = dict(scope)
    if chart_dir:
        from .adoption_visualization import render_ai_adoption_charts
        rendered = render_ai_adoption_charts(packet=packet, output_dir=chart_dir)
        packet["visualization_descriptors"] = rendered.get("descriptors", [])
        packet["table_descriptors"] = rendered.get("tables", [])
        if rendered.get("visualization_warning"):
            packet["warnings"].append(rendered["visualization_warning"])
            packet["visualization_warning"] = rendered["visualization_warning"]
        from .ramp_visualization import render_ramp_charts
        ramp_rendered = render_ramp_charts(packet=packet, output_dir=chart_dir)
        packet["visualization_descriptors"].extend(ramp_rendered.get("descriptors", []))
        packet["table_descriptors"] = packet.get("table_descriptors", []) + ramp_rendered.get("tables", [])
        if ramp_rendered.get("visualization_warning"):
            packet["warnings"].append(ramp_rendered["visualization_warning"])
    packet["context"] = _build_v2_context(packet)
    return packet


def _ramp_supplement(products, *, as_of) -> dict[str, Any]:
    """Load Ramp slices through DataProducts, preserving each native scope.

    This helper is intentionally defensive: older fixtures and deployments may
    not yet contain Ramp rows or methods.  The L1 packet remains valid and the
    caller receives an explicit access/no-coverage status.
    """
    methods = (
        "ramp_paid_adoption_snapshot", "ramp_spend_per_employee_series",
        "ramp_model_market_share_series",
    )
    if not all(hasattr(products, name) for name in methods):
        return {"status": "unavailable", "reason": "ramp_data_product_not_registered", "slices": {}}
    slices: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for scope in ("adoption_overall", "adoption_overall_models", "adoption_sector"):
        try:
            slices[scope] = products.ramp_paid_adoption_snapshot(scope=scope, as_of=as_of)
        except Exception as exc:  # noqa: BLE001 - one scope must not block others
            slices[scope] = {"status": "unavailable", "scope": scope,
                             "reason": f"{type(exc).__name__}: {exc}", "rows": []}
            warnings.append(f"Ramp {scope} unavailable: {type(exc).__name__}")
    try:
        slices["spend_per_employee_overall"] = products.ramp_spend_per_employee_series(
            scope="spend_per_employee_overall", as_of=as_of)
    except Exception as exc:  # noqa: BLE001
        slices["spend_per_employee_overall"] = {"status": "unavailable",
                                                 "scope": "spend_per_employee_overall",
                                                 "reason": f"{type(exc).__name__}: {exc}", "rows": []}
        warnings.append(f"Ramp spend_per_employee_overall unavailable: {type(exc).__name__}")
    try:
        slices["model_market_share_overall"] = products.ramp_model_market_share_series(
            scope="model_market_share_overall", as_of=as_of)
    except Exception as exc:  # noqa: BLE001
        slices["model_market_share_overall"] = {"status": "unavailable",
                                                 "scope": "model_market_share_overall",
                                                 "reason": f"{type(exc).__name__}: {exc}", "rows": []}
        warnings.append(f"Ramp model_market_share_overall unavailable: {type(exc).__name__}")
    available = [item for item in slices.values() if item.get("status") == "ok"]
    status = "ok" if len(available) == len(slices) else ("partial" if available else "unavailable")
    return {
        "status": status, "provider": "Ramp AI Index", "source_id": "ramp_ai_index",
        "claim": {"claim_id": RAMP_CLAIM_ID, "claim_text": RAMP_CLAIM_TEXT,
                   "role": "L1 第四个补充证据轴；不改变三轴主命题状态"},
        "slices": slices, "scope_count": len(slices), "available_scope_count": len(available),
        "warnings": warnings,
        "limitations": [
            "Ramp 数值代表 Ramp 网络中有相关付款的企业 cohort，不是全美企业或员工采用率。",
            "免费工具、个人账户、非 Ramp 付款和客户选择偏差可能造成低估或偏差。",
            "企业规模与地理图表未纳入首版；不同 slice 不共享分母。",
        ],
    }


def _build_v2_context(packet: dict[str, Any]) -> dict[str, Any]:
    """Deterministic bounded contexts; essential evidence is never tail-truncated."""
    essential = {key: packet.get(key) for key in (
        "claim_id", "claim_definition_version", "claim_text", "overall_status",
        "overall_interpretation", "axis_overview", "supplemental_claims",
        "corroboration_and_conflicts", "warnings")}
    essential["manifest_id"] = (packet.get("manifest") or {}).get("snapshot_id")
    ramp = packet.get("supplemental_signals", {}).get("ramp_paid_adoption", {})
    essential["ramp_paid_adoption"] = _ramp_context(ramp, compact=True)
    compact = json.dumps(essential, ensure_ascii=False, sort_keys=True, default=str)
    review_model = {**essential, "methodology_card": packet.get("methodology_card"),
                    "summary_table": packet.get("summary_table"),
                    "occupation_coverage_distribution": packet.get("occupation_coverage_distribution"),
                    "top_occupations": packet.get("top_occupations"), "top_tasks": packet.get("top_tasks")}
    review_model["ramp_paid_adoption"] = _ramp_context(ramp, compact=False)
    review = json.dumps(review_model, ensure_ascii=False, sort_keys=True, default=str)
    return {"schema_version": "ai_adoption_observer_context/v2", "compact": compact,
            "compact_char_count": len(compact), "compact_budget": 12000,
            "review": review, "review_char_count": len(review), "review_budget": 120000,
            "budget_status": "ok" if len(compact) <= 12000 and len(review) <= 120000 else "exceeded"}


def _ramp_context(signal: dict[str, Any], *, compact: bool) -> dict[str, Any]:
    """Bound Ramp context to headline rows; raw transaction payloads stay in lineage."""
    out = {key: signal.get(key) for key in ("status", "provider", "source_id", "scope_count",
                                             "available_scope_count", "warnings", "limitations")}
    out["slices"] = {}
    for scope, item in (signal.get("slices") or {}).items():
        rows = item.get("rows") or []
        # Review context receives a few latest rows, compact context only one
        # headline per scope.  Both retain quality, denominator and lineage IDs.
        rows = sorted(rows, key=lambda row: (str(row.get("period", "")), str(row.get("entity_id", ""))))
        selected = rows[-(1 if compact else 6):]
        safe_rows = []
        for row in selected:
            safe_rows.append({key: row.get(key) for key in (
                "observation_id", "artifact_id", "period", "entity_id", "entity_name", "metric_id",
                "value", "unit", "segment", "quantile", "provider", "model", "spend_type",
                "provider_monthly_change_pp", "provider_yearly_change_pp", "statistical_unit",
                "denominator_scope", "technology_scope", "quality_status")})
        out["slices"][scope] = {"status": item.get("status"), "period": item.get("period"),
                                 "periods": item.get("periods", []), "rows": safe_rows,
                                 "quality": item.get("quality"), "freshness": item.get("freshness"),
                                 "lineage": item.get("lineage"), "manifest": item.get("manifest"),
                                 "reason": item.get("reason"), "limitations": item.get("limitations")}
    return out


def _ramp_level_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only source-native level rows for a Ramp slice."""
    rows = item.get("rows") or []
    return [row for row in rows if row.get("value") is not None and (
        str(row.get("metric_id", "")).endswith("adoption_share")
        or str(row.get("metric_id", "")).endswith("spend_per_employee")
        or str(row.get("metric_id", "")).endswith("api_spend_share")
        or not row.get("metric_id")
    )]


def _ramp_series_summary(signal: dict[str, Any], scope: str) -> dict[str, Any]:
    """Summarise one Ramp level series without cross-source arithmetic."""
    item = (signal.get("slices") or {}).get(scope, {})
    rows = _ramp_level_rows(item)
    periods = sorted({str(row.get("period", ""))[:7] for row in rows if row.get("period")})
    latest = periods[-1] if periods else ""
    prior = periods[-2] if len(periods) >= 2 else ""
    latest_rows = [row for row in rows if str(row.get("period", ""))[:7] == latest]
    prior_rows = [row for row in rows if str(row.get("period", ""))[:7] == prior]
    latest_value = latest_rows[0].get("value") if scope == "adoption_overall" and latest_rows else None
    prior_value = prior_rows[0].get("value") if scope == "adoption_overall" and prior_rows else None
    change = (latest_value - prior_value) if latest_value is not None and prior_value is not None else None
    return {"scope": scope, "status": item.get("status", "unavailable"),
            "periods": periods, "latest_period": latest, "prior_period": prior,
            "period_count": len(periods),
            "latest_value": latest_value, "prior_value": prior_value,
            "change_pp": change, "row_count": len(rows),
            "history_status": "sufficient" if len(periods) >= 3 else "insufficient_history"}


def _pct(value: Any, digits: int = 2) -> str:
    """Format nullable percentage values without changing the governed value."""
    return "—" if value is None else f"{float(value):.{digits}f}%"


def _pp(value: Any, digits: int = 2) -> str:
    """Format a percentage-point change; this is not itself a percent sign."""
    return "—" if value is None else f"{float(value):.{digits}f}"


def _signed_pp(value: Any, digits: int = 2) -> str:
    """Format a nullable percentage-point change with an explicit direction."""
    return "—" if value is None else f"{float(value):+.{digits}f}pp"


def _occupation_label(row: dict[str, Any]) -> str:
    name = str(row.get("occupation_name") or row.get("occupation_id", ""))
    if row.get("occupation_name_status") == "stable_id_only":
        return f"{name}（当前 taxonomy 未提供名称）"
    return name


def render_ai_production_markdown(packet: dict[str, Any]) -> str:
    """Render the dedicated L1 review packet without querying or deriving data.

    The reader-facing order deliberately separates the four core series from the
    taxonomy-dependent occupational task-combination evidence.
    """
    if packet.get("claim_definition_version") == "v2":
        return _render_ai_production_diffusion_v2(packet)
    if packet.get("status") != "ok":
        reason = "; ".join(str(item) for item in packet.get("warnings", []))
        return f"# AI 应用层：生产化与应用扩散\n\n状态：{packet.get('status', 'unavailable')}\n\n{reason}\n"
    card = packet.get("methodology_card", {})
    scope = packet.get("workflow_scope", {})
    grain_name = {"occupation": "职业", "task": "任务"}
    lines = ["# AI 应用层：生产化与应用扩散", ""]
    if scope:
        lines.extend(
            [
                (
                    f"- 运行范围：{scope.get('sector_label', scope.get('sector', ''))} / "
                    f"{scope.get('layer_label', scope.get('layer', ''))}"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## 方法卡",
            "",
            f"- 命题：{packet.get('claim_text', '')}",
            f"- 数据范围：{card.get('provider', '')} / {card.get('source_product', '')} / GLOBAL",
            f"- 数据期间：{'、'.join(card.get('periods', []))}；最新月：{card.get('latest_period', '')}",
            f"- 历史状态：{packet.get('history_status', '')}（不足三个连续可比月时，只报告月度比较）",
            "- 生产化代理：Usage Share > 0、Work Use Share ≥ 80%、Automation Share ≥ 80%、Directive Share ≥ 50%。",
            f"- 可见/达标单元（最新月）：职业 {card.get('visible_samples', {}).get('occupation', '—')} / {card.get('qualified_samples', {}).get('occupation', '—')}；任务 {card.get('visible_samples', {}).get('task', '—')} / {card.get('qualified_samples', {}).get('task', '—')}。",
            "",
            "## 命题状态与四项核心指标",
            "",
            f"- 总体状态：`{packet.get('overall_status', '')}`；广度：`{packet.get('breadth_status', '')}`；深度：`{packet.get('depth_status', '')}`。",
            "- 当前仅有两个连续月，不能据此判断持续趋势。",
            "",
            "|期间|统计维度|可见单元生产化率|生产化流量份额|",
            "|---|---|---:|---:|",
        ]
    )
    for row in packet.get("summary_table", []):
        lines.append(
            f"|{row.get('period', '')}|{grain_name.get(row.get('grain'), row.get('grain', ''))}|"
            f"{_pct(row.get('visible_production_rate_pct'))}|"
            f"{_pct(row.get('production_traffic_share_pct'))}|"
        )
    comparison = packet.get("monthly_comparison", {})
    if comparison:
        lines.extend(
            [
                "",
                (
                    f"月度比较（{comparison.get('from_period')} → {comparison.get('to_period')}）："
                    f"职业可见单元生产化率 {_pp(comparison.get('occupation_breadth_change_pp'))} 个百分点，"
                    f"任务可见单元生产化率 {_pp(comparison.get('task_breadth_change_pp'))} 个百分点；"
                    f"职业生产化流量份额 {_pp(comparison.get('occupation_depth_change_pp'))} 个百分点，"
                    f"任务生产化流量份额 {_pp(comparison.get('task_depth_change_pp'))} 个百分点。"
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## 职业任务组合的已确认生产化覆盖分布",
            "",
            "在一个职业的 O*NET 任务组合中，分子是当月公开 1P API cell 已满足生产化代理的任务数；分母是该职业全部去重 O*NET 任务数。未公开、隐私过滤或不能映射的任务仍留在分母，因此这里只陈述公开数据能够确认的覆盖，不声称存在可观测的“上限”。这属于 taxonomy-dependent 补充证据，不改变上面的四项核心指标或命题状态。",
            "",
            "|至少达到的已确认覆盖|职业数|有任务组合职业中的占比|",
            "|---:|---:|---:|",
        ]
    )
    for row in packet.get("occupation_coverage_distribution", {}).get("landmarks", []):
        lines.append(
            f"|{_pct(row.get('minimum_coverage_pct'), 0)}|{row.get('occupation_count', '—')}|"
            f"{_pct(row.get('occupation_share_pct'))}|"
        )
    lines.extend(
        [
            "",
            "### 分布尾部的完整披露：已确认覆盖至少 50% 的职业（最新月）",
            "",
            "这不是典型职业样本，也不表示该职业有一半员工、工作时间或完整工作流已经 AI 生产化。它只是上表分布右尾的完整列示：在该职业的 O*NET 任务清单中，公开 1P API 数据能够同时确认满足本代理的任务达到一半以上。典型使用单位请看随后按 Usage Share 排列的高流量 TOP10。",
            "",
            "|职业|已确认生产化任务|O*NET 任务总数|已确认生产化覆盖|",
            "|---|---:|---:|---:|",
        ]
    )
    high_coverage = packet.get("high_coverage_occupations", [])
    if high_coverage:
        for row in high_coverage:
            coverage = row.get(
                "confirmed_production_task_coverage_pct", row.get("coverage_lower_bound_pct")
            )
            lines.append(
                f"|{_occupation_label(row)}|"
                f"{row.get('qualified_mapped_task_count', '—')}|{row.get('taxonomy_task_count', '—')}|{_pct(coverage)}|"
            )
    else:
        lines.append("|无|—|—|—|")
    for title, rows in (
        ("高流量生产化职业 TOP10（典型使用单元）", packet.get("top_occupations", [])),
        ("高流量生产化任务 TOP10（典型使用单元）", packet.get("top_tasks", [])),
    ):
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "以下仅在满足生产化代理的公开 cell 中，按最新月 1P API Usage Share 降序排列；它们是高流量使用的例子，并不是职业任务覆盖分布的代表性抽样。",
                "",
                "|排名|对象|Usage Share|Work Use Share|Automation Share|Directive Share|",
                "|---:|---|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                f"|{row.get('rank', '')}|{row.get('entity_name', row.get('entity_id', ''))}|"
                f"{_pct(row.get('usage_share_pct'))}|{_pct(row.get('work_use_share_pct'), 1)}|"
                f"{_pct(row.get('automation_share_pct'), 1)}|{_pct(row.get('directive_share_pct'), 1)}|"
            )
    descriptors = packet.get("visualization_descriptors", [])
    if descriptors:
        lines.extend(["", "## 图表与可复算 sidecar", ""])
        lines.extend(
            f"- {item.get('chart_type')}: {item.get('png_path')}（sidecar: {item.get('sidecar_path')}）"
            for item in descriptors
        )
    lines.extend(
        [
            "",
            "## 指标注释与公式",
            "",
            "1. **职业可见单元生产化率** = `100 × 当月达到生产化代理的公开详细职业 cell 数 / 当月具有公开 Usage Share 的详细职业 cell 数`。它测量公开职业单元中符合代理标准的比例，不是职业从业者采用率。",
            "2. **任务可见单元生产化率** = `100 × 当月达到生产化代理的公开 O*NET task cell 数 / 当月具有公开 Usage Share 的 O*NET task cell 数`。它测量公开任务单元中符合代理标准的比例，不是所有经济任务的自动化率。",
            "3. **职业生产化流量份额** = `Σ(达到生产化代理的详细职业 cell 的 Provider Usage Share)`。它表示这些职业分类下的合格公开 cell 承载了 1P API 总产品流量的多少份额。",
            "4. **任务生产化流量份额** = `Σ(达到生产化代理的 O*NET task cell 的 Provider Usage Share)`。它表示这些任务分类下的合格公开 cell 承载了 1P API 总产品流量的多少份额。",
            "5. 职业与任务是同一 1P API 产品流量的两种分类视角；四个值均不得跨粒度相加、平均或解释成员工/企业采用率。",
            "",
            "## 语义限制",
            "",
        ]
    )
    lines.extend(
        f"- {warning}" for warning in packet.get("warnings", []) if isinstance(warning, str)
    )
    return "\n".join(lines) + "\n"


def _render_ai_production_diffusion_v2(packet: dict[str, Any]) -> str:
    """Render v2 in review order from the packet only; never query or recalculate."""
    if packet.get("status") != "ok":
        return f"# AI 应用层：生产化与应用扩散\n\n状态：{packet.get('status', 'unavailable')}\n"
    scope = packet.get("workflow_scope", {})
    descriptors = packet.get("visualization_descriptors", [])
    rendered_paths: set[str] = set()

    def chart_lines(*prefixes: str) -> list[str]:
        selected = [item for item in descriptors
                    if any(str(item.get("title", "")).startswith(prefix) for prefix in prefixes)
                    and item.get("png_path") not in rendered_paths]
        output: list[str] = []
        for item in selected:
            rendered_paths.add(item["png_path"])
            output += ["", f"![{item.get('title')}]({item.get('png_path')})", "",
                       f"数据与复现：[sidecar]({item.get('sidecar_path')})"]
        return output

    status_cn = {
        "expanding": "中期扩大", "contracting": "中期收缩", "stable": "基本稳定",
        "mixed": "方向混合", "insufficient_history": "历史不足", "unavailable": "不可用",
        "broadening_and_deepening": "广度和深度同步扩大",
        "breadth_without_confirmed_depth": "广度扩大、深度尚未确认",
        "provider_telemetry_only": "仅供应商遥测扩大", "mixed_evidence": "证据方向不一致",
    }
    overall = packet.get("overall_status")
    lines = ["# AI 应用层：生产化与应用扩散", "",
             f"- 命题版本：`{packet.get('claim_definition_version')}`",
             f"- 命题：{packet.get('claim_text')}",
             f"- 判断：{packet.get('overall_interpretation') or status_cn.get(overall, '历史尚不足以形成完整三轴判断')}（机器状态：`{overall}`；不计算跨来源综合分数）"]
    if scope:
        lines.append(f"- 运行范围：{scope.get('sector')} / {scope.get('layer')}")
    lines += ["", "## 三轴总览", "",
              "|观察轴|最新期间|标题值|趋势状态|统计主体|分母|", "|---|---|---:|---|---|---|"]
    for row in packet.get("axis_overview", []):
        value = "—" if row.get("headline_value") is None else f"{float(row['headline_value']):.2f}%"
        lines.append(f"|{row.get('axis_label')}|{row.get('period') or '—'}|{value}|"
                     f"{row.get('trend_status')}|{row.get('statistical_unit')}|{row.get('denominator')}|")
    lines += ["", "## 相互印证与冲突", ""]
    conflicts = packet.get("corroboration_and_conflicts", [])
    lines += ([f"- {item.get('interpretation')} {item.get('axes')}" for item in conflicts]
              if conflicts else ["- 当前没有可判定的方向冲突；历史不足的轴不视作支持证据。"])
    lines += ["", "### 本报告使用的变量", "",
              "|来源|变量|物理含义|在判断中的作用|", "|---|---|---|---|",
              "|BTOS|`current_use_share`|过去两周在任一业务职能使用 AI 的美国雇主企业占比|企业采用广度 headline|",
              "|RPS|`last_week_work_use_share`|过去一周至少一次为工作使用 GenAI 的美国就业人口占比|员工持续使用 headline|",
              "|RPS|`work_use_share` / `daily_work_use_share`|曾为工作使用、以及每个工作日使用 GenAI 的就业人口占比|持续性边界与辅助诊断|",
              "|RPS|`assisted_work_hours` / `time_saved_hours`|受访者估计的 AI 辅助工时及节省工时占总工时比例|使用强度诊断，不直接决定本轴状态|",
              "|Anthropic|任务生产化流量份额|满足 Work≥80%、Automation≥80%、Directive≥50% 的任务 Usage Share 之和|任务生产化 headline|",]
    axes = packet.get("axes", {})
    for key, title, chart_prefix in (("enterprise_breadth", "BTOS 美国企业 AI 采用广度", "BTOS "),
                       ("worker_persistence", "RPS 美国员工工作使用持续性", "RPS ")):
        axis = axes.get(key, {})
        trend = axis.get("trend", {})
        detail = axis.get("detail", {})
        if key == "enterprise_breadth":
            strata = detail.get("strata", {})
            counts = "、".join(
                f"{name} {len({row.get('entity_id') for row in rows if row.get('entity_id')})} 个统计单元/{len(rows)} 个发布 cells"
                for name, rows in strata.items()
            )
            source_line = "[美国人口普查局 BTOS](https://www.census.gov/hfp/btos/data)；双周发布。"
            definition = "过去两周在任一业务职能使用 AI 的美国雇主企业占比；按企业计数权重估计。"
            formula = "回答 Yes 的加权企业数 ÷ 该统计层全部在范围企业的加权企业数 × 100%。"
            scope_line = f"最新期按全国、行业、企业规模、行业×规模分层；参与 {counts or '0 cells'}；趋势含 {len(trend.get('periods', []))} 个全国期间。"
        else:
            histories = detail.get("history", {})
            counts = "、".join(f"{name} {len(rows)} 期" for name, rows in histories.items())
            source_line = "[FRED / Real-Time Population Survey](https://fred.stlouisfed.org/categories/33509)；季度发布。"
            definition = "过去一周至少一次为工作使用 GenAI 的美国 18–64 岁就业人口占比；来自受访者自报。"
            formula = "报告过去一周为工作使用 GenAI 的就业成年人 ÷ 目标就业人口 × 100%。"
            scope_line = f"全国就业人口；5 条工作用途序列，参与 {counts or '0 期'}；headline 趋势含 {len(trend.get('periods', []))} 期。"
        lines += ["", f"## {title}", "",
                  f"- 指标说明：{definition}", f"- 计算公式：{formula}",
                  f"- 数据发布来源：{source_line}",
                  f"- 统计范围与数量：{scope_line}",
                  f"- 最新期间与结论：{axis.get('period') or '无'}；{status_cn.get(trend.get('status'), trend.get('status'))}（`{trend.get('status')}`）。",
                  f"- 统计主体：{axis.get('statistical_unit')}；分母：{axis.get('denominator')}。"]
        if key == "enterprise_breadth" and trend.get("standard_errors"):
            latest_se = trend["standard_errors"][-1]
            lines.append(f"- 最新 standard error：{latest_se.get('standard_error')} 个百分点；当前只作描述性判断，未做显著性检验。")
        lines += chart_lines(chart_prefix)
    lines += ["", "## Anthropic：任务生产化分布与四项指标", "",
              "- 数据发布来源：[Anthropic Economic Index](https://huggingface.co/datasets/Anthropic/EconomicIndex)，本报告使用 GLOBAL 1P API 数据。",
              "- 统计维度：SOC 详细职业与 O*NET task；职业和任务是同一产品流量的两种分类视角，不能相加。",
              "- 生产化代理：Usage Share > 0、Work Use Share ≥ 80%、Automation Share ≥ 80%、Directive Share ≥ 50%。",
              "- Usage Share 的分母：相应月份 Claude 1P API 的全部使用流量；不是某职业中使用 AI 的员工比例。", "",
              "|期间|维度|可见数|达标数|可见单元生产化率|生产化流量份额|已发布流量份额|条件生产化流量份额|",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in packet.get("summary_table", []):
        lines.append(f"|{row.get('period')}|{row.get('grain')}|{row.get('visible_count')}|{row.get('qualified_count')}|{_pct(row.get('visible_production_rate_pct'))}|"
                     f"{_pct(row.get('production_traffic_share_pct'))}|{_pct(row.get('published_usage_share_pct'))}|"
                     f"{_pct(row.get('conditional_production_traffic_share_pct'))}|")
    summary_rows = packet.get("summary_table", [])
    periods = sorted({str(row.get("period")) for row in summary_rows if row.get("period")})
    if len(periods) >= 2:
        previous_period, latest_period = periods[-2], periods[-1]
        indexed = {(row.get("period"), row.get("grain")): row for row in summary_rows}
        lines += ["", f"### 四项核心指标月度比较（{previous_period} → {latest_period}）", "",
                  f"|指标|{previous_period}|{latest_period}|变化|", "|---|---:|---:|---:|"]
        comparison_rows = []
        for grain, label, field, change_field in (
            ("occupation", "职业可见单元生产化率", "visible_production_rate_pct", "visible_production_rate_change_pp"),
            ("task", "任务可见单元生产化率", "visible_production_rate_pct", "visible_production_rate_change_pp"),
            ("occupation", "职业生产化流量份额", "production_traffic_share_pct", "production_traffic_share_change_pp"),
            ("task", "任务生产化流量份额", "production_traffic_share_pct", "production_traffic_share_change_pp"),
        ):
            previous = indexed.get((previous_period, grain), {})
            latest = indexed.get((latest_period, grain), {})
            comparison_rows.append(
                f"|{label}|{_pct(previous.get(field))}|{_pct(latest.get(field))}|"
                f"{_signed_pp(latest.get(change_field))}|"
            )
        lines += comparison_rows
    lines += chart_lines("Anthropic 职业/任务可见单元生产化率")
    lines += ["", "### 职业内已确认生产化任务覆盖分布", ""]
    for item in packet.get("occupation_coverage_distribution", {}).get("landmarks", []):
        lines.append(f"- 覆盖至少 {item.get('minimum_coverage_pct'):g}%：{item.get('occupation_count')} 个职业，"
                     f"占有可映射任务职业的 {_pct(item.get('occupation_share_pct'))}。")
    lines += chart_lines("Anthropic 职业内已确认生产化任务覆盖分布")
    for title, rows in (("TOP10 生产化职业", packet.get("top_occupations", [])),
                        ("TOP10 生产化任务", packet.get("top_tasks", []))):
        lines += ["", f"## {title}", ""]
        lines += ["|排名|名称|Usage Share|Work Use Share|Automation Share|Directive Share|",
                  "|---:|---|---:|---:|---:|---:|"]
        lines += [f"|{item.get('rank')}|{item.get('entity_name')}|{_pct(item.get('usage_share_pct'))}|"
                  f"{_pct(item.get('work_use_share_pct'))}|{_pct(item.get('automation_share_pct'))}|"
                  f"{_pct(item.get('directive_share_pct'))}|" for item in rows]
        lines += chart_lines(f"Anthropic {title}")
    ramp = packet.get("supplemental_signals", {}).get("ramp_paid_adoption", {})
    lines += ["", "## Ramp 付费企业采用与 AI 支出补充证据", "",
              "### 第四个补充追踪命题", "",
              f"> {RAMP_CLAIM_TEXT}", "",
              "Ramp 是 L1 的第四个证据位置：它观察企业是否已经发生真实 AI 付款，而不是企业自报、员工自报或 Claude 任务流量。该命题有自己的状态和图表，但不改写 BTOS/RPS/Anthropic 三轴的 `overall_status`。", "",
              "Ramp 不进入上面的三轴整体判断。它观察的是 Ramp 支付网络中发生 AI 正向交易的企业 cohort，与 BTOS（调查企业）、RPS（就业成年人）和 Anthropic（Claude 流量）的主体、分母和技术范围不同，因此只作方向性印证。", "",
              "### Ramp 方法卡", "",
              "- 采用判定：企业当月通过 Ramp corporate card、invoice 或 ACH 等渠道对 AI 产品/服务发生正向付款；分母为 Ramp 相关企业 cohort。",
              "- 报告保留五个 source-native scope 的独立结果；各 scope 的指标定义、统计数量和数据注释统一放在文末，不在方法卡中重复展开。",
              "- 未纳入首版：企业规模和地理下钻。免费工具、个人账户、非 Ramp 付款及 Ramp 客户选择偏差会造成覆盖偏差。", "",
              f"- 当前状态：`{ramp.get('status', 'unavailable')}`；可用 scope {ramp.get('available_scope_count', 0)}/{ramp.get('scope_count', 5)}。"
              ]
    ramp_summaries = [_ramp_series_summary(ramp, scope_name) for scope_name in (
        "adoption_overall", "adoption_overall_models", "adoption_sector",
        "spend_per_employee_overall", "model_market_share_overall")]
    overall_summary = next(item for item in ramp_summaries if item["scope"] == "adoption_overall")
    ramp_slices = ramp.get("slices") or {}
    overall_rows = _ramp_level_rows(ramp_slices.get("adoption_overall") or {})
    overall_rows = sorted(overall_rows, key=lambda row: str(row.get("period", "")))
    first_overall = overall_rows[0].get("value") if overall_rows else None
    if overall_summary["latest_value"] is not None:
        change_text = (f"，较 {overall_summary['prior_period']} 变动 {_signed_pp(overall_summary['change_pp'])}"
                       if overall_summary.get("prior_value") is not None else "")
        start_text = _pct(first_overall) if first_overall is not None else "起始期不可用"
        lines.append(f"- Ramp 付费采用率从起始可见期的 {start_text} 上升到 {_pct(overall_summary['latest_value'])}（{overall_summary['latest_period']}）{change_text}；"
                     f"这说明 Ramp 支付网络中发生 AI 正向付款的企业广度扩大，但不是全美企业采用率。")
    # Explain the commercial signal in prose before showing the detailed metric
    # notes.  These statements use only source-native latest-period rows.
    sector_rows = (ramp_slices.get("adoption_sector") or {}).get("rows") or []
    sector_period = (ramp_slices.get("adoption_sector") or {}).get("period") or ""
    sector_latest = [row for row in sector_rows if str(row.get("period", ""))[:7] == str(sector_period)[:7]]
    sector_latest.sort(key=lambda row: float(row.get("value") or 0), reverse=True)
    if sector_latest:
        leaders = "、".join(f"{row.get('segment', '—')} {_pct(row.get('value'))}" for row in sector_latest[:3])
        lines.append(f"- 行业扩散并不均匀：最新期采用率最高的三个 NAICS 行业为 {leaders}；这表示商业化先在部分行业集中，再向其他行业扩散。")
    vendor_rows = (ramp_slices.get("adoption_overall_models") or {}).get("rows") or []
    vendor_period = (ramp_slices.get("adoption_overall_models") or {}).get("period") or ""
    vendor_latest = [row for row in vendor_rows if str(row.get("period", ""))[:7] == str(vendor_period)[:7]]
    vendor_latest.sort(key=lambda row: float(row.get("value") or 0), reverse=True)
    if vendor_latest:
        vendor_text = "、".join(f"{row.get('segment', '—')} {_pct(row.get('value'))}" for row in vendor_latest[:3])
        lines.append(f"- 供应商扩散也可见：最新期采用率靠前的 vendor 为 {vendor_text}；vendor 之间可重叠，不能相加为市场份额。")
    spend_rows = (ramp_slices.get("spend_per_employee_overall") or {}).get("rows") or []
    spend_period = (ramp_slices.get("spend_per_employee_overall") or {}).get("period") or ""
    spend_latest = {str(row.get("quantile", "")).casefold(): row for row in spend_rows
                    if str(row.get("period", ""))[:7] == str(spend_period)[:7]}
    if spend_latest:
        median = spend_latest.get("median", {}).get("value")
        top10 = spend_latest.get("top10", {}).get("value")
        spend_sentence = f"最新期 AI 支出/员工中位数为 ${float(median):,.2f}" if median is not None else "最新期 AI 支出/员工中位数不可用"
        if top10 is not None:
            spend_sentence += f"，Top 10% 企业中位数为 ${float(top10):,.2f}"
        spend_sentence += "；这反映采购强度和集中度，不等于员工使用率。"
        lines.append(f"- {spend_sentence}")
    model_rows = (ramp_slices.get("model_market_share_overall") or {}).get("rows") or []
    model_period = (ramp_slices.get("model_market_share_overall") or {}).get("period") or ""
    model_latest = [row for row in model_rows if str(row.get("period", ""))[:7] == str(model_period)[:7]]
    model_latest.sort(key=lambda row: float(row.get("value") or 0), reverse=True)
    if model_latest:
        model_text = "、".join(f"{row.get('provider', '—')}/{row.get('model', '—')} {_pct(row.get('value'))}" for row in model_latest[:3])
        lines.append(f"- 模型市场份额使用 Token Spend Management cohort 的 API 支出归因，最新期靠前的模型为 {model_text}；它描述支出流向，不是企业采用率。")
    lines += ["", "### Ramp 图表", "",
              "下列五张图分别对应五个固定 scope；只展示源数据实际提供的序列，不把不同主体拼成一个总指标。"]
    lines += chart_lines("Ramp ")
    lines += ["", "### 与其他来源的使用边界", "",
              "- 若 Ramp adoption 与 BTOS 当前采用广度同向上升，只写‘方向性印证’，不计算 Ramp−BTOS 差值、平均值或加权统一采用率。",
              "- Ramp adoption 明显高于 BTOS 时，优先解释支付网络、正向交易定义、客户构成和免费/非 Ramp 付款覆盖差异；不得改写为全美企业采用率。",
              "- Ramp spend 与 model share 是商业化/支出强度背景，不是员工持续使用或 Anthropic 任务生产化率。"]
    lines += ["", "## 指标公式、限制与来源", "",
              "### Census BTOS", "",
              "- 当前采用广度 = 过去两周回答在任一业务职能使用 AI 的加权企业数 ÷ 在范围雇主企业加权总数 × 100%。来源为美国人口普查局 BTOS Core；只使用 2025-11-17 后新口径。",
              "- 四期移动平均 = 最近四个连续可比 BTOS period 的当前采用广度简单平均；standard error 单独披露，本文不作统计显著性推断。",
              "- 企业占比不是员工使用率、付费席位率或任务自动化率。", "",
              "### RPS / FRED", "",
              "- 上周工作使用率 = 过去一周至少一次为工作使用 GenAI 的就业成年人 ÷ 美国 18–64 岁目标就业人口 × 100%。",
              "- 每周持续使用代理 = 上周工作使用率 ÷ 曾为工作使用率；每日持续使用代理 = 每日工作使用率 ÷ 曾为工作使用率。两者是总体比例之比，不是 cohort 留存率。",
              "- AI 辅助工时和节省工时均为受访者自报；不能证明企业批准或正式部署。", "",
              "### Anthropic Economic Index", "",
              "- 职业/任务可见单元生产化率 = 达到生产化标准且公开的单元数 ÷ 有公开记录的单元数。",
              "- 职业/任务生产化流量份额 = 达标单元的 Usage Share 之和；分母是相应 1P API 总流量。",
              "- 已发布流量份额 = 所有公开单元 Usage Share 之和；隐私过滤或未发布单元不按零处理。",
              "- 条件生产化流量份额 = 达标单元 Usage Share ÷ 已发布单元 Usage Share。",
              "- Anthropic 达标标准：Work Use Share ≥80%、Automation Share ≥80%、Directive Share ≥50%，且 Usage Share >0。",
              "- BTOS、RPS 和 Anthropic 的主体、分母、地区与频率不同，只作方向性印证。",
              "", "### Ramp 五个固定观测量：指标与数据注释", "",
              "Ramp 来源：[Ramp AI Index](https://ramp.com/data/ai-index#adoption#overall)。以下 scope 保持独立统计，不与 BTOS、RPS 或 Anthropic 做加权融合。",
              "", "|scope|指标说明与公式|参与统计维度/数量|最新期间|", "|---|---|---|---|"]
    scope_questions = {
        "adoption_overall": "付费企业采用广度 = 当月对 AI 产品/服务发生正向 Ramp 付款的企业数 ÷ Ramp 相关企业 cohort 企业数 ×100%。",
        "adoption_overall_models": "模型供应商采用率 = 对相应 vendor 发生正向付款的 Ramp 企业数 ÷ 该 cohort 企业数 ×100%；一家企业可同时采用多个 vendor，不能相加为 100%。",
        "adoption_sector": "行业采用率 = 各 NAICS 行业中发生正向 AI 付款的企业数 ÷ 该行业 Ramp 企业 cohort 企业数 ×100%；行业曲线不是总体的加总分解。",
        "spend_per_employee_overall": "AI 支出/员工 = Ramp 企业 AI 月支出 ÷ 员工数的 source-native 分位数（Median、Top 10%、Top 1%）；不是平均值。当前公开导出未提供 Top 30%，不插值。",
        "model_market_share_overall": "模型 API 支出份额 = Token Spend Management 连接企业中归因到 provider/model 的 API spend ÷ 该 cohort API spend ×100%；不是全 Ramp 企业采用率。",
    }
    for item in ramp_summaries:
        scope_name = item["scope"]
        lines.append(f"|`{scope_name}`|{scope_questions[scope_name]}|{item['row_count']} 条 observation，{item['period_count']} 个期间|{item['latest_period'] or '—'}|")
    remaining = [item for item in descriptors if item.get("png_path") not in rendered_paths]
    if remaining:
        lines += ["", "## 其他受治理图表", ""]
        for item in remaining:
            lines += chart_lines(str(item.get("title", "")))
    tables = packet.get("table_descriptors", [])
    if tables:
        lines += ["", "## 可复算数据表", ""]
        for item in tables:
            lines.append(f"- {item.get('table_name')}（{item.get('row_count')} 行）："
                         f"[CSV]({item.get('csv_path')}) · [JSON]({item.get('json_path')}) · "
                         f"rows hash `{item.get('rows_hash')}`")
    lines += ["", "### 数据警告", ""] + [f"- {warning}" for warning in packet.get("warnings", [])]
    lines += ["", f"Snapshot manifest：`{(packet.get('manifest') or {}).get('snapshot_id', '未生成')}`", ""]
    return "\n".join(lines)
