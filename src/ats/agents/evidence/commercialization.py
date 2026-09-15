"""Governed L1 Evidence Observer for AI commercialization capability.

This Observer answers one scoped question first: are Frontier AI Labs turning
model usage into scaled revenue growth?  It reads only through DataProducts and
never touches Chain, scoring, portfolio, risk or trading workflows.  Retention,
unit economics and business-model durability remain ``not_yet_observed`` by
construction; revenue growth alone never becomes a "sustainable business model"
claim.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

CONSUMER = "evidence_observer"
COMMERCIALIZATION_CLAIM_ID = "ai_frontier_labs_commercialization"
COMMERCIALIZATION_CLAIM_DEFINITION_VERSION = "v1"
COMMERCIALIZATION_CLAIM_TEXT = (
    "模型公司能否把持续使用转化为高质量、可留存且具有合理单位经济的收入，并形成可持续商业模式？")
REVENUE_SECTION_ID = "frontier_labs_revenue_scale_and_trend"
REVENUE_SECTION_TEXT = "OpenAI、Anthropic 等 Frontier AI Labs 是否持续把模型使用转化为规模化收入增长？"
OPENROUTER_SECTION_ID = "openrouter_routed_usage_and_competition"
OPENROUTER_SECTION_TEXT = "OpenRouter 公共路由 token 用量是否持续扩大，并在模型厂商之间扩散或集中？"

# The full proposition stays fixed.  Only the sections with governed evidence
# may report ``observed``; the rest are explicit coverage gaps.
SECTION_COVERAGE: tuple[tuple[str, str], ...] = (
    ("revenue_scale_and_growth", "收入规模与增长"),
    ("revenue_retention", "收入留存与续费"),
    ("unit_economics", "单位经济（毛利、推理成本、获客效率）"),
    ("business_model_durability", "商业模式可持续性"),
)
SECTION_LABELS = dict(SECTION_COVERAGE)
OBSERVED_SECTIONS = {REVENUE_SECTION_ID: "revenue_scale_and_growth"}

TREND_LABELS = {
    "expanding": "扩大",
    "contracting": "收缩",
    "stable": "基本持平",
    "mixed": "混合波动",
    "insufficient_history": "历史不足",
    "unavailable": "数据不可用",
}
OVERALL_BY_SECTION = {
    "expanding": "revenue_monetization_expanding_but_economics_unverified",
    "contracting": "revenue_monetization_contracting_but_economics_unverified",
    "stable": "revenue_monetization_stable_but_economics_unverified",
    "mixed": "revenue_monetization_mixed_but_economics_unverified",
    "partial": "revenue_monetization_partial_but_economics_unverified",
    "insufficient_history": "revenue_history_insufficient_and_economics_unverified",
    "unavailable": "unavailable",
}
OVERALL_INTERPRETATION = {
    "revenue_monetization_expanding_but_economics_unverified":
        "收入兑现方向已观察到：Frontier AI Labs 的可比收入规模在已披露的离散期间上持续扩大；"
        "留存与单位经济尚未验证，因此不能宣称商业模式已可持续。",
    "revenue_monetization_contracting_but_economics_unverified":
        "已披露的可比收入序列呈收缩方向；留存与单位经济仍未被观察，无法判断收缩的性质。",
    "revenue_monetization_stable_but_economics_unverified":
        "已披露的可比收入序列基本持平；留存与单位经济仍未被观察。",
    "revenue_monetization_mixed_but_economics_unverified":
        "已披露的可比收入序列方向冲突；留存与单位经济仍未被观察。",
    "revenue_monetization_partial_but_economics_unverified":
        "只有部分实验室具备足够历史，方向判断只覆盖这些公司；留存与单位经济仍未被观察。",
    "revenue_history_insufficient_and_economics_unverified":
        "可比历史点数不足或跨度不足，暂不判断收入方向；留存与单位经济亦未被观察。",
    "unavailable": "没有通过质量门的收入观察；商业化总命题暂不可判断，不做缺失即零的推断。",
}

METHODOLOGY_LIMITS = [
    "Labs 是公司级收入样本，不能代表整个 L1 应用层收入。",
    "年化运行率不是审计收入，也不等同于合同 ARR；口径不同时不计算差额、倍数或排名。",
    "缺失月份是真实缺口：不插值、不前向填充、不把披露日当作收入参考期。",
    "收入增长只证明收入兑现，不证明留存、毛利、客户集中度或单位经济。",
]


def _status_label(status: str) -> str:
    return TREND_LABELS.get(status, status)


def _fmt_bn(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{numeric:,.2f}"


def _fmt_rate(value: Any, *, signed: bool = True) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{numeric * 100:+.2f}%" if signed else f"{numeric * 100:.2f}%"


def _fmt_share(value: Any) -> str:
    return _fmt_rate(value, signed=False)


def _fmt_tokens(value: Any) -> str:
    """Compact token counts for human-facing reports; raw tables stay exact."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(numeric) >= scale:
            return f"{numeric / scale:.2f}{suffix}"
    return f"{numeric:,.2f}"


def _report_asset_ref(value: Any) -> str:
    """Return a report-local asset reference so Markdown works when opened."""
    if not value:
        return ""
    return Path(str(value)).name


