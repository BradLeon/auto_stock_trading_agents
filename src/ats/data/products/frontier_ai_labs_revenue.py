"""Frontier AI Labs revenue DataProducts.

Everything here is read-only over the unified structured store.  The module never
contacts Sacra or TickerTrends, never interpolates a missing month, never mixes a
projection into a historical actual and never averages growth rates across
companies.  Comparability is decided explicitly: two figures may sit side by side
without being ranked, subtracted or fused.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Sequence

DATASET_ID = "frontier_ai_labs_revenue"
SOURCE_SACRA = "sacra_public_company_profiles"
SOURCE_TICKERTRENDS = "tickertrends_public_research"

METRIC_REPORTED_ARR = "ai.frontier_lab.reported_arr"
METRIC_ANNUALIZED_RUN_RATE = "ai.frontier_lab.annualized_revenue_run_rate"
METRIC_TRAILING_REVENUE = "ai.frontier_lab.trailing_revenue"
METRIC_FORWARD_PROJECTION = "ai.frontier_lab.forward_revenue_projection"

IDENTITY_COMPANY = "company_reported"
IDENTITY_MEDIA = "media_reported"
IDENTITY_ESTIMATE = "third_party_estimate"
IDENTITY_PROJECTION = "projection"

DERIVATION_VERSION = "frontier_ai_labs_revenue/v1"
TREND_RULE_VERSION = "frontier_labs_revenue_trend/v1"
COMPARABILITY_VERSION = "frontier_labs_revenue_comparability/v1"
HEADLINE_RULE_VERSION = "frontier_labs_revenue_headline/v1"
SECTION_RULE_VERSION = "frontier_labs_revenue_section/v1"

TREND_MINIMUM_POINTS = 3
TREND_MINIMUM_DAYS = 60
TREND_NET_CHANGE_THRESHOLD = 0.10
DEFAULT_CONFLICT_TOLERANCE = 0.10
BILLION = 1_000_000_000.0

COMPANIES: tuple[dict[str, str], ...] = (
    {"entity_id": "OPENAI", "label": "OpenAI"},
    {"entity_id": "ANTHROPIC", "label": "Anthropic"},
)
COMPANY_LABELS = {item["entity_id"]: item["label"] for item in COMPANIES}

# Cell families are tried in this order when deciding which series answers the
# "scale and trend" question for a company.  Projections never answer it.
CELL_METRIC_PREFERENCE = (
    METRIC_ANNUALIZED_RUN_RATE, METRIC_REPORTED_ARR, METRIC_TRAILING_REVENUE,
    METRIC_FORWARD_PROJECTION)
HISTORICAL_METRICS = (METRIC_ANNUALIZED_RUN_RATE, METRIC_REPORTED_ARR,
                      METRIC_TRAILING_REVENUE)

IDENTITY_LABELS = {
    IDENTITY_COMPANY: "公司披露",
    IDENTITY_MEDIA: "媒体披露",
    IDENTITY_ESTIMATE: "第三方估算",
    IDENTITY_PROJECTION: "预测/目标",
}
METRIC_LABELS = {
    METRIC_REPORTED_ARR: "披露 ARR（合同口径）",
    METRIC_ANNUALIZED_RUN_RATE: "年化收入运行率",
    METRIC_TRAILING_REVENUE: "已实现期间收入",
    METRIC_FORWARD_PROJECTION: "未来收入预测/目标",
}
SOURCE_LABELS = {
    SOURCE_SACRA: "Sacra 公开公司页面",
    SOURCE_TICKERTRENDS: "TickerTrends 公开文章（2026H1 冻结回填）",
}
TREND_LABELS = {
    "expanding": "扩大",
    "contracting": "收缩",
    "stable": "基本持平",
    "mixed": "混合波动",
    "insufficient_history": "历史不足",
    "unavailable": "数据不可用",
}


def _dims(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("dimensions")
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(row.get("dimensions_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _raw(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("raw")
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(row.get("raw_payload") or row.get("raw_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _quality_payload(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("quality")
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(row.get("quality_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _dataset_quality(repository) -> dict[str, Any]:
    row = repository.dataset(DATASET_ID) or {}
    try:
        return json.loads(row.get("quality_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _identity(row: dict[str, Any]) -> str:
    return str(_dims(row).get("observation_identity") or "")


def _regime(row: dict[str, Any]) -> str:
    return str(_dims(row).get("methodology_regime") or "")


def _cell_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (str(row.get("entity_id") or ""), str(row.get("metric_id") or ""),
            _identity(row), str(row.get("currency") or ""), _regime(row))


def _iso_day(value: str) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _period_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    period = str(row.get("period") or "")
    start = str(row.get("period_start") or "")
    return (start or period, period)


def _billion(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return round(numeric / BILLION, 6)


def _period_gap(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Describe missing calendar months without forward filling them."""
    keys: list[str] = []
    for row in rows:
        period = str(row.get("period") or "")
        if len(period) == 7 and period[4] == "-":
            keys.append(period)
    keys = sorted(set(keys))
    observed = keys
    if len(keys) < 2:
        return {"status": "not_applicable", "observed_periods": observed,
                "missing_periods": [],
                "note": "离散披露点不足以判断月度连续性；不插值、不前向填充。"}
    try:
        year, month = map(int, keys[0].split("-"))
        end_year, end_month = map(int, keys[-1].split("-"))
    except ValueError:
        return {"status": "unparseable", "observed_periods": observed, "missing_periods": []}
    expected: list[str] = []
    while (year, month) <= (end_year, end_month):
        expected.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    missing = [value for value in expected if value not in keys]
    return {"status": "gap" if missing else "continuous", "observed_periods": observed,
            "missing_periods": missing,
            "note": "缺的月份没有被披露，图中只画离散点；不得把线段读作月度估算。"}


