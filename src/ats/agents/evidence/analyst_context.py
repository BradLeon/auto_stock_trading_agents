"""Compact, governed L1 Evidence context for downstream Analyst LLMs.

The Markdown report remains the human review artifact.  This module emits the
same governed conclusions as a deliberately small machine contract: values,
trends, interpretation boundaries, missingness and lineage pointers.  It does
not flatten chart pixels or create a cross-Observer score.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


SCHEMA_VERSION = "l1_analyst_context/v1"


def _number(value: Any, digits: int = 2) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric.is_integer():
        return int(numeric)
    return round(numeric, digits)


def _missing_reason(status: Any, value: Any = None) -> str | None:
    if value is not None:
        return None
    normalized = str(status or "unavailable")
    return {
        "insufficient_history": "可比历史不足，不能形成趋势判断；不按零处理。",
        "unavailable": "当前没有通过质量门的公开观测；不按零处理。",
        "not_evaluated": "公开来源尚未覆盖该模型或 benchmark；不按零处理。",
        "not_applicable": "指标语义不支持该项判断。",
    }.get(normalized, f"当前状态为 {normalized}，没有可报告数值；不按零处理。")


def _production_context(packet: dict[str, Any]) -> dict[str, Any]:
    semantics = {
        "enterprise_breadth": {
            "supports": ["美国雇主企业自报 AI 使用广度及其方向"],
            "does_not_support": ["员工实际使用率", "企业付费采购率", "任务自动化率"],
        },
        "worker_persistence": {
            "supports": ["美国就业人口最近一周在工作中使用生成式 AI 的群体趋势"],
            "does_not_support": ["同一员工持续留存", "企业采购", "生产率或岗位替代"],
        },
        "paid_procurement": {
            "supports": ["Ramp 网络企业真实 AI 付款广度与支出强度的方向"],
            "does_not_support": ["全美企业采用率", "免费或非 Ramp 渠道使用", "员工使用率"],
        },
        "task_production": {
            "supports": ["Claude 1P API 已发布任务流量中生产化代理的结构变化"],
            "does_not_support": ["全经济体自动化率", "员工采用率", "生产率、ROI 或岗位替代"],
        },
    }
    evidence = []
    for row in packet.get("axis_overview") or []:
        axis_id = str(row.get("axis_id") or "unknown_axis")
        status = str(row.get("trend_status") or "unavailable")
        value = _number(row.get("headline_value"))
        meaning = semantics.get(axis_id, {
            "supports": ["该来源自身统计范围内的方向"],
            "does_not_support": ["跨来源统一采用率"],
        })
        evidence.append({
            "evidence_id": axis_id,
            "label": row.get("axis_label"),
            "source_id": row.get("source_id"),
            "latest": {"period": row.get("period"), "value": value,
                       "unit": row.get("headline_unit") or "percent"},
            "trend": {
                "status": status,
                "net_change_pp": _number(row.get("trend_net_change_pp")),
                "slope_pp_per_period": _number(row.get("trend_slope_pp_per_period"), 4),
                "comparable_period_count": int(row.get("comparable_period_count") or 0),
                "explanation": list(row.get("trend_explanation") or []),
            },
            "scope": {"statistical_unit": row.get("statistical_unit"),
                      "denominator": row.get("denominator"),
                      "geography": row.get("geography")},
            "supports": meaning["supports"],
            "does_not_support": meaning["does_not_support"],
            "missing_reason": _missing_reason(status, value),
            "uncertainty": {
                "interval_available": False,
                "interpretation": "方向来自 source-native 可比时序；不得解释为跨来源置信区间或因果效应。",
            },
            "lineage": {"observation_ids": list(row.get("input_observation_ids") or [])},
            "cross_evidence_numeric_merge_allowed": False,
        })
    return {
        "claim_id": packet.get("claim_id"),
        "claim_definition_version": packet.get("claim_definition_version"),
        "question": packet.get("claim_text"),
        "status": packet.get("overall_status") or packet.get("status"),
        "conclusion": packet.get("overall_interpretation"),
        "supports": ["企业自报采用、员工工作使用、企业付费采购、任务生产化四个独立维度的方向判断"],
        "does_not_support": ["把四轴平均为统一渗透率", "因果生产率", "ROI", "交易信号"],
        "evidence": evidence,
        "quality": {"warnings": list(packet.get("warnings") or []),
                    "conflicts": list(packet.get("corroboration_and_conflicts") or [])},
        "lineage": {"manifest_id": (packet.get("manifest") or {}).get("snapshot_id"),
                    "bundle_content_hash": packet.get("bundle_content_hash")},
        "cross_observer_merge_allowed": False,
    }


def _commercialization_context(packet: dict[str, Any]) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    for item in packet.get("companies") or []:
        record = ((item.get("headline") or {}).get("record") or {})
        trend = item.get("trend") or {}
        value = _number(record.get("value_usd_bn"))
        status = str(trend.get("status") or "unavailable")
        evidence.append({
            "evidence_id": f"frontier_lab_revenue:{item.get('entity_id') or item.get('company')}",
            "label": item.get("company"),
            "latest": {"period": record.get("period"), "value": value, "unit": "USD_bn",
                       "metric_id": record.get("metric_id"),
                       "observation_identity": record.get("observation_identity")},
            "trend": {"status": status,
                      "net_change_usd_bn": _number(trend.get("net_change_usd_bn")),
                      "net_change_rate": _number(trend.get("net_change_rate"), 4),
                      "comparable_point_count": int(trend.get("comparable_point_count") or 0),
                      "span_days": int(trend.get("span_days") or 0)},
            "supports": ["该公司在明确计量口径和观察身份下的收入水平与可比趋势"],
            "does_not_support": ["审计收入（除非 observation_identity 明确）", "留存", "毛利率", "单位经济"],
            "missing_reason": _missing_reason(status, value),
            "uncertainty": {"interval_available": False,
                            "interpretation": "估算、媒体披露和公司披露身份保留，不能互相升级。"},
            "lineage": {"observation_ids": list(trend.get("input_observation_ids") or [])},
            "cross_evidence_numeric_merge_allowed": False,
        })
    route = packet.get("openrouter") or {}
    route_facts = route.get("facts") or {}
    if route or any(str(item.get("section_id")) == "openrouter_token_economy"
                    for item in packet.get("evidence_sections") or []):
        route_trend = route_facts.get("trend") or {}
        route_status = route.get("directional_status") or route_trend.get("status") or route.get("status")
        route_value = _number(route_facts.get("latest_total_tokens"), 0)
        concentration = route_facts.get("concentration") or {}
        evidence.append({
            "evidence_id": "openrouter_token_economy",
            "label": "OpenRouter 公共路由 token 与作者竞争",
            "latest": {"period": route_facts.get("latest_complete_week"),
                       "value": route_value, "unit": "tokens"},
            "trend": {"status": route_status,
                      "change_rate": _number(route_trend.get("change_rate"), 4),
                      "comparison": route_trend.get("comparison")},
            "concentration": {"top3_share": _number(concentration.get("top3_share"), 4),
                              "top5_share": _number(concentration.get("top5_share"), 4),
                              "hhi": _number(concentration.get("hhi"), 4)},
            "supports": ["OpenRouter 公共路由内 token 规模与模型作者竞争格局"],
            "does_not_support": ["全球 token 总量", "模型公司收入", "request share", "付费意愿"],
            "missing_reason": _missing_reason(route_status, route_value),
            "uncertainty": {"interval_available": False,
                            "interpretation": "平台流量 proxy；免费路由和 tokenizer 差异仍在统计范围内。"},
            "cross_evidence_numeric_merge_allowed": False,
        })
    return {
        "claim_id": packet.get("claim_id"),
        "claim_definition_version": packet.get("claim_definition_version"),
        "question": packet.get("claim_text"),
        "status": packet.get("overall_status") or packet.get("status"),
        "conclusion": packet.get("overall_interpretation") or packet.get("reason"),
        "supports": ["Frontier AI Labs 收入兑现趋势", "公共 API 路由 token 规模及竞争格局"],
        "does_not_support": ["可持续商业模式已经成立", "客户留存", "单位经济", "把 token 换算为收入"],
        "evidence": evidence,
        "coverage": packet.get("coverage") or {},
        "quality": {"warnings": list(packet.get("warnings") or []),
                    "source_conflicts": list(packet.get("source_conflicts") or [])},
        "lineage": {"manifest_id": (packet.get("manifest") or {}).get("snapshot_id"),
                    "rows_hash": packet.get("rows_hash")},
        "cross_observer_merge_allowed": False,
    }


def _raw_capability_context(packet: dict[str, Any]) -> dict[str, Any]:
    matrix = packet.get("matrix") or {}
    a = packet.get("a") or {}
    b = packet.get("b") or {}
    a_items = []
    for item in a.get("benchmarks") or []:
        current = item.get("current") or {}
        previous = item.get("previous") or {}
        a_items.append({
            "benchmark_id": item.get("benchmark_id"), "status": item.get("status"),
            "current": {"model": current.get("model_release_name") or current.get("model_name"),
                        "score_pct": _number(current.get("score")),
                        "confidence_low_pct": _number(current.get("confidence_low")),
                        "score_as_of": current.get("score_as_of")},
            "previous": {"model": previous.get("model_release_name") or previous.get("model_name"),
                         "score_pct": _number(previous.get("score")),
                         "confidence_high_pct": _number(previous.get("confidence_high")),
                         "score_as_of": previous.get("score_as_of")},
            "delta_pp": _number(item.get("delta_pp")),
            "lcb_delta_pp": _number(item.get("lcb_delta_pp")),
            "missing_reason": _missing_reason(item.get("status"), current.get("score")),
        })
    b_items = []
    for item in b.get("benchmarks") or []:
        current = item.get("current") or {}
        levels = [{"level_id": level.get("level_id"), "label": level.get("label"),
                   "threshold_pct": _number(level.get("threshold_pct")),
                   "score_pct": _number(level.get("score")),
                   "confidence_low_pct": _number(level.get("confidence_low")),
                   "status": level.get("status")}
                  for level in item.get("levels") or []]
        b_items.append({
            "benchmark_id": item.get("benchmark_id"), "status": item.get("status"),
            "model": current.get("model_release_name") or current.get("model_name"),
            "score_pct": _number(current.get("score")), "levels": levels,
            "missing_reason": _missing_reason(item.get("status"), current.get("score")),
        })
    coverage = matrix.get("coverage") or packet.get("coverage") or {}
    return {
        "claim_id": packet.get("claim_id"),
        "claim_definition_version": packet.get("claim_definition_version"),
        "question": packet.get("claim_text"),
        "status": packet.get("status"),
        "conclusion": {
            "frontier_expansion": a.get("status"),
            "capability_thresholds": b.get("status"),
        },
        "supports": ["固定 benchmark 口径下的能力前沿变化", "预定义且可观测能力门槛的跨越"],
        "does_not_support": ["综合智能总分", "TAM", "经济价值", "跨 benchmark 平均能力"],
        "evidence": [
            {"evidence_id": "frontier_expansion_by_benchmark",
             "status": a.get("status"), "values": a_items,
             "supports": ["同一 comparability group 内 global frontier 的可比变化"],
             "does_not_support": ["跨 benchmark 总分或不同 harness 的直接比较"],
             "cross_evidence_numeric_merge_allowed": False},
            {"evidence_id": "capability_thresholds_by_benchmark",
             "status": b.get("status"), "framework": list(b.get("framework") or []),
             "values": b_items,
             "supports": ["预定义多数任务、人类基准或经济可用门槛的逐项判断"],
             "does_not_support": ["未预定义门槛的事后主观判定"],
             "cross_evidence_numeric_merge_allowed": False},
        ],
        "coverage": {"numeric": int(coverage.get("numeric") or 0),
                    "total": int(coverage.get("total") or 0),
                    "ratio": _number(coverage.get("ratio"), 4)},
        "uncertainty": {
            "rule": "A 仅在同一 comparability group 比较；B 仅在 95% 置信下界越过预定义阈值时确认。",
            "point_estimate_only_is_provisional": True,
        },
        "missingness": {"na_means_zero": False,
                        "fallback_scores_are_current_flagship": False,
                        "reason": "NA 表示尚无可接受公开观测；†/‡ 回退证据不得冒充当前旗舰。"},
        "quality": {"warnings": list(packet.get("warnings") or []),
                    "limitations": list(packet.get("limitations") or [])},
        "lineage": {"manifest_id": (packet.get("manifest") or {}).get("snapshot_id"),
                    "rows_hash": packet.get("rows_hash"),
                    "latest_score_as_of": (packet.get("freshness") or {}).get("latest_score_as_of")},
        "cross_observer_merge_allowed": False,
    }


def _generic_context(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": packet.get("claim_id"),
        "claim_definition_version": packet.get("claim_definition_version"),
        "question": packet.get("claim_text"),
        "status": packet.get("overall_status") or packet.get("status"),
        "conclusion": packet.get("overall_interpretation") or packet.get("reason"),
        "supports": [],
        "does_not_support": ["未在结构化契约中定义的推论"],
        "evidence": [],
        "cross_observer_merge_allowed": False,
    }


def build_layer_analyst_context(result: dict[str, Any], *, human_report: str | None = None) -> dict[str, Any]:
    """Build the canonical compact context without re-deriving governed facts."""
    builders = {
        "ai_core_production_workflow_penetration": _production_context,
        "ai_frontier_labs_commercialization": _commercialization_context,
        "ai_frontier_raw_capability": _raw_capability_context,
    }
    observers = []
    for packet in result.get("packets") or []:
        builder = builders.get(str(packet.get("claim_id") or ""), _generic_context)
        observers.append(builder(packet))
    context: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "context_role": "primary_input_for_analyst_llm",
        "human_report": human_report,
        "workflow_scope": result.get("workflow_scope") or {},
        "run_status": result.get("status"),
        "overall": {
            "observer_count": len(observers),
            "claim_ids": [item.get("claim_id") for item in observers],
            "interpretation": "三个 Observer 独立回答生产化扩散、商业化能力和原始能力边界；并列阅读，不合成为单一分数。",
        },
        "merge_policy": {
            "cross_observer_merge_allowed": False,
            "numeric_aggregation": "prohibited",
            "directional_synthesis": "allowed_only_with_explicit_scope_and_no_causal_claim",
            "reason": "Observer、来源和 benchmark 的统计主体、分母与计量语义不同。",
        },
        "observers": observers,
    }
    canonical = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    context["context_hash_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    context["context_char_count"] = len(canonical)
    return context


def analyst_context_paths(output: str | Path) -> tuple[Path, Path]:
    report = Path(output)
    stem = report.stem
    context_stem = f"{stem[:-7]}_CONTEXT" if stem.endswith("_REPORT") else f"{stem}_CONTEXT"
    return report.with_name(f"{context_stem}.json"), report.with_name(f"{context_stem}.yaml")


def write_layer_analyst_context(result: dict[str, Any], output: str | Path) -> list[Path]:
    """Write canonical JSON plus semantically identical YAML beside the report."""
    report = Path(output)
    json_path, yaml_path = analyst_context_paths(report)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        human_report = str(report.relative_to(json_path.parent))
    except ValueError:
        human_report = str(report)
    context = build_layer_analyst_context(result, human_report=human_report)
    json_path.write_text(json.dumps(context, ensure_ascii=False, indent=2, sort_keys=False, default=str) + "\n",
                         encoding="utf-8")
    yaml_path.write_text(yaml.safe_dump(context, allow_unicode=True, sort_keys=False, width=120),
                         encoding="utf-8")
    return [json_path, yaml_path]


def load_layer_analyst_context(path: str | Path) -> dict[str, Any]:
    """Load and validate the primary Analyst input, including its content hash."""
    source = Path(path)
    raw = source.read_text(encoding="utf-8")
    payload = yaml.safe_load(raw) if source.suffix.lower() in {".yaml", ".yml"} else json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported Analyst context schema: {payload.get('schema_version') if isinstance(payload, dict) else type(payload).__name__}")
    if (payload.get("merge_policy") or {}).get("cross_observer_merge_allowed") is not False:
        raise ValueError("Analyst context must explicitly prohibit cross-Observer numeric merging")
    expected = str(payload.get("context_hash_sha256") or "")
    core = {key: value for key, value in payload.items()
            if key not in {"context_hash_sha256", "context_char_count"}}
    canonical = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if not expected or actual != expected:
        raise ValueError("Analyst context hash mismatch")
    return payload


def render_analyst_llm_input(context: dict[str, Any]) -> str:
    """Return the compact JSON that should be placed ahead of optional Markdown."""
    if context.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid Analyst context")
    return json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