def _openrouter_chart_note(slug: str, facts: dict[str, Any]) -> tuple[str, str]:
    """Return a short definition and a data-driven reading for each chart."""
    trend = facts.get("trend") or {}
    concentration = facts.get("concentration") or {}
    top_authors = facts.get("top_authors") or []
    top_models = facts.get("top_models") or []
    if slug == "openrouter_token_volume_weekly":
        return (
            "完整 UTC 周的公共路由 `total_tokens` 总量；橙线为 4 周移动平均。",
            f"最新完整周为 {_fmt_tokens(facts.get('latest_total_tokens'))}；最近 4 周相对前 4 周"
            f"为 {_status_label(str(trend.get('status', 'insufficient_history')))}，变化率 {_fmt_rate(trend.get('change_rate'))}。",
        )
    if slug == "openrouter_author_share_100":
        leader = top_authors[0] if top_authors else {}
        return (
            "最近 12 个完整 UTC 周，各模型作者占公共路由 token 的份额；未识别作者与官方 Other 单列。",
            f"最新完整周最大作者为 `{leader.get('author', '—')}`，份额 {_fmt_share(leader.get('share'))}；"
            f"Top-3 合计 {_fmt_share(concentration.get('top3_share'))}。",
        )
    if slug == "openrouter_model_leaderboard":
        leader = min((item for item in top_models if item.get("rank") is not None),
                     key=lambda item: item.get("rank", 10**9), default=(top_models[0] if top_models else {}))
        return (
            "按 2026-01-01 起的完整 UTC 周累计 token 选取稳定模型集合；发布日期后缀已合并到同一模型版本。柱形显示各周 token 总量，折线显示相对全部模型的排名（1 为最高）。",
            f"最新完整周第一名为 `{leader.get('model_name', leader.get('model_permaslug', '—'))}`，"
            f"份额 {_fmt_share(leader.get('share'))}；图中同时可见其与其他模型的排名变化。",
        )
    if slug == "openrouter_concentration":
        return (
            "Top-3、Top-5 作者份额与 HHI 的完整周趋势；HHI 为作者份额平方和，范围 0–1，越高表示越集中。",
            f"最新完整周 Top-3 为 {_fmt_share(concentration.get('top3_share'))}、"
            f"Top-5 为 {_fmt_share(concentration.get('top5_share'))}，HHI 为 {float(concentration.get('hhi', 0) or 0):.2f}。",
        )
    return ("OpenRouter 公共路由 token 派生指标。", "请结合图例和数据表复核。")


def observe_ai_commercialization(
    *,
    periods: list[str] | None = None,
    as_of: datetime | None = None,
    top_n: int = 10,
    chart_dir: str = "",
    workflow_scope: dict[str, str] | None = None,
    claim_definition_version: str = "v1",
    products=None,
) -> dict[str, Any]:
    """Read-only L1 commercialization packet, scoped to its own fixed claim."""
    if products is None:
        from ...data.products import get_platform_data_products

        products = get_platform_data_products()
    scope = dict(workflow_scope or {})
    sections = list((scope.get("evidence_sections") or [])) or [REVENUE_SECTION_ID]
    supplemental = list(scope.get("supplemental_claims") or [])
    openrouter_enabled = OPENROUTER_SECTION_ID in sections or any(
        str(item.get("claim_id")) == OPENROUTER_SECTION_ID for item in supplemental
    )
    if REVENUE_SECTION_ID not in sections and not openrouter_enabled:
        return {
            "status": "section_not_enabled", "consumer": CONSUMER,
            "source_access": "data_products_only",
            "claim_id": COMMERCIALIZATION_CLAIM_ID,
            "claim_definition_version": COMMERCIALIZATION_CLAIM_DEFINITION_VERSION,
            "claim_text": COMMERCIALIZATION_CLAIM_TEXT,
            "reason": f"配置的 evidence_sections 未包含 {REVENUE_SECTION_ID}",
            "facts": [], "warnings": ["当前配置未启用收入或 OpenRouter 路由用量证据段。"],
        }

    scope_suffix = ""
    if scope:
        scope_suffix = ":" + ":".join(str(scope.get(key, "")) for key in ("sector", "layer"))
    if not hasattr(products, "frontier_labs_revenue_evidence_bundle") and REVENUE_SECTION_ID in sections:
        return {
            "status": "unavailable", "consumer": CONSUMER,
            "source_access": "data_products_only",
            "claim_id": COMMERCIALIZATION_CLAIM_ID,
            "claim_definition_version": COMMERCIALIZATION_CLAIM_DEFINITION_VERSION,
            "claim_text": COMMERCIALIZATION_CLAIM_TEXT,
            "reason": "DataProducts 未注册 Frontier Labs 收入证据包",
            "facts": [], "warnings": ["收入数据产品不可用；不影响生产化与应用扩散 Observer。"],
        }
    if REVENUE_SECTION_ID in sections and hasattr(products, "frontier_labs_revenue_evidence_bundle"):
        bundle = products.frontier_labs_revenue_evidence_bundle(
            as_of=as_of, snapshot_consumer=CONSUMER,
            snapshot_purpose=(f"{COMMERCIALIZATION_CLAIM_ID}:{claim_definition_version}"
                              f"{scope_suffix}"))
    else:
        bundle = {"status": "unavailable", "section": {"status": "unavailable",
                  "reason": "收入证据段未启用。"}, "companies": [], "limitations": []}
    openrouter_bundle = None
    if openrouter_enabled:
        if hasattr(products, "openrouter_token_evidence_bundle"):
            openrouter_bundle = products.openrouter_token_evidence_bundle(
                as_of=as_of, snapshot_consumer=CONSUMER,
                snapshot_purpose=(f"{COMMERCIALIZATION_CLAIM_ID}:{claim_definition_version}:openrouter"
                                  f"{scope_suffix}"))
        else:
            openrouter_bundle = {"status": "unavailable", "section_id": OPENROUTER_SECTION_ID,
                                 "section_claim_text": OPENROUTER_SECTION_TEXT,
                                 "warnings": ["OpenRouter token数据产品未注册。"], "facts": {}}
    return _build_packet(bundle, openrouter_bundle=openrouter_bundle, chart_dir=chart_dir,
                         workflow_scope=scope, claim_definition_version=claim_definition_version,
                         top_n=top_n)