def _lineage(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    observations = sorted({str(row.get("observation_id") or "") for row in materialized
                           if row.get("observation_id")})
    artifacts = sorted({str(row.get("artifact_id") or "") for row in materialized
                        if row.get("artifact_id")})
    return {
        "derivation_version": DERIVATION_VERSION,
        "input_observation_ids": observations,
        "input_observation_ids_hash": hashlib.sha256(
            json.dumps(observations, separators=(",", ":")).encode()).hexdigest(),
        "artifact_ids": artifacts,
    }


def _revision_index(repository, rows: Sequence[dict[str, Any]], *, as_of: datetime | None
                    ) -> dict[tuple[str, str], dict[str, Any]]:
    """Count vintages per (series, period) so each row knows whether it is revised."""
    try:
        all_rows = repository.observations(dataset_id=DATASET_ID, as_of=as_of,
                                           latest_only=False, accepted_only=True,
                                           limit=100_000)
    except TypeError:  # pragma: no cover - defensive for alternate store signatures
        return {}
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in all_rows:
        key = (str(row.get("series_id") or ""), str(row.get("period") or ""))
        grouped.setdefault(key, []).append(str(row.get("known_at") or ""))
    return {
        key: {"vintage_count": len(sorted(set(values))),
              "latest_known_at": max(values) if values else ""}
        for key, values in grouped.items()}


def _citation(row: dict[str, Any]) -> dict[str, Any]:
    citation = dict(_raw(row).get("origin_citation") or {})
    return {
        "publisher_url": str(_raw(row).get("publisher_url") or ""),
        "publisher": SOURCE_LABELS.get(str(row.get("source_id") or ""),
                                       str(row.get("source_id") or "")),
        "origin_publisher": str(citation.get("origin_publisher") or ""),
        "origin_title": str(citation.get("origin_title") or ""),
        "origin_url": str(citation.get("origin_url") or ""),
        "origin_quote": str(citation.get("origin_quote") or ""),
        "raw_quote": str(_raw(row).get("raw_quote") or ""),
    }


def to_records(rows: Sequence[dict[str, Any]], *, repository=None,
               as_of: datetime | None = None) -> list[dict[str, Any]]:
    """Project store rows into the one shared record shape used by every output."""
    revisions = _revision_index(repository, rows, as_of=as_of) if repository is not None else {}
    records: list[dict[str, Any]] = []
    for row in sorted(rows, key=_period_sort_key):
        dimensions = _dims(row)
        key = (str(row.get("series_id") or ""), str(row.get("period") or ""))
        vintage = revisions.get(key, {})
        vintage_count = int(vintage.get("vintage_count") or 1)
        records.append({
            "observation_id": str(row.get("observation_id") or ""),
            "series_id": str(row.get("series_id") or ""),
            "artifact_id": str(row.get("artifact_id") or ""),
            "source_id": str(row.get("source_id") or ""),
            "source_label": SOURCE_LABELS.get(str(row.get("source_id") or ""), ""),
            "dataset_id": str(row.get("dataset_id") or DATASET_ID),
            "entity_id": str(row.get("entity_id") or ""),
            "company": COMPANY_LABELS.get(str(row.get("entity_id") or ""),
                                          str(row.get("entity_id") or "")),
            "metric_id": str(row.get("metric_id") or ""),
            "metric_label": METRIC_LABELS.get(str(row.get("metric_id") or ""), ""),
            "observation_identity": str(dimensions.get("observation_identity") or ""),
            "observation_identity_label": IDENTITY_LABELS.get(
                str(dimensions.get("observation_identity") or ""), ""),
            "raw_metric_label": str(dimensions.get("raw_metric_label") or ""),
            "methodology_regime": str(dimensions.get("methodology_regime") or ""),
            "period": str(row.get("period") or ""),
            "period_start": str(row.get("period_start") or ""),
            "period_end": str(row.get("period_end") or ""),
            "period_basis": str(row.get("period_basis") or ""),
            "value_usd": float(row.get("value") or 0.0),
            "value_usd_bn": _billion(row.get("value")),
            "currency": str(row.get("currency") or ""),
            "unit": str(row.get("unit") or ""),
            "published_at": str(row.get("published_at") or ""),
            "known_at": str(row.get("known_at") or ""),
            "quality_status": str(row.get("quality_status") or ""),
            "vintage_count": vintage_count,
            "is_revision": vintage_count > 1,
            "citation": _citation(row),
        })
    return records


def rows_hash(rows: Sequence[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(list(rows), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Comparability / headline selection
# --------------------------------------------------------------------------- #

def comparability(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Decide whether two company revenue figures may be compared numerically."""
    left_records = left.get("citation") or {}
    dimensions = {
        "entity_id": str(left.get("entity_id", "")) == str(right.get("entity_id", "")),
        "metric_id": str(left.get("metric_id", "")) == str(right.get("metric_id", "")),
        "observation_identity": str(left.get("observation_identity", ""))
        == str(right.get("observation_identity", "")),
        "methodology_regime": str(left.get("methodology_regime", ""))
        == str(right.get("methodology_regime", "")),
        "currency": str(left.get("currency", "")) == str(right.get("currency", "")),
        "period_basis": str(left.get("period_basis", "")) == str(right.get("period_basis", "")),
        "reference_period": str(left.get("period", "")) == str(right.get("period", "")),
    }
    comparable = (dimensions["metric_id"] and dimensions["observation_identity"]
                  and dimensions["methodology_regime"] and dimensions["currency"]
                  and dimensions["period_basis"])
    same_entity = dimensions["entity_id"]
    return {
        "version": COMPARABILITY_VERSION,
        "comparable": bool(comparable and same_entity),
        "directionally_comparable": bool(comparable),
        "allowed_use": "numeric_comparison" if comparable else "side_by_side_only",
        "dimensions": dimensions,
        "note": "" if comparable else "口径或身份不同：只能并列展示，不得计算差额、倍数或排名。",
    }


def _relative_difference(left: float, right: float) -> float | None:
    base = abs(left) if abs(left) > abs(right) else abs(right)
    if not base:
        return None
    return abs(left - right) / base


def source_conflicts(records: Sequence[dict[str, Any]], *, tolerance: float
                     ) -> list[dict[str, Any]]:
    """Flag same-period comparable figures whose values disagree beyond tolerance."""
    conflicts: list[dict[str, Any]] = []
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for record in records:
        key = (str(record.get("entity_id") or ""), str(record.get("period") or ""),
               str(record.get("metric_id") or ""),
               str(record.get("observation_identity") or ""),
               str(record.get("methodology_regime") or ""))
        grouped.setdefault(key, []).append(record)
    for key, members in sorted(grouped.items()):
        if len(members) < 2:
            continue
        sources = sorted({str(item.get("source_id") or "") for item in members})
        if len(sources) < 2:
            continue
        if members[0].get("metric_id") != METRIC_FORWARD_PROJECTION and \
                str(members[0].get("metric_id")) not in HISTORICAL_METRICS:
            continue
        values = [float(item.get("value_usd") or 0.0) for item in members]
        reference = max(values)
        difference = _relative_difference(min(values), max(values))
        if difference is None or difference <= tolerance:
            continue
        conflicts.append({
            "company": COMPANY_LABELS.get(str(key[0]), str(key[0])),
            "entity_id": str(key[0]),
            "period": str(key[1]),
            "metric_id": str(key[2]),
            "observation_identity": str(key[3]),
            "status": "source_conflict",
            "relative_difference": round(difference, 6),
            "absolute_difference_usd": round(max(values) - min(values), 2),
            "tolerance": tolerance,
            "values": [
                {"source_id": str(item.get("source_id") or ""),
                 "source_label": str(item.get("source_label") or ""),
                 "value_usd_bn": item.get("value_usd_bn"),
                 "observation_id": str(item.get("observation_id") or "")}
                for item in members],
            "note": "两个来源对同一期间给出可比值但差异超过阈值；并列披露，不用来源优先级掩盖冲突。",
        })
    return conflicts


def select_headline(records: Sequence[dict[str, Any]], *, quality: dict[str, Any]
                    ) -> dict[str, Any]:
    """Choose which value the report leads with, without deleting alternatives."""
    if not records:
        return {"status": "unavailable", "selection_rule_version": HEADLINE_RULE_VERSION,
                "reason": "no_accepted_observation"}
    priority = [str(item) for item in (quality.get("headline_source_priority") or [])
                if str(item)] or [IDENTITY_COMPANY, IDENTITY_MEDIA, IDENTITY_ESTIMATE]
    source_rank = [SOURCE_SACRA, SOURCE_TICKERTRENDS]

    def rank_key(record: dict[str, Any]) -> tuple[int, int]:
        """Lower is better: the governed identity first, then the headline source."""
        identity = str(record.get("observation_identity") or "")
        identity_rank = priority.index(identity) if identity in priority else len(priority)
        source = str(record.get("source_id") or "")
        source_index = source_rank.index(source) if source in source_rank else len(source_rank)
        return (identity_rank, source_index)

    def recency_key(record: dict[str, Any]) -> tuple[str, str, str]:
        return (str(record.get("known_at") or ""), str(record.get("period") or ""),
                str(record.get("source_id") or ""))

    # Two stable passes: newest first, then that order re-sorted by rank.  A single
    # descending sort on the combined tuple would invert the rank itself and make
    # an estimate outrank a company disclosure.
    ordered = sorted(sorted(records, key=recency_key, reverse=True), key=rank_key)
    selected = ordered[0]
    alternatives = [
        {"source_id": str(item.get("source_id") or ""),
         "source_label": str(item.get("source_label") or ""),
         "value_usd_bn": item.get("value_usd_bn"),
         "observation_identity": str(item.get("observation_identity") or ""),
         "observation_id": str(item.get("observation_id") or "")}
        for item in ordered[1:]]
    return {
        "status": "ok",
        "selection_rule_version": HEADLINE_RULE_VERSION,
        "priority": priority,
        "selected_source": str(selected.get("source_id") or ""),
        "selected_source_label": str(selected.get("source_label") or ""),
        "selected_observation_id": str(selected.get("observation_id") or ""),
        "selection_reason": "identity_then_source_then_known_at",
        "record": selected,
        "alternatives": alternatives,
        "note": "来源优先级只决定主显示，不删除候选，也不把估算升级为公司披露。",
    }


# --------------------------------------------------------------------------- #
# Trend derivation
# --------------------------------------------------------------------------- #

def revenue_trend(records: Sequence[dict[str, Any]], *, quality: dict[str, Any]
                  ) -> dict[str, Any]:
    """Direction of one trend cell using only the disclosed discrete points."""
    tolerance = float(quality.get("direction_net_change_threshold",
                                  TREND_NET_CHANGE_THRESHOLD))
    minimum_points = int(quality.get("minimum_comparable_points", TREND_MINIMUM_POINTS))
    minimum_days = int(quality.get("minimum_history_days", TREND_MINIMUM_DAYS))
    points: list[tuple[date, float, dict[str, Any]]] = []
    for record in sorted(records, key=lambda item: str(item.get("period_start") or "")):
        anchor = _iso_day(str(record.get("period_start") or "")) or _iso_day(
            str(record.get("period_end") or ""))
        if anchor is None:
            continue
        points.append((anchor, float(record.get("value_usd") or 0.0), record))
    steps: list[str] = []
    result: dict[str, Any] = {
        "rule_version": TREND_RULE_VERSION,
        "minimum_points": minimum_points,
        "minimum_days": minimum_days,
        "net_change_threshold": tolerance,
        "comparable_point_count": len(points),
        "observed_periods": [str(item[2].get("period") or "") for item in points],
        "period_gap": _period_gap(records),
        "steps": steps,
    }
    if not points:
        result.update(status="unavailable", reason="no_accepted_observation")
        steps.append("没有通过质量门的可比观察")
        return result
    span_days = (points[-1][0] - points[0][0]).days
    result["span_days"] = span_days
    result["first_period"] = str(points[0][2].get("period") or "")
    result["last_period"] = str(points[-1][2].get("period") or "")
    result["first_value_usd_bn"] = _billion(points[0][1])
    result["last_value_usd_bn"] = _billion(points[-1][1])
    if len(points) < minimum_points:
        result.update(status="insufficient_history",
                      reason=f"comparable_point_count={len(points)} < {minimum_points}")
        steps.append(f"可比点 {len(points)} 个 < 最少 {minimum_points} 个：不判断方向")
        return result
    if span_days < minimum_days:
        result.update(status="insufficient_history",
                      reason=f"span_days={span_days} < {minimum_days}")
        steps.append(f"跨度 {span_days} 天 < 最少 {minimum_days} 天：不判断方向")
        return result

    x_days = [(item[0] - points[0][0]).days for item in points]
    y_values = [item[1] for item in points]
    x_mean = sum(x_days) / len(x_days)
    y_mean = sum(y_values) / len(y_values)
    denominator = sum((x - x_mean) ** 2 for x in x_days)
    slope_per_day = (sum((x - x_mean) * (y - y_mean) for x, y in zip(x_days, y_values))
                     / denominator) if denominator else 0.0
    net_change = y_values[-1] - y_values[0]
    net_rate = net_change / y_values[0] if y_values[0] else 0.0
    last_step_rate = ((y_values[-1] - y_values[-2]) / y_values[-2]) if y_values[-2] else 0.0
    endpoint_direction = 1 if net_rate > tolerance else -1 if net_rate < -tolerance else 0
    material_changes = [later - earlier for earlier, later in zip(y_values, y_values[1:])]
    reverse_endpoint = False
    if endpoint_direction and material_changes:
        last = material_changes[-1]
        reverse_rate = abs(last / y_values[-2]) if y_values[-2] else 0.0
        reverse_endpoint = bool(
            reverse_rate >= tolerance
            and (1 if last > 0 else -1) == -endpoint_direction)
    if abs(net_rate) < tolerance and abs(slope_per_day * span_days) / max(y_values[0], 1) < tolerance:
        status = "stable"
        steps.append("净变化率与斜率投影均低于阈值：基本持平")
    elif endpoint_direction > 0 and slope_per_day > 0 and not reverse_endpoint:
        status = "expanding"
        steps.append("净变化率为正且超过阈值、斜率为正，且最近一个可比变化未构成反向阈值变化")
    elif endpoint_direction < 0 and slope_per_day < 0 and not reverse_endpoint:
        status = "contracting"
        steps.append("净变化率为负且低于负阈值、斜率为负，且最近一个可比变化未构成反向阈值变化")
    else:
        status = "mixed"
        steps.append("端点方向与斜率方向冲突，或最近一个可比变化构成反向阈值变化")
    result.update(
        status=status,
        net_change_usd=round(net_change, 2),
        net_change_usd_bn=_billion(net_change),
        net_change_rate=round(net_rate, 6),
        linear_slope_usd_per_day=round(slope_per_day, 4),
        linear_slope_usd_bn_per_30d=round(slope_per_day * 30 / BILLION, 6),
        last_comparable_change_rate=round(last_step_rate, 6),
        reverse_endpoint_move=reverse_endpoint,
        input_observation_ids=[str(item[2].get("observation_id") or "") for item in points],
        note="只使用来源明确的离散披露点，不插值、不前向填充、不把披露日当作收入参考期。")
    return result


# --------------------------------------------------------------------------- #
# Public DataProducts
# --------------------------------------------------------------------------- #

def _fetch_rows(repository, *, metric: str = "", entity_id: str = "",
                source_id: str = "", observation_identity: str = "",
                methodology_regime: str = "", periods: Sequence[str] | None = None,
                as_of: datetime | None = None, include_vintages: bool = False,
                limit: int = 50_000) -> list[dict[str, Any]]:
    rows = repository.observations(dataset_id=DATASET_ID, entity_id=entity_id or None,
                                   metric_id=metric or None, source_id=source_id or None,
                                   as_of=as_of, latest_only=not include_vintages,
                                   accepted_only=True, limit=limit)
    selected = []
    for row in rows:
        dimensions = _dims(row)
        if observation_identity and str(dimensions.get("observation_identity")) != observation_identity:
            continue
        if methodology_regime and str(dimensions.get("methodology_regime")) != methodology_regime:
            continue
        if periods and str(row.get("period")) not in {str(item) for item in periods}:
            continue
        selected.append(row)
    return selected


def frontier_labs_revenue_series(products, *, company: str = "", metric: str = "",
                                 observation_identity: str = "", source_id: str = "",
                                 methodology_regime: str = "",
                                 periods: list[str] | None = None, as_of=None,
                                 include_vintages: bool = False, as_frame: bool = False
                                 ) -> dict[str, Any]:
    """Filtered revenue observations with full citation, identity and lineage."""
    repository = products.structured
    rows = _fetch_rows(repository, entity_id=str(company or "").upper(), metric=metric,
                       source_id=source_id, observation_identity=observation_identity,
                       methodology_regime=methodology_regime, periods=periods,
                       as_of=as_of, include_vintages=include_vintages)
    records = to_records(rows, repository=repository, as_of=as_of)
    result = {
        "status": "ok" if records else "no_coverage",
        "source_access": "data_products_only",
        "dataset_id": DATASET_ID,
        "filters": {"company": str(company or "").upper(), "metric": metric,
                    "observation_identity": observation_identity, "source_id": source_id,
                    "methodology_regime": methodology_regime, "periods": periods or [],
                    "as_of": as_of.isoformat() if as_of else "",
                    "include_vintages": include_vintages},
        "row_count": len(records),
        "rows": records,
        "rows_hash": rows_hash(records),
        "period_gap": _period_gap(records),
        "lineage": _lineage(rows),
        "derivation_version": DERIVATION_VERSION,
        "limitations": [
            "Labs 收入不能代表整个 L1 应用层收入。",
            "年化运行率不是审计收入，也不等同于合同 ARR。",
            "OpenAI 与 Anthropic 的 disclosed 月份不同步，不做对齐插值。",
        ],
    }
    if as_frame:
        import pandas as pd

        frame = pd.DataFrame(records)
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


def frontier_labs_revenue_cells(products, *, company: str = "", as_of=None,
                                ) -> list[dict[str, Any]]:
    """Every comparable trend cell of one company, ranked for headline selection."""
    repository = products.structured
    rows = _fetch_rows(repository, entity_id=str(company or "").upper(), as_of=as_of)
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_cell_key(row), []).append(row)
    cells: list[dict[str, Any]] = []
    for key, members in grouped.items():
        records = to_records(members, repository=repository, as_of=as_of)
        cells.append({
            "entity_id": str(key[0]), "company": COMPANY_LABELS.get(str(key[0]), str(key[0])),
            "metric_id": str(key[1]),
            "metric_label": METRIC_LABELS.get(str(key[1]), str(key[1])),
            "observation_identity": str(key[2]),
            "observation_identity_label": IDENTITY_LABELS.get(str(key[2]), str(key[2])),
            "currency": str(key[3]), "methodology_regime": str(key[4]),
            "point_count": len(records), "periods": sorted(
                {str(item.get("period") or "") for item in records}),
            "rows": records,
        })
    rank = {metric: index for index, metric in enumerate(CELL_METRIC_PREFERENCE)}
    cells.sort(key=lambda item: (rank.get(str(item["metric_id"]), len(rank)),
                                 -int(item["point_count"]), str(item["metric_id"])))
    return cells


def frontier_labs_company_view(products, *, company: str, as_of=None) -> dict[str, Any]:
    """One company: headline value, every cell, conflicts and its trend judgement."""
    repository = products.structured
    quality = _dataset_quality(repository)
    tolerance = float(quality.get("source_conflict_relative_tolerance",
                                 DEFAULT_CONFLICT_TOLERANCE))
    cells = frontier_labs_revenue_cells(products, company=company, as_of=as_of)
    historical = [cell for cell in cells if str(cell["metric_id"]) in HISTORICAL_METRICS]
    comparison = historical[0] if historical else (cells[0] if cells else None)
    trend = revenue_trend(comparison["rows"], quality=quality) if comparison else {
        "status": "unavailable", "rule_version": TREND_RULE_VERSION,
        "comparable_point_count": 0, "steps": ["没有通过质量门的收入观察"]}
    all_records = [record for cell in cells for record in cell["rows"]]
    latest_records = [record for record in all_records
                      if str(record.get("period")) == comparison["periods"][-1]] \
        if comparison and comparison["periods"] else []
    headline = select_headline(latest_records or all_records, quality=quality)
    conflicts = source_conflicts(all_records, tolerance=tolerance)
    return {
        "entity_id": str(company).upper(),
        "company": COMPANY_LABELS.get(str(company).upper(), str(company).upper()),
        "status": "ok" if cells else "unavailable",
        "headline": headline,
        "comparison_cell": (
            {"metric_id": comparison["metric_id"],
             "metric_label": comparison["metric_label"],
             "observation_identity": comparison["observation_identity"],
             "observation_identity_label": comparison["observation_identity_label"],
             "methodology_regime": comparison["methodology_regime"],
             "point_count": comparison["point_count"],
             "periods": comparison["periods"]} if comparison else None),
        "trend": trend,
        "cells": [{key: value for key, value in cell.items() if key != "rows"}
                  for cell in cells],
        "history_rows": comparison["rows"] if comparison else [],
        "rows_hash": rows_hash(comparison["rows"]) if comparison else "",
        "period_gap": trend.get("period_gap", _period_gap([])),
        "source_conflicts": conflicts,
        "lineage": _lineage(all_records),
        "derivation_version": DERIVATION_VERSION,
    }


def _aggregate_section(per_company: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Two companies confirm a common direction only when both already show it."""
    statuses = [str(item.get("trend", {}).get("status") or "unavailable")
                for item in per_company]
    available = [item for item in statuses if item not in {"unavailable", "insufficient_history"}]
    directional = [item for item in available if item in {"expanding", "contracting", "stable"}]
    unresolved = [item for item in statuses if item in {"unavailable", "insufficient_history"}]
    if not available:
        status = "unavailable"
        reason = "没有公司具备通过质量门的可比收入序列"
    elif not directional:
        status = "insufficient_history"
        reason = "所有公司的最高可比序列都不足 3 点或不足 60 天"
    elif unresolved:
        status = "partial"
        reason = "部分公司的可比历史不足，方向判断只覆盖具备序列的公司"
    elif len(set(directional)) == 1:
        status = directional[0]
        reason = "两家公司的最高可比序列方向一致"
    else:
        status = "mixed"
        reason = "两家公司的最高可比序列方向冲突"
    return {
        "status": status,
        "rule_version": SECTION_RULE_VERSION,
        "reason": reason,
        "company_statuses": [
            {"entity_id": str(item.get("entity_id") or ""),
             "company": str(item.get("company") or ""),
             "trend_status": str(item.get("trend", {}).get("status") or "unavailable")}
            for item in per_company],
        "cross_company_average_growth_computed": False,
        "note": "不计算跨公司平均增速；公司口径不可比时不计算差额、倍数或排名。",
    }


def frontier_labs_revenue_evidence_bundle(products, *, companies: Sequence[str] | None = None,
                                          as_of=None, snapshot_consumer: str = "",
                                          snapshot_purpose: str = "") -> dict[str, Any]:
    """Complete Frontier Labs revenue evidence bundle for the commercialization observer."""
    repository = products.structured
    quality = _dataset_quality(repository)
    targets = companies or [item["entity_id"] for item in COMPANIES]
    per_company = [frontier_labs_company_view(products, company=item, as_of=as_of)
                   for item in targets]
    history_rows: list[dict[str, Any]] = []
    for view in per_company:
        history_rows.extend(view.get("history_rows") or [])
    conflicts = [item for view in per_company for item in (view.get("source_conflicts") or [])]
    comparable_across_companies = True
    comparison_pair: list[dict[str, str]] = []
    views_with_cells = [view for view in per_company if view.get("comparison_cell")]
    if len(views_with_cells) >= 2:
        left = views_with_cells[0]["comparison_cell"]
        right = views_with_cells[1]["comparison_cell"]
        comparable_across_companies = bool(
            left["metric_id"] == right["metric_id"]
            and left["observation_identity"] == right["observation_identity"]
            and left["methodology_regime"] == right["methodology_regime"])
        comparison_pair = [
            {"entity_id": str(views_with_cells[0].get("entity_id") or ""),
             "metric_id": str(left["metric_id"]),
             "observation_identity": str(left["observation_identity"]),
             "methodology_regime": str(left["methodology_regime"])},
            {"entity_id": str(views_with_cells[1].get("entity_id") or ""),
             "metric_id": str(right["metric_id"]),
             "observation_identity": str(right["observation_identity"]),
             "methodology_regime": str(right["methodology_regime"])}]
    section = _aggregate_section(per_company)
    manifest = None
    if snapshot_consumer and history_rows:
        cutoff = as_of or datetime.now(timezone.utc)
        selected = [dict(row, selected_source=str(row.get("source_id") or ""),
                         derivation_version=DERIVATION_VERSION,
                         selection_reason="cell_then_headline_rule")
                    for row in history_rows]
        manifest = products.snapshot_manifest(
            consumer=snapshot_consumer,
            purpose=snapshot_purpose or f"{DATASET_ID}:{DERIVATION_VERSION}",
            as_of=cutoff, rows=selected,
            metadata={"derivation_version": DERIVATION_VERSION,
                      "trend_rule_version": TREND_RULE_VERSION,
                      "section_rule_version": SECTION_RULE_VERSION,
                      "comparability_version": COMPARABILITY_VERSION,
                      "headline_rule_version": HEADLINE_RULE_VERSION,
                      "comparable_across_companies": comparable_across_companies})
    return {
        "status": "ok" if any(view.get("status") == "ok" for view in per_company)
                  else "unavailable",
        "source_access": "data_products_only",
        "dataset_id": DATASET_ID,
        "derivation_version": DERIVATION_VERSION,
        "as_of": as_of.isoformat() if as_of else "",
        "companies": per_company,
        "history_rows": history_rows,
        "rows_hash": rows_hash(history_rows),
        "source_conflicts": conflicts,
        "comparability": {
            "version": COMPARABILITY_VERSION,
            "across_companies": comparable_across_companies,
            "comparison_pair": comparison_pair,
            "note": "" if comparable_across_companies else
                    "两家公司的最高可比口径不同：只并列趋势，不计算绝对差额、倍数或排名。",
        },
        "section": section,
        "lineage": _lineage(history_rows),
        "manifest": manifest,
        "limitations": [
            "只覆盖 Frontier AI Labs 的公司级收入，不代表 L1 应用层整体收入。",
            "缺失月份是真实缺口，不是零，也不被插值或前向填充。",
            "收入增长只证明收入兑现，不证明留存、毛利、客户集中度或单位经济。",
        ],
    }
