"""Governed FactSet Earnings Insight consumer views.

This module intentionally contains no HTTP acquisition, local-folder fallback,
or PDF parsing. The registered FactSet ingest pipeline owns those operations;
Macro and Sector only read released DataProducts through the functions below.
"""

from __future__ import annotations

import logging

from ..schemas.macro_strategy import EarningsBackdrop

log = logging.getLogger("ats.data.factset")
name = "factset"


def _close_owned_products(products) -> None:
    for repository in (getattr(products, "_structured_repository", None),
                       getattr(products, "_unstructured_repository", None)):
        close = getattr(repository, "close", None)
        if close:
            close()


def _platform_snapshot(products=None):
    owned = products is None
    if products is None:
        from .products import get_platform_data_products
        products = get_platform_data_products()
    try:
        return products.earnings_insight_snapshot()
    finally:
        if owned:
            _close_owned_products(products)


def _platform_analysis_packet(products=None):
    owned = products is None
    if products is None:
        from .products import get_platform_data_products
        products = get_platform_data_products()
    try:
        return products.earnings_insight_analysis_packet()
    finally:
        if owned:
            _close_owned_products(products)


_GROUP_LABELS = {
    "reporting_progress": "财报披露进度",
    "earnings_revenue_surprises": "盈利和营收超预期情况",
    "earnings_revenue_growth": "盈利和营收增长",
    "profit_margin": "利润率",
    "company_guidance": "公司指引",
    "estimate_revision_breadth": "盈利预测上调范围",
    "valuation": "市盈率",
    "ratings_and_target_price": "分析师评级和目标价",
    "other": "其他已发布指标",
}


def _display_value(observation) -> str:
    value = observation.value
    if observation.unit == "ratio":
        return f"{value * 100:.1f}%"
    if observation.unit == "count":
        return f"{value:g} 家/个"
    if observation.unit == "multiple":
        return f"{value:g} 倍"
    return f"{value:g} {observation.unit}"


def render_macro_analysis_packet(packet) -> str:
    """Render every released index metric plus bounded page-cited prose."""
    if not packet.report.version_id:
        return "FactSet 分析材料不可用：" + packet.status.state
    lines = [
        f"报告日期: {packet.report.report_date}",
        f"数据状态: {packet.status.state}; freshness={packet.status.freshness}; "
        f"age_days={packet.status.age_days}",
        f"结构化指标: {packet.observation_count} 项（以下全部提供，不得只挑少数复述）",
    ]
    if packet.status.warnings:
        lines.append("质量警告: " + "; ".join(packet.status.warnings))
    for group_name, observations in packet.observation_groups.items():
        lines.append(f"\n### {_GROUP_LABELS.get(group_name, group_name)}")
        if not observations:
            lines.append("- 无已发布数据")
            continue
        for item in observations:
            pages = sorted({anchor.page_number for anchor in item.evidence
                            if anchor.page_number})
            page_ref = f"；原始证据页 {','.join(map(str, pages))}" if pages else ""
            state = (f"；口径 {item.estimate_state}"
                     if item.estimate_state != "not_applicable" else "")
            lines.append(
                f"- `{item.metric_id}` = {_display_value(item)}；期间 {item.period}"
                f"{state}{page_ref}；数据编号 `{item.observation_id}`")
    if packet.diagnostics:
        lines += ["", "### 程序直接计算的关系（非模型推断）"]
        for item in packet.diagnostics:
            unit = {"percentage_point": "个百分点", "percent": "%",
                    "ratio": "倍", "count": "家"}.get(item.unit, item.unit)
            lines.append(
                f"- `{item.diagnostic_id}` {item.label} = {item.value:g}{unit}；"
                f"输入数据编号 {', '.join(f'`{value}`' for value in item.input_observation_ids)}")
    lines += ["", "### 报告正文证据（最多六段；仅作分析素材）"]
    if not packet.narrative_evidence:
        lines.append("- 未找到符合预设分析问题的正文段落；不得自行补充。")
    else:
        for item in packet.narrative_evidence:
            lines.append(
                f"- **{item.topic_label}** [第 {item.page_number} 页，"
                f"{item.section_title or '未标章节'}]：{item.text}")
    lines += ["", "分析纪律：结构化数字是数据事实；对增长质量、集中度、驱动因素、"
              "估值和市场预期的文字属于模型解释。两者必须明确区分。"]
    return "\n".join(lines)