def _build_packet(bundle: dict[str, Any], *, openrouter_bundle: dict[str, Any] | None = None,
                  chart_dir: str,
                  workflow_scope: dict[str, str],
                  claim_definition_version: str, top_n: int) -> dict[str, Any]:
    companies: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    warnings: list[str] = list(bundle.get("limitations") or [])
    for company in bundle.get("companies") or []:
        trend = dict(company.get("trend") or {})
        trend["status_label"] = _status_label(str(trend.get("status") or ""))
        company = dict(company, trend=trend)
        companies.append(company)
        headline = company.get("headline") or {}
        record = headline.get("record") or {}
        facts.append({
            "kind": "company_revenue_status",
            "company": company.get("company"),
            "entity_id": company.get("entity_id"),
            "latest_period": record.get("period"),
            "latest_value_usd_bn": record.get("value_usd_bn"),
            "metric_id": record.get("metric_id"),
            "observation_identity": record.get("observation_identity"),
            "trend_status": trend.get("status"),
            "trend_status_label": trend.get("status_label"),
            "comparable_point_count": trend.get("comparable_point_count"),
            "span_days": trend.get("span_days"),
            "net_change_usd_bn": trend.get("net_change_usd_bn"),
            "net_change_rate": trend.get("net_change_rate"),
            "input_observation_ids": trend.get("input_observation_ids", []),
        })
        if trend.get("status") == "insufficient_history":
            warnings.append(
                f"{company.get('company')}：可比历史不足（点数 {trend.get('comparable_point_count')}"
                f"、跨度 {trend.get('span_days')} 天），只展示最新水平，不判断方向。")
        if trend.get("status") == "unavailable":
            warnings.append(f"{company.get('company')}：没有通过质量门的收入观察。")
        if trend.get("period_gap", {}).get("status") == "gap":
            missing = trend["period_gap"].get("missing_periods") or []
            warnings.append(
                f"{company.get('company')}：存在未披露月份缺口（{', '.join(missing[:6])}"
                f"{'…' if len(missing) > 6 else ''}），不插值、不补值。")

    section_status = str((bundle.get("section") or {}).get("status") or "unavailable")
    overall_status = OVERALL_BY_SECTION.get(
        section_status, "revenue_monetization_partial_but_economics_unverified")
    coverage = {
        key: ("observed" if key == "revenue_scale_and_growth"
              and section_status not in {"unavailable"} else "not_yet_observed")
        for key, _ in SECTION_COVERAGE
    }
    if section_status == "unavailable":
        coverage["revenue_scale_and_growth"] = "not_yet_observed"

    for conflict in bundle.get("source_conflicts") or []:
        warnings.append(
            f"{conflict.get('company')} {conflict.get('period')}："
            f"两个来源可比值差异 {float(conflict.get('relative_difference') or 0) * 100:.1f}% "
            "超过阈值，已并列披露。")
    if not (bundle.get("comparability") or {}).get("across_companies", True):
        warnings.append("两家公司的最新可比口径不同：只并列趋势，不计算差额、倍数或排名。")

    methodology_card = {
        "claim_id": COMMERCIALIZATION_CLAIM_ID,
        "claim_definition_version": claim_definition_version,
        "title": "Frontier AI Labs 收入水平与趋势（L1 商业化能力首版证据）",
        "claim_text": COMMERCIALIZATION_CLAIM_TEXT,
        "section_claim_text": REVENUE_SECTION_TEXT,
        "sources": [
            {"source_id": "sacra_public_company_profiles",
             "usage": "最新收入观察主信源；每 7 天探测两个公开页面，一次采集后离线复用。",
             "observation_identity": "按原文区分第三方估算、公司披露、媒体披露与预测。"},
            {"source_id": "tickertrends_public_research",
             "usage": "冻结的一次性 2026 年 1—6 月历史回填，不参与周期发现与最新 headline 竞争。",
             "observation_identity": "第三方估算；raw label 写作 ARR 时规范化为年化运行率。"}],
        "metric_definitions": {
            "reported_arr": "公司自行宣布的合同/订阅 ARR。",
            "annualized_revenue_run_rate": "由某一期间收入年化得到的运行率，不是审计收入。",
            "trailing_revenue": "已闭合期间实际记录的收入，不年化。",
            "forward_revenue_projection": "未来年度预测或目标，永不进入历史实际序列。"},
        "trend_rule": {
            "cell_key": "公司 × 计量口径 × 观察身份 × 币种 × methodology regime",
            "minimum_points": 3, "minimum_days": 60,
            "direction_threshold": "净变化率绝对值 ≥ 10% 且斜率同向，且最近一个可比变化未构成 ≥10% 的反向变化",
            "slope": "按真实天数计算，不是按期数计算",
            "prohibited": ["插值", "前向填充", "把披露日当作收入参考期", "跨公司平均增速"]},
        "comparability": bundle.get("comparability"),
        "quality_gate": [
            "不含日期或计量口径的数值只保留 artifact 与诊断，不进入正式 observation。",
            "产品级收入（如 Claude Code、广告业务）不进入公司级序列，也不与公司收入相加。",
            "付费墙脱敏的表格行记录为覆盖缺口，不写零值。"],
        "limitations": METHODOLOGY_LIMITS,
        "lineage_pointer": "manifest.snapshot_id / companies[].lineage",
    }
    if openrouter_bundle is not None:
        methodology_card["sources"].append({
            "source_id": "openrouter_rankings",
            "usage": "官方 Rankings Data API 的公共路由 token 日度数据；按完整 UTC 周聚合。",
            "observation_identity": "total_tokens = prompt + completion；不代表全市场收入或 request share。",
            "license": "CC BY 4.0",
        })
        methodology_card["openrouter_metrics"] = {
            "total_tokens": "完整 UTC 周内 accepted daily total_tokens 之和",
            "author_share": "作者 token / 该完整周总 token",
            "top3_top5": "按作者 token 排序的前 3/5 名占完整周总 token",
            "hhi": "命名或未知作者 share 的平方和；Other 不反向分配",
        }
        methodology_card["openrouter_scope"] = {
            "coverage": "OpenRouter 公共路由请求；private requests、直连厂商 API 和其他路由平台不在分母内",
            "period": "日度 UTC；正式趋势只用完整 UTC 周，数据起点为 2025-01-01（官方可用范围）",
            "token_definition": "prompt + completion tokens；不同上游 tokenizer 不完全可比",
            "top_n": "每日 Top 50 模型加官方 Other；Other 不反向分配给作者",
            "free_routes": "免费模型和促销路由保留在总量，可能扭曲商业意图",
            "share_boundary": "作者 token share 不是 request share、spend share 或 revenue share",
        }

    openrouter_status = str((openrouter_bundle or {}).get("status") or "unavailable")
    packet: dict[str, Any] = {
        # The two evidence sections are independently publishable.  A live
        # OpenRouter bundle must therefore produce a reviewable report even
        # when the platform has not yet loaded a Frontier Labs revenue vintage.
        "status": "ok" if bundle.get("status") == "ok" or openrouter_status in {"ok", "insufficient_history"}
        else "unavailable",
        "consumer": CONSUMER,
        "source_access": "data_products_only",
        "claim_id": COMMERCIALIZATION_CLAIM_ID,
        "claim_definition_version": claim_definition_version,
        "claim_text": COMMERCIALIZATION_CLAIM_TEXT,
        "evidence_sections": [
            {"section_id": REVENUE_SECTION_ID,
             "section_claim_text": REVENUE_SECTION_TEXT,
             "status": section_status,
             "status_label": _status_label(section_status)},
        ],
        "coverage": coverage,
        "section_status": section_status,
        "section_reasoning": (bundle.get("section") or {}).get("reason", ""),
        "overall_status": overall_status,
        "overall_interpretation": OVERALL_INTERPRETATION.get(overall_status, ""),
        "economics_verified": False,
        "retention_verified": False,
        "companies": companies,
        "history_rows": bundle.get("history_rows") or [],
        "rows_hash": bundle.get("rows_hash", ""),
        "source_conflicts": bundle.get("source_conflicts") or [],
        "comparability": bundle.get("comparability"),
        "methodology_card": methodology_card,
        "facts": facts,
        "warnings": sorted(set(warnings)),
        "snapshot_manifest": bundle.get("manifest"),
        "manifest": bundle.get("manifest"),
        "lineage": bundle.get("lineage"),
        "derivation_version": bundle.get("derivation_version"),
        "section_rule": bundle.get("section"),
        "visualization_descriptors": [],
        "table_descriptors": [],
        "openrouter": openrouter_bundle,
        "cross_evidence": {"status": "not_evaluated", "rule": "收入与路由 token 只做方向性并列，不做金额-token换算。"},
    }
    if openrouter_bundle is not None:
        open_status = str(openrouter_bundle.get("status") or "unavailable")
        packet["evidence_sections"].append({"section_id": OPENROUTER_SECTION_ID,
                                             "section_claim_text": OPENROUTER_SECTION_TEXT,
                                             "status": open_status,
                                             "status_label": "可用" if open_status == "ok" else _status_label(open_status)})
        packet["warnings"].extend(openrouter_bundle.get("warnings") or [])
        packet["warnings"] = sorted(set(packet["warnings"]))
        packet["facts"].append({"kind": OPENROUTER_SECTION_ID,
                                **(openrouter_bundle.get("facts") or {})})
        revenue_status = section_status
        route_status = str(openrouter_bundle.get("directional_status") or open_status)
        if revenue_status not in {"unavailable", "insufficient_history"} and route_status not in {"unavailable", "insufficient_history"}:
            aligned = revenue_status == route_status
            if aligned and revenue_status == "expanding":
                cross_status = "revenue_and_routed_demand_expanding"
            elif aligned:
                cross_status = "directionally_synchronous"
            else:
                cross_status = "mixed_commercialization_evidence"
            packet["cross_evidence"] = {"status": cross_status,
                                         "revenue_status": revenue_status, "openrouter_status": route_status,
                                         "interpretation": "两个独立证据方向一致" if aligned else "两个独立证据方向不一致；不做归因。"}
        elif revenue_status not in {"unavailable"}:
            packet["cross_evidence"] = {"status": "revenue_only", "revenue_status": revenue_status, "openrouter_status": route_status}
        elif route_status not in {"unavailable"}:
            packet["cross_evidence"] = {"status": "openrouter_only", "revenue_status": revenue_status, "openrouter_status": route_status}
    if workflow_scope:
        packet["workflow_scope"] = dict(workflow_scope)
    if chart_dir:
        from .commercialization_visualization import render_frontier_labs_revenue_charts

        rendered = render_frontier_labs_revenue_charts(packet=packet, output_dir=chart_dir)
        packet["visualization_descriptors"] = rendered.get("descriptors") or []
        packet["table_descriptors"] = rendered.get("tables") or []
        packet["chart_font"] = {"font_path": rendered.get("font_path", ""),
                                "glyph_check": rendered.get("glyph_status", {})}
        if rendered.get("visualization_warning"):
            packet["warnings"].append(rendered["visualization_warning"])
            packet["warnings"] = sorted(set(packet["warnings"]))
            packet["visualization_warning"] = rendered["visualization_warning"]
        if openrouter_bundle is not None:
            from .openrouter_visualization import render_openrouter_charts

            open_rendered = render_openrouter_charts(bundle=openrouter_bundle, output_dir=chart_dir)
            packet["visualization_descriptors"].extend(open_rendered.get("descriptors") or [])
            packet["table_descriptors"].extend(open_rendered.get("tables") or [])
            if open_rendered.get("font_path"):
                packet.setdefault("chart_font", {})["font_path"] = open_rendered["font_path"]
            if open_rendered.get("glyph_status"):
                packet.setdefault("chart_font", {})["glyph_check_openrouter"] = open_rendered["glyph_status"]
            if open_rendered.get("visualization_warning"):
                packet["warnings"].append(open_rendered["visualization_warning"])
                packet["warnings"] = sorted(set(packet["warnings"]))
    packet["context"] = _build_context(packet)
    return packet


