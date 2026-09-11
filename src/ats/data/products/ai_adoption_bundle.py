"""Three-axis governed evidence bundle for AI production diffusion.

The bundle deliberately preserves source-native grains and periods.  It offers
directional corroboration, never a synthetic cross-source penetration score.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

BUNDLE_VERSION = "ai_adoption_evidence_bundle/v2"
TREND_RULE_VERSION = "ai_adoption_axis_trend/v2"
OVERALL_RULE_VERSION = "ai_adoption_overall_status/v2"
COMPARABILITY_VERSION = "ai_adoption_comparability/v1"
MIN_HISTORY = 3
TOLERANCE_PP = 0.5

AXIS_DEFINITIONS = {
    "enterprise_breadth": {
        "label": "企业采用广度", "dataset_id": "ai_enterprise_adoption_us",
        "statistical_unit": "US employer business",
        "denominator": "in-scope employer businesses",
        "geography": "United States", "technology_scope": "AI in any business function",
        "reference_period": "two-week survey reference window", "frequency": "biweekly",
    },
    "worker_persistence": {
        "label": "员工持续使用", "dataset_id": "ai_worker_adoption_us",
        "statistical_unit": "US employed adult age 18-64",
        "denominator": "nationally representative employed adults age 18-64",
        "geography": "United States", "technology_scope": "self-reported generative AI use for work",
        "reference_period": "quarter", "frequency": "quarterly",
    },
    "task_production": {
        "label": "任务生产化", "dataset_id": "ai_work_adoption",
        "statistical_unit": "published Claude 1P API occupation/task cell",
        "denominator": "visible published cells or 1P API traffic, depending on metric",
        "geography": "Global", "technology_scope": "Claude first-party API",
        "reference_period": "calendar month", "frequency": "release event / monthly cells",
    },
}


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _row_time(rows: list[dict], key: str) -> str | None:
    values = sorted(str(row.get(key)) for row in rows if row.get(key))
    return values[-1] if values else None


def _age_days(value: str | None, as_of: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (as_of - parsed.astimezone(as_of.tzinfo)).total_seconds() / 86400)
    except ValueError:
        return None


def _period_sort_key(value: str) -> tuple:
    """Sort source-native numeric waves and calendar periods chronologically."""
    text = str(value or "")
    if text.isdigit():
        return (0, int(text))
    wave = re.fullmatch(r"wave[-_ ]?(\d+)", text, re.IGNORECASE)
    if wave:
        return (1, int(wave.group(1)))
    quarter = re.fullmatch(r"(\d{4})-Q([1-4])", text, re.IGNORECASE)
    if quarter:
        return (2, int(quarter.group(1)), int(quarter.group(2)))
    month = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", text)
    if month:
        return (3, *(int(part) if part else 0 for part in month.groups()))
    return (9, text)


def _trend(values: list[tuple[str, float, str]], *, regime_count: int = 1,
           tolerance: float = TOLERANCE_PP) -> dict[str, Any]:
    """Classify the medium-term direction, without demanding monotonicity."""
    inputs = [item[2] for item in values if item[2]]
    result = {"status": "insufficient_history", "rule_version": TREND_RULE_VERSION,
              "minimum_history": MIN_HISTORY, "tolerance_pp": tolerance,
              "periods": [item[0] for item in values], "input_observation_ids": inputs,
              "steps": []}
    if regime_count != 1:
        result["steps"].append("methodology_regime_count != 1")
        return result
    if len(values) < MIN_HISTORY:
        result["steps"].append(f"comparable_period_count={len(values)} < {MIN_HISTORY}")
        return result
    changes = [values[index][1] - values[index - 1][1] for index in range(1, len(values))]
    signs = [1 if change > tolerance else -1 if change < -tolerance else 0 for change in changes]
    count = len(values)
    x_mean = (count - 1) / 2
    y_mean = sum(item[1] for item in values) / count
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    slope = (sum((index - x_mean) * (item[1] - y_mean)
                 for index, item in enumerate(values)) / denominator if denominator else 0.0)
    net_change = values[-1][1] - values[0][1]
    material = [sign for sign in signs if sign]
    endpoint_direction = 1 if net_change > tolerance else -1 if net_change < -tolerance else 0
    direction_consistency = (
        sum(sign == endpoint_direction for sign in material) / len(material)
        if material and endpoint_direction else 0.0)
    projected_slope_change = slope * (count - 1)
    if abs(net_change) <= tolerance and abs(projected_slope_change) <= tolerance:
        status = "stable"
    elif (endpoint_direction > 0 and slope > 0 and direction_consistency >= 0.6):
        status = "expanding"
    elif (endpoint_direction < 0 and slope < 0 and direction_consistency >= 0.6):
        status = "contracting"
    else:
        status = "mixed"
    result.update(status=status, changes_pp=changes, start_value=values[0][1],
                  end_value=values[-1][1], net_change_pp=net_change,
                  linear_slope_pp_per_period=slope,
                  direction_consistency=direction_consistency,
                  material_change_count=len(material))
    rounded_changes = [round(change, 3) for change in changes]
    result["steps"].extend([
        f"按来源原生期间排序：{[item[0] for item in values]}",
        f"相邻变化（百分点）={rounded_changes}；实质变化方向={signs}",
        (f"端点净变化={net_change:.3f}pp；线性斜率={slope:.3f}pp/期；"
         f"与端点方向一致率={direction_consistency:.1%}"),
        f"描述性趋势={status}；不代表统计显著性结论",
    ])
    return result


def _axis(axis_id: str, *, headline: dict | None, history: list[dict], period: str | None,
          rows: list[dict], methodology_regimes: set[str], freshness: dict,
          extra: dict | None = None) -> dict[str, Any]:
    definition = AXIS_DEFINITIONS[axis_id]
    comparable = sorted(
        [(str(row["period"]), float(row["value"]), row.get("observation_id", ""))
         for row in history if row and row.get("value") is not None],
        key=lambda item: _period_sort_key(item[0]))
    trend = (_trend(comparable, regime_count=len(methodology_regimes or {"default"}))
             if headline else {"status": "unavailable", "rule_version": TREND_RULE_VERSION,
                               "minimum_history": MIN_HISTORY, "tolerance_pp": TOLERANCE_PP,
                               "periods": [], "input_observation_ids": [],
                               "steps": ["headline unavailable"]})
    if axis_id == "enterprise_breadth":
        se_values = [{"period": row.get("period"), "standard_error": row.get("standard_error"),
                      "observation_id": row.get("standard_error_observation_id")}
                     for row in history if row.get("standard_error") is not None]
        trend["standard_errors"] = se_values
        trend["statistical_inference"] = "not_performed"
    return {"axis_id": axis_id, **definition, "period": period, "headline": headline,
            "trend": trend, "methodology_regimes": sorted(methodology_regimes),
            "published_at": _row_time(rows, "published_at"),
            "known_at": _row_time(rows, "known_at") or _row_time(rows, "fetched_at"),
            "freshness": freshness, "input_observation_ids": sorted({row.get("observation_id", "")
                                                                       for row in rows} - {""}),
            "artifact_ids": sorted({row.get("artifact_id", "") for row in rows} - {""}),
            "source_status": "available" if headline else "unavailable", **(extra or {})}


def _comparability(axes: dict[str, dict]) -> dict[str, Any]:
    dimensions = ("statistical_unit", "denominator", "geography", "technology_scope",
                  "reference_period", "frequency", "methodology_regime")
    pairs = []
    names = list(axes)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1:]:
            left, right = axes[left_name], axes[right_name]
            values = {}
            for dimension in dimensions:
                if dimension == "methodology_regime":
                    lv, rv = left.get("methodology_regimes"), right.get("methodology_regimes")
                else:
                    lv, rv = left.get(dimension), right.get(dimension)
                values[dimension] = {"left": lv, "right": rv, "compatible": bool(lv and lv == rv)}
            fully = all(item["compatible"] for item in values.values())
            pairs.append({"left": left_name, "right": right_name, "dimensions": values,
                          "fully_compatible": fully,
                          "allowed_use": "numeric_comparison" if fully else "directional_context_only"})
    return {"version": COMPARABILITY_VERSION, "pairs": pairs,
            "numeric_comparison_allowed_pairs": [f'{p["left"]}:{p["right"]}' for p in pairs
                                                   if p["fully_compatible"]]}


def _overall(axes: dict[str, dict]) -> dict[str, Any]:
    states = {name: axis["trend"]["status"] for name, axis in axes.items()}
    available = {name: state for name, state in states.items() if state != "unavailable"}
    expanding = {name for name, state in states.items() if state == "expanding"}
    contrary = {name for name, state in states.items() if state in {"contracting", "mixed"}}
    if len(expanding) >= 3 and not contrary:
        status = "broadening_and_deepening"
    elif states["enterprise_breadth"] == "expanding" and states["task_production"] != "expanding":
        status = "breadth_without_confirmed_depth"
    elif expanding == {"task_production"}:
        status = "provider_telemetry_only"
    elif contrary or (expanding and any(state == "stable" for state in available.values())):
        status = "mixed_evidence"
    else:
        status = "insufficient_history"
    if status == "broadening_and_deepening":
        interpretation = "至少三条独立证据轴呈中期扩大，且没有方向相反的轴。"
    elif status == "mixed_evidence":
        interpretation = "可判定的证据轴存在方向冲突或混合走势，不能声称同步扩大。"
    elif expanding:
        labels = [AXIS_DEFINITIONS[name]["label"] for name in sorted(expanding)]
        interpretation = f"{'、'.join(labels)}呈中期扩大；其余轴历史不足或尚未确认，不能声称三轴同步扩大。"
    else:
        interpretation = "可比较历史不足，暂不能判断三轴是否同步扩大。"
    return {"status": status, "interpretation": interpretation,
            "rule_version": OVERALL_RULE_VERSION, "axis_statuses": states,
            "steps": [f"expanding={sorted(expanding)}", f"contrary={sorted(contrary)}",
                      "No composite score is calculated."]}


def build(products, *, as_of: datetime | None = None, snapshot_consumer: str = "",
          snapshot_purpose: str = "") -> dict[str, Any]:
    selected_at = as_of or datetime.now(timezone.utc)
    btos = products.census_btos_ai_snapshot(as_of=as_of)
    rps = products.rps_genai_adoption_snapshot(as_of=as_of)
    anth = products.ai_production_penetration(as_of=as_of, source_product="1p_api")

    btos_history = btos.get("history", [])
    btos_rows = btos.get("rows", [])
    rps_history = rps.get("history", {}).get("last_week", [])
    rps_rows = [row for values in rps.get("history", {}).values() for row in values]
    anth_series = [row for row in anth.get("period_rows", []) if row.get("grain") == "task"]
    anth_history = [{"period": row["period"], "value": row.get("production_traffic_share_pct"),
                     "observation_id": next(iter(row.get("lineage", {}).get("input_observation_ids", [])), "")}
                    for row in anth_series if row.get("production_traffic_share_pct") is not None]
    anth_observation_ids = sorted({observation_id
        for row in anth.get("period_rows", [])
        for observation_id in row.get("lineage", {}).get("input_observation_ids", [])})
    anth_rows = [row for observation_id in anth_observation_ids
                 if (row := products.structured.observation(observation_id)) is not None]

    axes = {
        "enterprise_breadth": _axis(
            "enterprise_breadth", headline=btos.get("current_use"), history=btos_history,
            period=btos.get("period"), rows=btos_rows,
            methodology_regimes={btos.get("methodology_regime", "")}-{''},
            freshness=btos.get("freshness", {}), extra={"source_id": btos.get("source_id"),
                                                        "detail": btos}),
        "worker_persistence": _axis(
            "worker_persistence", headline=rps.get("latest", {}).get("last_week"), history=rps_history,
            period=rps.get("period"), rows=rps_rows, methodology_regimes={"rps_work_quarterly"},
            freshness=rps.get("freshness", {}), extra={"source_id": rps.get("source_id"),
              "detail": rps,
              "persistence_proxies": {key: rps.get("derivations", {}).get(key)
                                      for key in ("weekly_persistence_proxy", "daily_persistence_proxy")}}),
        "task_production": _axis(
            "task_production", headline=(anth_series[-1] if anth_series else None), history=anth_history,
            period=anth.get("latest_period"), rows=anth_rows,
            methodology_regimes={row.get("methodology_version", "") for row in anth_series}-{''},
            freshness={"source_native_cadence": "release_event"},
            extra={"source_id": anth.get("source_id"), "source_product": "1p_api",
                   "threshold_version": anth.get("threshold_version"),
                   "derivation_version": anth.get("derivation_version"),
                   "detail": anth}),
    }
    for axis in axes.values():
        axis["age_days"] = _age_days(axis.get("published_at") or axis.get("known_at"), selected_at)
    comparison = _comparability(axes)
    overall = _overall(axes)
    periods = {name: axis["period"] for name, axis in axes.items()}
    release_identities = {}
    # The headline snapshot is intentionally small.  Detail tables keep their
    # own rows hashes and expandable lineage; they are not flattened into the
    # claim manifest or Agent context.
    headline_rows = btos_history + rps_history
    detail_lineage = {}
    for name, rows_for_axis in {
        "enterprise_breadth": btos_rows,
        "worker_persistence": rps_rows,
        "task_production": anth_rows,
    }.items():
        ids = sorted({row.get("observation_id", "") for row in rows_for_axis} - {""})
        detail_lineage[name] = {
            "input_observation_count": len(ids),
            "input_observation_ids_hash": hashlib.sha256(
                json.dumps(ids, separators=(",", ":")).encode()).hexdigest(),
            "artifact_ids": sorted({row.get("artifact_id", "") for row in rows_for_axis} - {""}),
            "expand_via": f"DataProducts.ai_adoption_evidence_bundle.axes.{name}.detail",
        }
    all_rows = headline_rows
    for row in all_rows:
        raw = row.get("raw") or {}
        if isinstance(raw, str):
            try: raw = json.loads(raw)
            except json.JSONDecodeError: raw = {}
        source = row.get("source_id", "")
        identity = raw.get("release_identity") or raw.get("source_version")
        if source and identity:
            release_identities.setdefault(source, set()).add(str(identity))
    metadata = {
        "bundle_version": BUNDLE_VERSION, "query": {"as_of": _iso(as_of)}, "periods": periods,
        "release_identities": {key: sorted(value) for key, value in release_identities.items()},
        "rule_versions": {"trend": TREND_RULE_VERSION, "overall": OVERALL_RULE_VERSION,
                          "comparability": COMPARABILITY_VERSION},
        "comparability": comparison, "overall": overall,
        "detail_lineage": detail_lineage,
        "axis_summaries": {name: {key: axis.get(key) for key in
                            ("period", "source_id", "source_status", "trend", "headline")}
                           for name, axis in axes.items()},
    }
    manifest = None
    if snapshot_consumer:
        manifest = products.snapshot_manifest(
            consumer=snapshot_consumer, purpose=snapshot_purpose or "ai_adoption_evidence_bundle",
            as_of=selected_at, rows=all_rows, metadata=metadata)
    body = {"status": "ok" if any(a["source_status"] == "available" for a in axes.values()) else "unavailable",
            "bundle_version": BUNDLE_VERSION, "as_of": selected_at.isoformat(), "axes": axes,
            "periods_are_asynchronous": len({p for p in periods.values() if p}) > 1,
            "no_forward_fill": True, "no_interpolation": True, "comparability": comparison,
            "overall": overall, "manifest": manifest, "detail_lineage": detail_lineage}
    body["content_hash"] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str,
                                                       ensure_ascii=False).encode()).hexdigest()
    return body


def replay(products, snapshot_id: str) -> dict[str, Any] | None:
    replayed = products.replay_snapshot(snapshot_id)
    if replayed is None:
        return None
    metadata = replayed.get("metadata", {})
    return {"status": "replayed", "offline": True, "snapshot_id": snapshot_id,
            "bundle_version": metadata.get("bundle_version"), "periods": metadata.get("periods", {}),
            "overall": metadata.get("overall", {}), "comparability": metadata.get("comparability", {}),
            "axis_summaries": metadata.get("axis_summaries", {}), "rows": replayed.get("rows", []),
            "artifacts": replayed.get("artifacts", []), "manifest": replayed}