def _platform_macro_material(packet) -> tuple[str, str, EarningsBackdrop, object]:
    from .products import to_earnings_backdrop

    backdrop = to_earnings_backdrop(packet)
    text = render_macro_analysis_packet(packet)
    if backdrop.degraded and not packet.report.version_id:
        text = ""
    return text, f"factset:{packet.report.version_id or packet.status.state}", backdrop, packet


def fetch_macro_material(*, products=None):
    """Read Macro's released FactSet packet without acquisition or fallback."""
    from .rollout_modes import read_mode

    mode = read_mode("macro_factset")
    if mode == "off":
        return "", "disabled", None, None
    if mode != "platform":
        log.warning("factset Macro consumer mode %s has no legacy route", mode)
        return "", f"factset:{mode}", None, None
    try:
        return _platform_macro_material(_platform_analysis_packet(products))
    except Exception as exc:
        log.warning("factset: platform Macro analysis material unavailable: %s", exc)
        return "", "factset:unavailable", None, None


def fetch_macro_context(*, products=None) -> tuple[str, str, EarningsBackdrop | None]:
    """Compatibility view for Macro callers; DataProducts is the sole source."""
    text, source, backdrop, _packet = fetch_macro_material(products=products)
    return text, source, backdrop


def _render_sector_snapshot(snapshot) -> str:
    if not snapshot.sectors:
        return ""
    lines = [
        "## FactSet GICS 行业矩阵（top-down 市场背景）",
        "> 仅用于行业盈利/估值背景；不是 AI Hardware L1-L8、个股基本面或 Chain 独立证据。",
        f"> 报告日期 {snapshot.report.report_date}; 状态 {snapshot.status.state}; "
        f"freshness={snapshot.status.freshness}",
    ]
    if snapshot.status.warnings:
        lines.append("> 质量警告: " + "; ".join(snapshot.status.warnings))
    for entity, periods in sorted(snapshot.sectors.items()):
        readings = []
        for period, metrics in sorted(periods.items()):
            for metric, observation in sorted(metrics.items()):
                readings.append(
                    f"{period}/{metric}={observation.value:g} {observation.unit}"
                    f"[{observation.estimate_state}]")
        lines.append(f"- {entity}: " + "; ".join(readings))
    return "\n".join(lines)


def fetch_sector_context(*, products=None) -> str:
    """Return a released GICS overlay only; never acquire or parse a PDF."""
    return fetch_sector_material(products=products)["text"]


def fetch_sector_material(*, products=None) -> dict:
    """Return final-synthesis context plus an explicit availability reason."""
    from .rollout_modes import read_mode

    mode = read_mode("sector_factset")
    if mode == "off":
        return {"text": "", "mode": mode, "state": "unavailable",
                "reason": "FactSet 行业数据消费者已关闭。", "report_date": "",
                "version_id": "", "freshness": "unavailable"}
    if mode == "legacy":
        return {"text": "", "mode": mode, "state": "unavailable",
                "reason": "旧读取路径没有可供最终对照使用的正式十一行业数据。",
                "report_date": "", "version_id": "", "freshness": "unavailable"}
    try:
        snapshot = _platform_snapshot(products)
        rendered = _render_sector_snapshot(snapshot)
    except Exception as exc:
        log.warning("factset: platform Sector product unavailable: %s", exc)
        return {"text": "", "mode": mode, "state": "unavailable",
                "reason": f"读取本地 FactSet 行业数据失败：{exc}",
                "report_date": "", "version_id": "", "freshness": "unavailable"}
    base = {
        "mode": mode, "report_date": str(snapshot.report.report_date or ""),
        "version_id": snapshot.report.version_id,
        "freshness": snapshot.status.freshness,
    }
    if mode == "shadow":
        log.info("factset Sector shadow: version=%s sectors=%d status=%s",
                 snapshot.report.version_id, len(snapshot.sectors), snapshot.status.state)
        return {**base, "text": "", "state": "shadow",
                "reason": "FactSet 十一行业数据仍处于影子验证阶段，未作为正式分析输入。"}
    if not rendered:
        reason = ("所选报告尚未发布可用的十一行业分区。"
                  if not snapshot.sectors else "FactSet 十一行业材料为空。")
        return {**base, "text": "", "state": snapshot.status.sector_release.state,
                "reason": reason}
    reason = ""
    if snapshot.status.freshness == "stale":
        reason = f"FactSet 行业报告已过期，原报告日期为 {snapshot.report.report_date}。"
    return {**base, "text": rendered, "state": snapshot.status.sector_release.state,
            "reason": reason}