def _build_context(packet: dict[str, Any]) -> dict[str, Any]:
    """Bounded two-tier context; the compact tier never flattens web pages."""
    essential = {
        "claim_id": packet.get("claim_id"),
        "claim_definition_version": packet.get("claim_definition_version"),
        "claim_text": packet.get("claim_text"),
        "coverage": packet.get("coverage"),
        "section_status": packet.get("section_status"),
        "overall_status": packet.get("overall_status"),
        "overall_interpretation": packet.get("overall_interpretation"),
        "economics_verified": packet.get("economics_verified"),
        "retention_verified": packet.get("retention_verified"),
        "companies": [
            {"company": item.get("company"), "entity_id": item.get("entity_id"),
             "latest_period": (item.get("headline") or {}).get("record", {}).get("period"),
             "latest_value_usd_bn": (item.get("headline") or {}).get("record", {}).get("value_usd_bn"),
             "metric_id": (item.get("headline") or {}).get("record", {}).get("metric_id"),
             "observation_identity": (item.get("headline") or {}).get("record", {}).get("observation_identity"),
             "trend_status": (item.get("trend") or {}).get("status"),
             "comparable_point_count": (item.get("trend") or {}).get("comparable_point_count"),
             "span_days": (item.get("trend") or {}).get("span_days"),
             "net_change_rate": (item.get("trend") or {}).get("net_change_rate"),
             "observation_ids": (item.get("trend") or {}).get("input_observation_ids", [])}
            for item in packet.get("companies") or []],
        "source_conflicts": packet.get("source_conflicts") or [],
        "openrouter": {
            "status": (packet.get("openrouter") or {}).get("status"),
            "latest_complete_week": ((packet.get("openrouter") or {}).get("facts") or {}).get("latest_complete_week"),
            "latest_total_tokens": ((packet.get("openrouter") or {}).get("facts") or {}).get("latest_total_tokens"),
            "trend": ((packet.get("openrouter") or {}).get("facts") or {}).get("trend"),
            "top_authors": [
                {"author": item.get("author"), "share": item.get("share"), "tokens": item.get("tokens")}
                for item in (((packet.get("openrouter") or {}).get("facts") or {}).get("top_authors") or [])[:10]
            ],
            "top_models": [
                {"model_permaslug": item.get("model_permaslug"), "author": item.get("author"),
                 "tokens": item.get("tokens"), "share": item.get("share"),
                 "is_free_route": item.get("is_free_route")}
                for item in (((packet.get("openrouter") or {}).get("facts") or {}).get("top_models") or [])[:10]
            ],
            "concentration": ((packet.get("openrouter") or {}).get("facts") or {}).get("concentration"),
        },
        "cross_evidence": packet.get("cross_evidence"),
        "warnings": packet.get("warnings") or [],
        "manifest_id": (packet.get("manifest") or {}).get("snapshot_id"),
        "lineage_pointer": "manifest.snapshot_id / companies[].lineage",
        "rows_hash": packet.get("rows_hash"),
    }
    compact = json.dumps(essential, ensure_ascii=False, sort_keys=True, default=str)
    review_model = {**essential, "methodology_card": packet.get("methodology_card"),
                    "history_rows": packet.get("history_rows"),
                    "comparability": packet.get("comparability"),
                    "openrouter": packet.get("openrouter"),
                    "cross_evidence": packet.get("cross_evidence")}
    review = json.dumps(review_model, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "schema_version": "ai_commercialization_observer_context/v1",
        "compact": compact, "compact_char_count": len(compact), "compact_budget": 12000,
        "review": review, "review_char_count": len(review), "review_budget": 120000,
        "budget_status": "ok" if len(compact) <= 12000 and len(review) <= 120000 else "exceeded",
    }


