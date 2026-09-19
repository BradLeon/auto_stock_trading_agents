"""Independent L1 Evidence Observer for Frontier AI raw capability."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CLAIM_ID = "ai_frontier_raw_capability"
CLAIM_VERSION = "v1"
CLAIM_TEXT = "前沿生成式 AI 的原始能力边界是否持续外扩，并出现过去模型无法跨越的新任务门槛？"
CONSUMER = "evidence_observer"


def _fmt(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return "NA"


def _status_a(status: str) -> str:
    return {"confirmed_expansion": "已确认外扩", "provisional_expansion": "暂定外扩",
            "contracting": "收缩", "stable": "基本稳定", "insufficient_history": "历史不足",
            "non_comparable_version_change": "换版不可比"}.get(status, status)


def _status_b(status: str) -> str:
    return {"confirmed_crossing": "已确认跨越", "provisional_crossing": "暂定跨越",
            "not_crossed": "尚未跨越", "not_applicable": "不适用",
            "not_evaluated": "未评测"}.get(status, status)


def _release_label(row: dict[str, Any]) -> str:
    """Return a release-level label while retaining config in structured data."""
    return str(row.get("model_release_name") or row.get("model_name") or row.get("model_release_id") or "NA")


def observe_ai_raw_capability(*, periods=None, as_of=None, chart_dir: str = "",
                              workflow_scope: dict[str, Any] | None = None,
                              claim_definition_version: str = CLAIM_VERSION, products=None,
                              previous_matrix: dict[str, Any] | None = None) -> dict[str, Any]:
    if products is None:
        from ...data.products import get_platform_data_products
        products = get_platform_data_products()
    scope = dict(workflow_scope or {})
    if not hasattr(products, "frontier_ai_capability_evidence_bundle"):
        return {"status": "unavailable", "claim_id": CLAIM_ID, "claim_definition_version": claim_definition_version,
                "claim_text": CLAIM_TEXT, "facts": [], "warnings": ["raw capability DataProduct 未注册；不影响既有 Observer。"]}
    packet = products.frontier_ai_capability_evidence_bundle(as_of=as_of, previous_matrix=previous_matrix,
                                                              snapshot_consumer=CONSUMER,
                                                              snapshot_purpose=f"{CLAIM_ID}:{claim_definition_version}")
    packet = dict(packet, claim_id=CLAIM_ID, claim_definition_version=claim_definition_version, claim_text=CLAIM_TEXT,
                  workflow_scope=scope, evidence_sections=["raw_capability"])
    warnings = list(packet.get("warnings") or [])
    if packet.get("status") != "ok":
        warnings.append("当前没有通过质量门的 benchmark 数值；矩阵仍保留十一乘九 NA。")
    if chart_dir and packet.get("matrix"):
        from .raw_capability_visualization import render_raw_capability_charts
        rendered = render_raw_capability_charts(packet=packet, output_dir=chart_dir)
        packet["visualization_descriptors"] = rendered.get("descriptors", [])
        packet["table_descriptors"] = rendered.get("tables", [])
        packet["chart_font"] = {"font_path": rendered.get("font_path", ""), "glyph_status": rendered.get("glyph_status")}
        if rendered.get("visualization_warning"):
            warnings.append(rendered["visualization_warning"])
    packet["warnings"] = warnings
    compact_payload = {"claim_id": CLAIM_ID, "claim_definition_version": claim_definition_version,
                       "a": packet.get("a"), "b": packet.get("b"), "coverage": packet.get("coverage"),
                       "frontier_diagnostics": packet.get("frontier_diagnostics"),
                       "manifest_id": (packet.get("manifest") or {}).get("snapshot_id"),
                       "rows_hash": packet.get("rows_hash"), "lineage": packet.get("lineage"),
                       "freshness": packet.get("freshness"), "warnings": warnings}
    compact = json.dumps(compact_payload, ensure_ascii=False, sort_keys=True, default=str)
    packet["context"] = {"compact": compact, "compact_char_count": len(compact), "compact_budget": 12000,
                          "review_char_count": len(json.dumps(packet, ensure_ascii=False, default=str)),
                          "review_budget": 120000, "budget_status": "ok" if len(compact) <= 12000 else "over_budget",
                          "lineage_pointer": packet.get("lineage")}
    return packet


def render_ai_raw_capability_markdown(packet: dict[str, Any]) -> str:
    matrix = packet.get("matrix") or {}
    a = packet.get("a") or {}; b = packet.get("b") or {}
    lines = ["# L1 原始能力边界 Observer", "", f"**追踪命题：** {packet.get('claim_text', CLAIM_TEXT)}", ""]
    a_items = list(a.get("benchmarks") or []); b_items = list(b.get("benchmarks") or [])
    a_expanding = [x for x in a_items if x.get("status") in {"confirmed_expansion", "provisional_expansion"}]
    a_stable = [x for x in a_items if x.get("status") == "stable"]
    a_unknown = [x for x in a_items if x.get("status") in {"insufficient_history", "non_comparable_version_change"}]
    if a_expanding:
        evidence = "、".join(f"{x['benchmark_id']}（{_status_a(x['status'])}，{x.get('delta_pp', 'NA')}pp）" for x in a_expanding)
        a_text = f"A（前沿外扩）：已观察到 {evidence}；这表示能力边界在这些可比 benchmark 上向外移动。"
    elif a_stable:
        evidence = "、".join(x["benchmark_id"] for x in a_stable)
        suffix = f"；另有 {len(a_unknown)} 项因历史不足或换版暂不判断" if a_unknown else ""
        a_text = f"A（前沿外扩）：本期未观察到超过判定阈值的新外扩；{evidence} 的累计前沿基本稳定{suffix}。"
    elif a_unknown:
        a_text = "A（前沿外扩）：当前无法确认。部分 benchmark 缺少同一可比组的历史基线或发生换版；NA 不表示能力下降。"
    else:
        a_text = f"A（前沿外扩）：{_status_a(a.get('status', 'insufficient_history'))}。"
    b_cross = [x for x in b_items if x.get("status") in {"confirmed_crossing", "provisional_crossing"}]
    b_not = [x for x in b_items if x.get("status") == "not_crossed"]
    b_na = [x for x in b_items if x.get("status") == "not_applicable"]
    b_parts = []
    if b_cross:
        b_parts.append("已跨越当前已定义的最高层级：" + "、".join(
            f"{x['benchmark_id']}（{_status_b(x['status'])}，{(x.get('levels') or [{}])[-1].get('label', 'NA')}，模型 {_release_label(x.get('current') or {})}）"
            for x in b_cross))
    if b_not:
        b_parts.append("尚未跨越当前已定义层级：" + "、".join(
            f"{x['benchmark_id']}（{(x.get('levels') or [{}])[-1].get('label', 'NA')}）" for x in b_not))
    if b_na:
        b_parts.append("不适用：" + "、".join(x['benchmark_id'] for x in b_na))
    lines += ["## 先给结论", "", f"- {a_text}", f"- B（能力门槛）：{'；'.join(b_parts) if b_parts else '当前没有可报告的门槛结果。'}", "",
              "## 关键证据", "", f"- 数据状态：`{packet.get('status')}`；最新 score-as-of：`{(packet.get('freshness') or {}).get('latest_score_as_of') or 'NA'}`；统一矩阵覆盖 {matrix.get('coverage', {}).get('numeric', 0)}/{matrix.get('coverage', {}).get('total', 99)}。",
              f"- 统一规则：A 比较同一 comparability group 的累计 global frontier；缺置信区间只能暂定。B 分三级：多数任务解锁（95% 置信下界 > 50%）、人类基准门槛、经济可用门槛；后两级仅在 benchmark 预先定义且可观测时报告。", ""]
    lines += ["## 更新机制与覆盖滞后", "",
              "- **不是关键词查询。** 每日轮询已登记的公开 Git/CSV/JSON/README 载体，动态发现其中出现的 model id、版本和分数。当前旗舰由官方 release 发现或显式 override 决定；配置注册表只在官方 release 暂无覆盖时作为回退。benchmark 发布先后不会把旧模型误选成新旗舰，精确版本无结果时保留 `NA`。",
              "- **上游变更检测。** Git 使用 commit/blob 身份，HTTP 使用 ETag/Last-Modified，最终以 payload SHA-256 兜底；相同身份返回 `no_change`，新增或修订才追加新的 observation vintage。",
              "- **调度频率。** 模型/benchmark 发现和分数探测每日运行；最近 30 天的热观测每日检查，31–90 天每三天检查，成熟序列每周检查；官方实验室自报另有每日探测，每周做全量方法/来源审计。",
              "- **必须区分两种滞后。** 系统探测滞后由轮询频率决定（正常为不超过一个日周期，成熟源最长约一周）；benchmark 覆盖滞后则是“模型公开 release → benchmark 首次发布该精确版本结果”的时间差，只有当两端都提供可验证日期时才计算。当前多数公开结果表没有模型 release date，因此报告不填推测天数。", ""]
    # Review-facing matrix orientation is benchmark rows × model columns.  The
    # underlying product remains lab-indexed so selection and lineage are not
    # changed; this is only a presentation transpose.  Full benchmark labels
    # are deliberately used here (no internal ids or abbreviations).
    panel = list(matrix.get("by_lab", {}).values())
    benchmark_catalog = list(matrix.get("benchmarks") or [])
    model_headers = [((lab.get("model") or {}).get("model_name") or "NA") for lab in panel]
    lines += ["## 九家 Labs × 十一项 benchmark 当前矩阵", "",
              "| Benchmark | " + " | ".join(model_headers) + " |",
              "|---|" + "|".join(["---"] * len(model_headers)) + "|"]
    for index, method in enumerate(benchmark_catalog):
        row_cells = [((lab.get("cells") or [])[index] if index < len(lab.get("cells") or []) else {})
                     for lab in panel]
        numeric_scores = [float(cell["score"]) for cell in row_cells if cell.get("score") is not None]
        row_max = max(numeric_scores) if numeric_scores else None
        vals = []
        for cell in row_cells:
            if cell.get("score") is None:
                vals.append("NA")
            else:
                value = f"{float(cell['score']):.1f}%"
                if cell.get("release_match") == "latest_evaluated_lab_fallback":
                    value += "†"
                elif cell.get("release_match") == "latest_evaluated_event_fallback":
                    value += "‡"
                vals.append(f"**{value}**" if float(cell["score"]) == row_max else value)
        lines.append("| %s | %s |" % (method.get("label"), " | ".join(vals)))
    lines += ["", "- 矩阵列为本期各实验室当前旗舰模型；行名使用 benchmark 全称。无标记数值是当前旗舰精确 release 的成绩；`†` 表示同一可比组内该 Lab 最近一次可接受评测；`‡` 表示最近一次事件型评测（task set/harness 口径须单独核对）。两者都保留实际被评模型身份，绝不冒充当前旗舰。`NA` 表示连同 Lab 回退也没有可接受公开结果，不等于零分。来源选择遵循维护者/独立第三方 > 竞对报告 > 实验室自报。",
              "- **同一模型版本的配置归并：** `Max`、`High`、`xhigh` 等 reasoning effort 或 harness 配置不各占一列。在同一精确 release、同一 benchmark 和同一来源优先级内，矩阵展示公开观测到的最高分；被选中的原始配置、effort、harness 与来源仍保留在单元格 sidecar/JSON 中，可审计且不取平均。不同产品变体（例如 `GLM-5.3` 与 `GLM-5.3-Flash`）不做模糊合并。", ""]
    fallback_cells = [cell for lab in panel for cell in (lab.get("cells") or [])
                      if cell.get("release_match") in {"latest_evaluated_lab_fallback",
                                                       "latest_evaluated_event_fallback"}]
    if fallback_cells:
        lines += ["## 附录：`†/‡` 最近被评测模型明细", "",
                  "该表防止把旧 release 的分数冒充当前旗舰；主矩阵只为减少 NA 而显示其同 Lab 证据。", "",
                  "| 标记 | Benchmark | Lab | 当前旗舰 | 实际被评模型 | score-as-of |",
                  "|---|---|---|---|---|---|"]
        for cell in fallback_cells:
            marker = "‡" if cell.get("release_match") == "latest_evaluated_event_fallback" else "†"
            lines.append(f"| {marker} | {cell.get('benchmark')} | {cell.get('lab')} | {cell.get('panel_model_name')} | "
                         f"{_release_label(cell)} | {cell.get('score_as_of') or 'NA'} |")
        lines.append("")
    event_rows = list(matrix.get("event_observations") or [])
    if event_rows:
        lines += ["## 事件型评测证据账本", "",
                  "AutomationBench、OSWorld 和 SpreadsheetBench 2 等可能同时存在不同 task set、harness、grader 或评分语义。下表仅展示事件证据，不把不同可比组混入统一矩阵。", "",
                  "| Benchmark | 模型 | 分数 | 来源 | 可比组 | 方法/设置 |", "|---|---|---:|---|---|---|"]
        for row in event_rows:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                row.get("benchmark", row.get("benchmark_id", "NA")),
                row.get("model_name", row.get("model_id", "NA")),
                "NA" if row.get("score") is None else f"{float(row['score']):.1f}%",
                row.get("source_type", "NA"), row.get("comparability_group", "NA"),
                row.get("harness") or row.get("task_set") or "NA"))
        lines.append("")
    lines += ["", "## A 决策表", "", "| Benchmark | 状态 | 当前 frontier | 先前 frontier | 增量/说明 |", "|---|---|---|---|---|"]
    for item in a_items:
        cur, prev = item.get("current") or {}, item.get("previous") or {}
        delta = f"{float(item['delta_pp']):.1f}pp" if item.get("delta_pp") is not None else "NA"
        lines.append(f"| {item.get('benchmark_id')} | {_status_a(item.get('status', ''))} | {_release_label(cur)} {('%.1f%%' % cur['score']) if cur.get('score') is not None else 'NA'} | {_release_label(prev)} {('%.1f%%' % prev['score']) if prev.get('score') is not None else 'NA'} | {delta} |")
    lines += ["", "## B 门槛表", "",
              "三级门槛按要求递进：多数任务解锁 → 人类基准 → 经济可用。表中只出现该 benchmark 已预定义且可观测的层级；没有可靠阈值时不臆测。", "",
              "| Benchmark | 已定义最高层级 | 状态 | 模型/分数 | 判定证据 |", "|---|---|---|---|---|"]
    for item in b_items:
        cur = item.get("current") or {}
        levels = list(item.get("levels") or [])
        level = levels[-1] if levels else {}
        evidence = (f"阈值 {float(level['threshold_pct']):.1f}%；95% LCB "
                    f"{float(level['confidence_low']):.1f}%" if level.get("confidence_low") is not None
                    else ("仅有点估计，未提供 95% 置信区间" if levels else item.get("reason", "NA")))
        lines.append(f"| {item.get('benchmark_id')} | {level.get('label', '不适用')} | {_status_b(item.get('status', ''))} | {_release_label(cur)} {('%.1f%%' % cur['score']) if cur.get('score') is not None else 'NA'} | {evidence} |")
    lines += ["", "## Benchmark 方法卡", ""]
    candidate_rows = list(matrix.get("candidate_observations") or [])
    for method in matrix.get("benchmarks") or []:
        benchmark_id = method.get("benchmark_id")
        source_rows = [row for row in candidate_rows if row.get("benchmark_id") == benchmark_id]
        transports = sorted({str(row.get("source_transport") or "unknown") for row in source_rows})
        scopes = sorted({str(row.get("measurement_scope") or "unknown") for row in source_rows})
        source_text = ", ".join(transports) if transports else "NA（当前无有效成绩）"
        scope_text = ", ".join(scopes) if scopes else "NA"
        lines += [f"### {method.get('label')}", f"- 评估方向：{method.get('direction')}；值域：{method.get('value_range', [0, 100])[0]}–{method.get('value_range', [0, 100])[1]}%。",
                  f"- 分数含义：{method.get('score_semantics')}；可比组：`{method.get('comparability_group')}`；B：{'适用' if method.get('b_eligible') else '仅报告 A'}。",
                  f"- 测量范围：`{scope_text}`；本期来源载体：`{source_text}`。NA 表示尚无该精确模型版本的可接受公开观测，不等于零分。", ""]
    diagnostics = packet.get("frontier_diagnostics") or {}
    divergences = [
        (benchmark, item) for benchmark, item in (diagnostics.get("divergence") or {}).items()
        if item.get("score_gap_pp") not in (None, 0, 0.0)
    ]
    if diagnostics:
        lines += ["## Panel / global frontier 注释", "",
                  "A 命题使用已治理成绩中的 global frontier；九家 Labs × 十一项 benchmark 表是固定展示面板。两者不一致时不做静默替换。"]
        if divergences:
            lines.append("- 当前发现面板与 global frontier 存在差异：" + "、".join(
                f"{benchmark}（{float(item['score_gap_pp']):+.1f}pp）" for benchmark, item in divergences))
        else:
            lines.append("- 当前没有可量化的面板/global frontier 差异，或覆盖不足以比较。")
        lines.append("")
    if packet.get("visualization_descriptors"):
        lines += ["## 图表", ""]
        for descriptor in packet["visualization_descriptors"]:
            png = Path(descriptor["png_path"])
            # Reports are written beside the renderer's asset directory.  Keep
            # the link relative and include that directory so Markdown
            # viewers do not render a broken image when assets are separated
            # from the report file.
            asset_link = f"{png.parent.name}/{png.name}" if png.parent.name else png.name
            lines += [f"![{descriptor.get('title', 'raw capability chart')}]({asset_link})", "", f"图表解读：{descriptor.get('metric_definition', '')}", ""]
    lines += ["## 口径与限制", ""] + [f"- {item}" for item in packet.get("limitations") or []]
    lines += ["", f"rows hash：`{packet.get('rows_hash', '')}`"]
    return "\n".join(lines)
