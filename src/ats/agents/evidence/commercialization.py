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
from typing import Any

CONSUMER = "evidence_observer"
COMMERCIALIZATION_CLAIM_ID = "ai_frontier_labs_commercialization"
COMMERCIALIZATION_CLAIM_DEFINITION_VERSION = "v1"
COMMERCIALIZATION_CLAIM_TEXT = (
    "模型公司能否把持续使用转化为高质量、可留存且具有合理单位经济的收入，并形成可持续商业模式？")
REVENUE_SECTION_ID = "frontier_labs_revenue_scale_and_trend"
REVENUE_SECTION_TEXT = "OpenAI、Anthropic 等 Frontier AI Labs 是否持续把模型使用转化为规模化收入增长？"

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
    return f"{numeric:,.1f}"


def _fmt_rate(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{numeric * 100:+.1f}%"


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
    if REVENUE_SECTION_ID not in sections:
        return {
            "status": "section_not_enabled", "consumer": CONSUMER,
            "source_access": "data_products_only",
            "claim_id": COMMERCIALIZATION_CLAIM_ID,
            "claim_definition_version": COMMERCIALIZATION_CLAIM_DEFINITION_VERSION,
            "claim_text": COMMERCIALIZATION_CLAIM_TEXT,
            "reason": f"配置的 evidence_sections 未包含 {REVENUE_SECTION_ID}",
            "facts": [], "warnings": ["商业化 Observer 首版只实现 Frontier Labs 收入部分。"],
        }

    scope_suffix = ""
    if scope:
        scope_suffix = ":" + ":".join(str(scope.get(key, "")) for key in ("sector", "layer"))
    if not hasattr(products, "frontier_labs_revenue_evidence_bundle"):
        return {
            "status": "unavailable", "consumer": CONSUMER,
            "source_access": "data_products_only",
            "claim_id": COMMERCIALIZATION_CLAIM_ID,
            "claim_definition_version": COMMERCIALIZATION_CLAIM_DEFINITION_VERSION,
            "claim_text": COMMERCIALIZATION_CLAIM_TEXT,
            "reason": "DataProducts 未注册 Frontier Labs 收入证据包",
            "facts": [], "warnings": ["收入数据产品不可用；不影响生产化与应用扩散 Observer。"],
        }
    bundle = products.frontier_labs_revenue_evidence_bundle(
        as_of=as_of, snapshot_consumer=CONSUMER,
        snapshot_purpose=(f"{COMMERCIALIZATION_CLAIM_ID}:{claim_definition_version}"
                          f"{scope_suffix}"))
    return _build_packet(bundle, chart_dir=chart_dir, workflow_scope=scope,
                         claim_definition_version=claim_definition_version, top_n=top_n)


def _build_packet(bundle: dict[str, Any], *, chart_dir: str,
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

    packet: dict[str, Any] = {
        "status": "ok" if bundle.get("status") == "ok" else "unavailable",
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
    }
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
        "warnings": packet.get("warnings") or [],
        "manifest_id": (packet.get("manifest") or {}).get("snapshot_id"),
        "lineage_pointer": "manifest.snapshot_id / companies[].lineage",
        "rows_hash": packet.get("rows_hash"),
    }
    compact = json.dumps(essential, ensure_ascii=False, sort_keys=True, default=str)
    review_model = {**essential, "methodology_card": packet.get("methodology_card"),
                    "history_rows": packet.get("history_rows"),
                    "comparability": packet.get("comparability")}
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
        "首版只实现 `frontier_labs_revenue_scale_and_trend` 一个证据部分。"
        "**收入兑现方向已观察、留存与单位经济尚未验证**；"
        "不得仅凭收入增长宣称商业模式可持续或单位经济成立。",
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

    chart_lines = [item for item in (packet.get("visualization_descriptors") or [])]
    lines.extend(["## 四、收入水平与趋势", ""])
    if chart_lines:
        for item in chart_lines:
            lines.extend([
                f"![{item.get('title')}]({item.get('png_path')})", "",
                f"数据与复现：[sidecar]({item.get('sidecar_path')}) · "
                f"rows hash `{item.get('rows_hash')}`", "",
            ])
        lines.append("图中只连接同一可比 cell 的离散披露点；"
                     "线段是方向辅助线，不是未披露月份的估计值。")
    else:
        lines.append(f"未生成图表：{packet.get('visualization_warning', '没有可绘制的可比观察。')}")
    lines.append("")

    lines.extend(["## 五、历史观察表", "",
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
    lines.extend(["## 六、来源、口径与冲突", "",
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
        "## 七、指标公式、统计范围与数据缺口", "",
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
        "## 八、尚待建设的留存、单位经济与商业模式证据", "",
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
                         f"[CSV]({item.get('csv_path')}) · [JSON]({item.get('json_path')}) · "
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