# --------------------------------------------------------------------------- #
# Chinese formal report
# --------------------------------------------------------------------------- #

def render_ai_commercialization_markdown(packet: dict[str, Any]) -> str:
    """Render the scoped commercialization report from the packet only."""
    lines: list[str] = []
    if packet.get("status") not in {"ok"}:
        lines.extend([
            "# AI 应用层：商业化能力", "",
            f"- 状态：`{packet.get('status', 'unavailable')}`",
            f"- 说明：{packet.get('reason', '没有通过质量门的收入观察。')}", "",
        ])
        lines.extend(f"- {item}" for item in packet.get("warnings", []) or ["无。"])
        return "\n".join(lines)

    scope = packet.get("workflow_scope") or {}
    coverage = packet.get("coverage") or {}
    lines.extend([
        "# AI 应用层：商业化能力", "",
        f"- 命题版本：`{packet.get('claim_definition_version')}`",
        f"- 总命题：{packet.get('claim_text')}",
        f"- 判断：{packet.get('overall_interpretation')}",
        f"- 机器状态：`{packet.get('overall_status')}`；收入部分 `{packet.get('section_status')}`"
        f"（{_status_label(str(packet.get('section_status')))}）",
        f"- 运行范围：{scope.get('sector', '')} / {scope.get('layer', '')}" if scope else "",
        "",
    ])
    lines.extend([
        "## 一、命题覆盖与范围受限结论", "",
        "| 命题维度 | 覆盖状态 |",
        "| --- | --- |",
    ])
    for key, label in SECTION_COVERAGE:
        state = coverage.get(key, "not_yet_observed")
        lines.append(f"| {label} | `{state}` |")
    lines.extend([
        "",
        ("当前没有通过质量门的 Frontier Labs 收入序列；"
         "OpenRouter 路由 token 已提供独立的使用规模/竞争格局证据，但不代表收入。"
         if packet.get("openrouter") is not None and packet.get("section_status") in {"unavailable", "insufficient_history"} else
         "收入兑现方向已观察、留存与单位经济尚未验证；"
         "OpenRouter 路由 token 只作为独立的使用规模/竞争格局证据，不代表收入。"
         if packet.get("openrouter") is not None else
         "首版只实现 `frontier_labs_revenue_scale_and_trend` 一个证据部分。"
         "**收入兑现方向已观察、留存与单位经济尚未验证**；"
         "不得仅凭收入增长宣称商业模式可持续或单位经济成立."),
        "",
        "## 二、Frontier Labs 收入部分判断", "",
        f"- 追踪问题：{REVENUE_SECTION_TEXT}",
        f"- 结论：`{packet.get('section_status')}`（{_status_label(str(packet.get('section_status')))}）；"
        f"{packet.get('section_reasoning', '')}",
        "",
    ])

    lines.extend(["## 三、最新可比收入", "",
                  "| 公司 | 最新参考期 | 最新值（USD 十亿） | 计量口径 | 观察身份 | 最新来源 |",
                  "| --- | --- | --- | --- | --- | --- |"])
    for company in packet.get("companies") or []:
        headline = company.get("headline") or {}
        record = headline.get("record") or {}
        lines.append(
            f"| {company.get('company')} | {record.get('period', '—')} "
            f"| {_fmt_bn(record.get('value_usd_bn'))} "
            f"| {record.get('metric_label', '—')} "
            f"| {record.get('observation_identity_label', '—')} "
            f"| {record.get('source_label', headline.get('selected_source_label', '—'))} |")
    lines.extend(["", "来源优先级只决定主显示，不删除候选，也不把估算升级为公司披露。", ""])

    chart_lines = [item for item in (packet.get("visualization_descriptors") or [])
                   if not str(item.get("chart_slug", "")).startswith("openrouter_")]
    lines.extend(["## 四、Frontier Labs 收入水平与趋势", ""])
    if chart_lines:
        for item in chart_lines:
            lines.extend([
                f"![{item.get('title')}]({_report_asset_ref(item.get('png_path'))})", "",
                f"数据与复现：[sidecar]({_report_asset_ref(item.get('sidecar_path'))}) · "
                f"rows hash `{item.get('rows_hash')}`", "",
            ])
        lines.append("图中只连接同一可比 cell 的离散披露点；"
                     "线段是方向辅助线，不是未披露月份的估计值。")
    else:
        lines.append(f"未生成图表：{packet.get('visualization_warning', '没有可绘制的可比观察。')}")
    lines.append("")

    openrouter = packet.get("openrouter")
    if openrouter is not None:
        facts_open = openrouter.get("facts") or {}
        trend = facts_open.get("trend") or {}
        source_as_of = (openrouter.get("volume") or {}).get("source_as_of") or "—"
        lines.extend(["## 五、OpenRouter 公共路由 token 与模型竞争", "",
                      f"- 追踪问题：{OPENROUTER_SECTION_TEXT}",
                      f"- 规模结论：公共路由 token 用量最近 4 周相对前 4 周"
                      f"{_status_label(str(trend.get('status', 'insufficient_history')))}，"
                      f"变化率 {_fmt_rate(trend.get('change_rate'))}；最新完整周总量为"
                      f" {_fmt_tokens(facts_open.get('latest_total_tokens'))} tokens。",
                      "- 解释边界：OpenRouter 的公开路由 token 是平台流量与模型竞争代理，不能当作全球 token 总量、模型公司收入或 request share。",
                      ""])
        concentration = facts_open.get("concentration") or {}
        hhi_value = concentration.get("hhi")
        hhi_display = "—" if hhi_value is None else f"{float(hhi_value):.2f}"
        top3_display = _fmt_share(concentration.get('top3_share'))
        top5_display = _fmt_share(concentration.get('top5_share'))
        lines.extend([f"- 竞争结构结论：头部作者（模型厂商）呈‘头部集中、多厂商共存’，最新完整周 Top-3/Top-5 作者"
                      f" token 份额为 {top3_display}/{top5_display}，HHI 为 {hhi_display}；"
                      "因此该证据支持‘集中度与扩散并存’，而不是单一厂商垄断。",
                      f"- 关键证据：最新完整 UTC 周为 `{facts_open.get('latest_complete_week', '—')}`；"
                      f"官方 API 最新 `meta.as_of` 为 `{source_as_of}`；机器状态为 `{openrouter.get('status', 'unavailable')}`。",
                      "", "| 指标 | 最新完整周 |", "| --- | --- |",
                      f"| Top-3 作者 token 份额 | {_fmt_share(concentration.get('top3_share'))} |",
                      f"| Top-5 作者 token 份额 | {_fmt_share(concentration.get('top5_share'))} |",
                      f"| HHI（0–1；Other 不反向分配） | {hhi_display} |",
                      "", "HHI 说明：公式为 `HHI = Σᵢ sᵢ²`，其中 `sᵢ` 是作者 i 的 token 份额（小数），"
                      "取值范围为 0–1。平方会放大大份额：10 个作者完全均分时 HHI=0.10，单一作者占 100% 时 HHI=1.00，"
                      "所以数值越高表示 token 越集中。这里官方 `Other` 保留为未归属桶、不反向分配给任何作者，"
                      "但仍留在总分母中；因此该 HHI 是已纳入作者桶（`unknown_author` 也作为独立桶保留）集中度的保守下界，"
                      "且在 `Other` 占比变化时不宜与标准全量 HHI 直接比较。",
                      "", "头部作者（按绝对 token）："])
        for item in (facts_open.get("top_authors") or [])[:10]:
            lines.append(f"- `{item.get('author')}`：{_fmt_tokens(item.get('tokens'))} tokens（{_fmt_share(item.get('share'))}）")
        lines.append("")
        if packet.get("cross_evidence"):
            lines.append(f"- 收入 × 路由方向性矩阵：`{packet['cross_evidence'].get('status')}`；"
                         "只做并列方向判断，不做金额-token换算或综合评分。")
        open_desc = [item for item in (packet.get("visualization_descriptors") or [])
                     if str(item.get("chart_slug", "")).startswith("openrouter_")]
        for item in open_desc:
            definition, reading = _openrouter_chart_note(str(item.get("chart_slug", "")), facts_open)
            lines.extend([f"### {item.get('title')}", "",
                          f"指标含义：{definition}",
                          f"图表解读：{reading}", "",
                          f"![{item.get('title')}]({_report_asset_ref(item.get('png_path'))})", "",
                          f"数据与复现：[sidecar]({_report_asset_ref(item.get('sidecar_path'))}) · rows hash `{item.get('rows_hash')}`", ""])
        if not open_desc:
            open_tables = [item for item in (packet.get("table_descriptors") or [])
                           if str(item.get("table_name", "")).startswith("openrouter_")]
            if open_tables:
                lines.append("图表未通过渲染质量门，以下同源结构化表格仍可复核：")
                for item in open_tables:
                    lines.append(f"- `{item.get('table_name')}`：[CSV]({_report_asset_ref(item.get('csv_path'))}) · [JSON]({_report_asset_ref(item.get('json_path'))})")
                lines.append("")

    history_heading = "## 六、历史观察表" if openrouter is not None else "## 五、历史观察表"
    lines.extend([history_heading, "",
                  "| 公司 | 参考期 | 值（USD 十亿） | 计量口径 | 观察身份 | 原始标签 | 来源 | 修订 | observation id |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
    for row in sorted(packet.get("history_rows") or [],
                      key=lambda item: (str(item.get("entity_id")),
                                        str(item.get("period_start")))):
        lines.append(
            f"| {row.get('company')} | {row.get('period')} | {_fmt_bn(row.get('value_usd_bn'))} "
            f"| {row.get('metric_label')} | {row.get('observation_identity_label')} "
            f"| {row.get('raw_metric_label')} | {row.get('source_label')} "
            f"| v{row.get('vintage_count', 1)} | `{row.get('observation_id', '—')}` |")
    lines.append("")

    conflicts = packet.get("source_conflicts") or []
    comparability = packet.get("comparability") or {}
    source_heading = "## 七、来源、口径与冲突" if openrouter is not None else "## 六、来源、口径与冲突"
    lines.extend([source_heading, "",
                  f"- 跨公司可比性：`{comparability.get('across_companies')}`"
                  f"（版本 `{comparability.get('version')}`）"])
    if comparability.get("note"):
        lines.append(f"- {comparability['note']}")
    if conflicts:
        lines.extend(["", "| 公司 | 期间 | 口径 | 相对差异 | 两个值 |", "| --- | --- | --- | --- | --- |"])
        for item in conflicts:
            values = "；".join(
                f"{value.get('source_label')} {value.get('value_usd_bn')}B"
                for value in item.get("values") or [])
            lines.append(f"| {item.get('company')} | {item.get('period')} "
                         f"| {item.get('metric_id', '').split('.')[-1]} "
                         f"| {float(item.get('relative_difference') or 0) * 100:.1f}% | {values} |")
    else:
        lines.append("- 当前没有可比期间超过阈值的跨来源冲突。")
    lines.extend(["", "来源优先级只决定主显示；出现冲突时并列披露，不用优先级掩盖数值分歧。", ""])

    card = packet.get("methodology_card") or {}
    trend_rule = card.get("trend_rule") or {}
    lines.extend([
        ("## 八、指标公式、统计范围与数据缺口" if openrouter is not None
         else "## 七、指标公式、统计范围与数据缺口"), "",
        "- 趋势 cell：`公司 × 计量口径 × 观察身份 × 币种 × methodology regime`。",
        f"- 最小点数 {trend_rule.get('minimum_points', 3)}；最小跨度 "
        f"{trend_rule.get('minimum_days', 60)} 天；"
        f"方向阈值：{trend_rule.get('direction_threshold', '')}。",
        f"- 斜率：{trend_rule.get('slope', '')}。",
        "- 禁止：" + "、".join(trend_rule.get("prohibited") or []) + "。",
        "- 计量口径：",
    ])
    for key, definition in (card.get("metric_definitions") or {}).items():
        lines.append(f"  - `{key}`：{definition}")
    lines.extend(["- 数据缺口："])
    lines.extend(f"  - {item}" for item in (card.get("quality_gate") or []))
    lines.extend(f"  - {item}" for item in (card.get("limitations") or []))
    lines.append("")

    lines.extend([
        ("## 九、尚待建设的留存、单位经济与商业模式证据" if openrouter is not None
         else "## 八、尚待建设的留存、单位经济与商业模式证据"), "",
        "- 收入留存与续费：尚无可观测的留存、净收入留存或续费序列。",
        "- 单位经济：尚无毛利、推理成本、客户集中度或获客效率的受治理数据。",
        "- 商业模式可持续性：在留存与单位经济落地前，不得由收入增长推出该结论。",
        "- 后续补充证据应扩展同一商业化 Observer，不改写生产化与应用扩散 Observer。",
        "",
    ])

    tables = packet.get("table_descriptors") or []
    if tables:
        lines.extend(["## 可复算数据表", ""])
        for item in tables:
            lines.append(f"- {item.get('table_name')}（{item.get('row_count')} 行）："
                         f"[CSV]({_report_asset_ref(item.get('csv_path'))}) · [JSON]({_report_asset_ref(item.get('json_path'))}) · "
                         f"rows hash `{item.get('rows_hash')}`")
        lines.append("")
    warnings = packet.get("warnings") or []
    if warnings:
        lines.extend(["### 数据警告", ""])
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")
    manifest = packet.get("manifest") or {}
    if manifest.get("snapshot_id"):
        lines.append(f"Snapshot manifest：`{manifest['snapshot_id']}`")
    return "\n".join(lines)
