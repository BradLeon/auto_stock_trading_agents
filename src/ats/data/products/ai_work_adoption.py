"""Read models and reproducible query-time derivations for Claude job adoption."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

SOURCE_ID = "anthropic_economic_index"
DATASET_ID = "ai_work_adoption"
DERIVATION_VERSION = "ai_work_adoption/v1"
PRODUCTION_DERIVATION_VERSION = "ai_production_penetration/v1"
PRODUCTION_THRESHOLD_VERSION = "core_production_workflow_proxy_v1"
PRODUCTION_SOURCE_PRODUCT = "1p_api"
PRODUCTION_PROXY = {
    "version": PRODUCTION_THRESHOLD_VERSION,
    "usage_share": {"operator": ">", "value": 0.0, "unit": "percent"},
    "work_use_share": {"operator": ">=", "value": 80.0, "unit": "percent"},
    "automation_share": {"operator": ">=", "value": 80.0, "unit": "percent"},
    "directive_share": {"operator": ">=", "value": 50.0, "unit": "percent"},
}
PRODUCTION_GRAINS = {"occupation", "task"}
PRODUCTION_CORE_SERIES = (
    "occupation_visible_production_rate",
    "task_visible_production_rate",
    "occupation_production_traffic_share",
    "task_production_traffic_share",
)
WORK_USE = "ai.work_adoption.work_use_share"
DIRECTIVE = "ai.work_adoption.collaboration.directive_share"
USAGE = "ai.work_adoption.usage_share"
AUTOMATION = "ai.work_adoption.automation_share"
AUGMENTATION = "ai.work_adoption.augmentation_share"
EXPOSURE = "ai.work_adoption.observed_exposure"
PENETRATION = "ai.work_adoption.task_penetration"
COLLABORATION_METRICS = (
    "ai.work_adoption.collaboration.directive_share",
    "ai.work_adoption.collaboration.feedback_loop_share",
    "ai.work_adoption.collaboration.task_iteration_share",
    "ai.work_adoption.collaboration.validation_share",
    "ai.work_adoption.collaboration.learning_share",
    "ai.work_adoption.collaboration.none_share",
)


def _dims(row: dict) -> dict:
    value = row.get("dimensions")
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(row.get("dimensions_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _previous_month(period: str) -> str | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if not match:
        return None
    year, month = map(int, match.groups())
    return f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"


def _previous_year_month(period: str) -> str | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})", period)
    return f"{int(match.group(1)) - 1:04d}-{match.group(2)}" if match else None


def _lineage(*rows: dict | None, relation_ids: list[str] | None = None) -> dict:
    observations = [row["observation_id"] for row in rows if row and row.get("observation_id")]
    return {
        "derivation_version": DERIVATION_VERSION,
        "input_observation_ids": observations,
        "input_relation_ids": relation_ids or [],
    }


def _production_lineage(rows: list[dict], relation_ids: list[str] | None = None) -> dict:
    """The production derivation keeps its own version while reusing immutable rows."""
    return {
        "derivation_version": PRODUCTION_DERIVATION_VERSION,
        "input_observation_ids": sorted(
            {row["observation_id"] for row in rows if row.get("observation_id")}
        ),
        "input_relation_ids": sorted(set(relation_ids or [])),
    }


def _record_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


def _production_grain(row: dict) -> str | None:
    dims = _dims(row)
    if dims.get("classification") == "soc_occupation" and dims.get("hierarchy_level") == 0:
        return "occupation"
    if dims.get("classification") == "onet" and dims.get("hierarchy_level") == 0:
        return "task"
    return None


def _periods_are_adjacent(previous: str, current: str) -> bool:
    return _previous_month(current) == previous


def _production_cells(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Build fail-closed, same-product/month/methodology cells from long observations."""
    required = (USAGE, WORK_USE, AUTOMATION, DIRECTIVE)
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        grain = _production_grain(row)
        if grain is None:
            continue
        dims = _dims(row)
        key = (
            dims.get("source_product"),
            row.get("period"),
            dims.get("methodology_version", ""),
            dims.get("classification"),
            dims.get("hierarchy_level"),
            row.get("entity_id"),
        )
        grouped[key].append(row)
    cells, diagnostics = [], []
    for key, members in sorted(grouped.items()):
        product, period, methodology, classification, hierarchy, entity_id = key
        grain = "occupation" if classification == "soc_occupation" else "task"
        by_metric: dict[str, list[dict]] = defaultdict(list)
        for row in members:
            if row.get("metric_id") in required:
                by_metric[row["metric_id"]].append(row)
        reasons: list[str] = []
        metrics: dict[str, dict] = {}
        for metric in required:
            choices = by_metric.get(metric, [])
            if not choices:
                reasons.append(f"missing_{metric.rsplit('.', 1)[-1]}")
                continue
            try:
                values = {float(item.get("value")) for item in choices}
            except (TypeError, ValueError):
                reasons.append(f"invalid_{metric.rsplit('.', 1)[-1]}")
                continue
            units = {item.get("unit") for item in choices}
            if len(values) != 1 or len(units) != 1:
                reasons.append(f"conflicting_{metric.rsplit('.', 1)[-1]}")
                continue
            selected = max(
                choices, key=lambda item: (item.get("known_at", ""), item.get("fetched_at", ""))
            )
            if selected.get("unit") != "percent" or not 0 <= float(selected["value"]) <= 100:
                reasons.append(f"invalid_{metric.rsplit('.', 1)[-1]}")
                continue
            metrics[metric] = selected
        inputs = {
            "usage_share": metrics.get(USAGE),
            "work_use_share": metrics.get(WORK_USE),
            "automation_share": metrics.get(AUTOMATION),
            "directive_share": metrics.get(DIRECTIVE),
        }
        comparisons = {}
        for name, rule in PRODUCTION_PROXY.items():
            if name == "version":
                continue
            value_row = inputs.get(name)
            if value_row is None:
                comparisons[name] = {
                    "value": None,
                    "observation_id": None,
                    "threshold": rule,
                    "passed": False,
                }
                continue
            value = float(value_row["value"])
            passed = value > rule["value"] if rule["operator"] == ">" else value >= rule["value"]
            comparisons[name] = {
                "value": value,
                "observation_id": value_row.get("observation_id"),
                "threshold": rule,
                "passed": passed,
            }
            if not passed:
                reasons.append(f"{name}_below_threshold")
        visible = USAGE in metrics
        qualified = visible and not reasons
        cell = {
            "entity_id": entity_id,
            "entity_name": _dims(members[0]).get("node_name", entity_id),
            "grain": grain,
            "period": period,
            "source_product": product,
            "methodology_version": methodology,
            "classification": classification,
            "hierarchy_level": hierarchy,
            "visible": visible,
            "qualified": qualified,
            "quality_status": "accepted" if not reasons else "warning",
            "reason_codes": sorted(set(reasons)),
            "inputs": comparisons,
            "_observation_rows": list(metrics.values()),
            "lineage": _production_lineage(list(metrics.values())),
        }
        cells.append(cell)
        if reasons:
            diagnostics.append(
                {
                    "entity_id": entity_id,
                    "period": period,
                    "grain": grain,
                    "reason_codes": cell["reason_codes"],
                }
            )
    return cells, diagnostics


def _cell_value(cell: dict, name: str) -> float | None:
    item = (cell.get("inputs") or {}).get(name) or {}
    return item.get("value")


def _entities_by_id(products) -> dict[str, dict]:
    return {row["entity_id"]: row for row in products.structured.entities()}


def _production_frames(result: dict) -> dict[str, Any]:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required for as_frame=True") from exc
    definitions = {
        "summary": [
            "period",
            "grain",
            "visible_count",
            "qualified_count",
            "visible_production_rate_pct",
            "production_traffic_share_pct",
            "published_usage_share_pct",
            "conditional_production_traffic_share_pct",
            "source_product",
            "threshold_version",
            "methodology_version",
            "quality_status",
            "change_status",
        ],
        "top_occupations": [
            "rank",
            "entity_id",
            "entity_name",
            "period",
            "usage_share_pct",
            "work_use_share_pct",
            "automation_share_pct",
            "directive_share_pct",
            "usage_share_change_pp",
            "work_use_share_change_pp",
            "automation_share_change_pp",
            "directive_share_change_pp",
            "change_status",
        ],
        "top_tasks": [
            "rank",
            "entity_id",
            "entity_name",
            "period",
            "usage_share_pct",
            "work_use_share_pct",
            "automation_share_pct",
            "directive_share_pct",
            "usage_share_change_pp",
            "work_use_share_change_pp",
            "automation_share_change_pp",
            "directive_share_change_pp",
            "change_status",
        ],
        "coverage": [
            "period",
            "visible_task_count",
            "mapped_task_count",
            "unmapped_task_count",
            "qualified_mapped_task_count",
            "qualified_unmapped_task_count",
            "mapping_coverage_pct",
            "taxonomy_version",
            "quality_status",
        ],
        "occupation_task_coverage": [
            "occupation_id",
            "occupation_name",
            "period",
            "qualified_mapped_task_count",
            "taxonomy_task_count",
            "coverage_lower_bound_pct",
            "taxonomy_version",
            "quality_status",
        ],
        "occupation_coverage_distribution": [
            "minimum_coverage_pct",
            "occupation_count",
            "eligible_occupation_count",
            "occupation_share_pct",
            "period",
            "taxonomy_version",
            "point_type",
        ],
    }
    sources = {
        "summary": result.get("period_rows", []),
        "top_occupations": result.get("top_occupations", []),
        "top_tasks": result.get("top_tasks", []),
        "coverage": result.get("coverage_diagnostics", []),
        "occupation_task_coverage": result.get("occupation_task_coverage", []),
        "occupation_coverage_distribution": result.get("occupation_coverage_distribution", {}).get(
            "points", []
        )
        + result.get("occupation_coverage_distribution", {}).get("landmarks", []),
    }
    integer_columns = {
        "visible_count",
        "qualified_count",
        "rank",
        "visible_task_count",
        "mapped_task_count",
        "unmapped_task_count",
        "qualified_mapped_task_count",
        "qualified_unmapped_task_count",
        "taxonomy_task_count",
        "occupation_count",
        "eligible_occupation_count",
    }
    numeric_columns = {
        "visible_production_rate_pct",
        "production_traffic_share_pct",
        "published_usage_share_pct",
        "conditional_production_traffic_share_pct",
        "usage_share_pct",
        "work_use_share_pct",
        "automation_share_pct",
        "directive_share_pct",
        "usage_share_change_pp",
        "work_use_share_change_pp",
        "automation_share_change_pp",
        "directive_share_change_pp",
        "mapping_coverage_pct",
        "coverage_lower_bound_pct",
        "minimum_coverage_pct",
        "occupation_share_pct",
    }
    frames = {}
    for name, rows in sources.items():
        frame = pd.DataFrame(rows, columns=definitions[name])
        for column in frame.columns:
            if column in integer_columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
            elif column in numeric_columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Float64")
            else:
                frame[column] = frame[column].astype("string")
        frames[name] = frame
    return frames


def _public_cell(cell: dict) -> dict:
    return {key: value for key, value in cell.items() if key != "_observation_rows"}


def _production_change(current: dict, previous: dict | None) -> tuple[dict, str]:
    if previous is None:
        return (
            {
                name: None
                for name in (
                    "usage_share_change_pp",
                    "work_use_share_change_pp",
                    "automation_share_change_pp",
                    "directive_share_change_pp",
                )
            },
            "insufficient_history",
        )
    if not _periods_are_adjacent(previous["period"], current["period"]):
        return (
            {
                name: None
                for name in (
                    "usage_share_change_pp",
                    "work_use_share_change_pp",
                    "automation_share_change_pp",
                    "directive_share_change_pp",
                )
            },
            "period_gap",
        )
    if previous["methodology_version"] != current["methodology_version"]:
        return (
            {
                name: None
                for name in (
                    "usage_share_change_pp",
                    "work_use_share_change_pp",
                    "automation_share_change_pp",
                    "directive_share_change_pp",
                )
            },
            "methodology_changed",
        )
    return (
        {
            "usage_share_change_pp": _cell_value(current, "usage_share")
            - _cell_value(previous, "usage_share"),
            "work_use_share_change_pp": _cell_value(current, "work_use_share")
            - _cell_value(previous, "work_use_share"),
            "automation_share_change_pp": _cell_value(current, "automation_share")
            - _cell_value(previous, "automation_share"),
            "directive_share_change_pp": _cell_value(current, "directive_share")
            - _cell_value(previous, "directive_share"),
        },
        "ok",
    )


def ai_production_penetration(
    products,
    *,
    periods: list[str] | tuple[str, ...] | None = None,
    as_of: datetime | None = None,
    top_n: int = 10,
    as_frame: bool = False,
    source_product: str = PRODUCTION_SOURCE_PRODUCT,
    snapshot_consumer: str = "",
    snapshot_purpose: str = "",
) -> dict:
    """Derive the versioned 1P API production-workflow proxy at query time.

    This surface intentionally exposes product traffic and a user-defined proxy only;
    it is not an employee, firm, or confirmed-workflow adoption estimator.
    """
    if source_product != PRODUCTION_SOURCE_PRODUCT:
        return {
            "status": "unsupported_source_product",
            "source_product": source_product,
            "supported_source_product": PRODUCTION_SOURCE_PRODUCT,
            "reason": "core_production_workflow_proxy_v1 only permits 1p_api",
        }
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    all_rows = _production_monthly_rows(products, as_of=as_of)
    available_periods = sorted({row["period"] for row in all_rows if row.get("period")})
    selected_periods = sorted(set(periods or available_periods))
    if periods:
        selected_periods = [period for period in selected_periods if period in available_periods]
    rows = [row for row in all_rows if row.get("period") in selected_periods]
    cells, cell_diagnostics = _production_cells(rows)
    cells_by_key = {
        (cell["period"], cell["grain"], cell["methodology_version"], cell["entity_id"]): cell
        for cell in cells
    }
    entities = _entities_by_id(products)
    occupation_names = {
        cell["entity_id"]: cell["entity_name"]
        for cell in cells
        if cell["grain"] == "occupation" and cell.get("entity_name")
    }
    period_rows: list[dict] = []
    all_input_rows: list[dict] = []
    for period, grain, methodology, group in sorted(
        (
            (
                period,
                grain,
                methodology,
                [
                    cell
                    for cell in cells
                    if cell["period"] == period
                    and cell["grain"] == grain
                    and cell["methodology_version"] == methodology
                ],
            )
            for period in selected_periods
            for grain in sorted(PRODUCTION_GRAINS)
            for methodology in sorted(
                {
                    cell["methodology_version"]
                    for cell in cells
                    if cell["period"] == period and cell["grain"] == grain
                }
            )
        ),
        key=lambda value: value[:3],
    ):
        visible = [cell for cell in group if cell["visible"]]
        if not visible:
            continue
        qualified = [cell for cell in visible if cell["qualified"]]
        input_rows = [row for cell in visible for row in cell["_observation_rows"]]
        all_input_rows.extend(input_rows)
        qualified_usage = sum(_cell_value(cell, "usage_share") or 0.0 for cell in qualified)
        published_usage = sum(_cell_value(cell, "usage_share") or 0.0 for cell in visible)
        releases = sorted(
            {
                _dims(row).get("release_date", "")
                for row in input_rows
                if _dims(row).get("release_date", "")
            }
        )
        published_values = sorted(
            {str(row.get("published_at", "")) for row in input_rows if row.get("published_at")}
        )
        period_rows.append(
            {
                "period": period,
                "grain": grain,
                "visible_count": len(visible),
                "qualified_count": len(qualified),
                "visible_production_rate_pct": 100 * len(qualified) / len(visible),
                "production_traffic_share_pct": qualified_usage,
                "published_usage_share_pct": published_usage,
                "conditional_production_traffic_share_pct": (
                    100 * qualified_usage / published_usage if published_usage else None
                ),
                "source_product": source_product,
                "threshold_version": PRODUCTION_THRESHOLD_VERSION,
                "derivation_version": PRODUCTION_DERIVATION_VERSION,
                "methodology_version": methodology,
                "quality_status": "accepted",
                "release_date": releases[-1] if releases else None,
                "published_at": published_values[-1] if published_values else None,
                "unit": "percent",
                "lineage": _production_lineage(input_rows),
            }
        )
    by_summary = {
        (row["period"], row["grain"], row["methodology_version"]): row for row in period_rows
    }
    for row in period_rows:
        previous = by_summary.get(
            (_previous_month(row["period"]) or "", row["grain"], row["methodology_version"])
        )
        if previous is None:
            row["visible_production_rate_change_pp"] = None
            row["production_traffic_share_change_pp"] = None
            row["change_status"] = (
                "insufficient_history" if not _previous_month(row["period"]) else "period_gap"
            )
        else:
            row["visible_production_rate_change_pp"] = (
                row["visible_production_rate_pct"] - previous["visible_production_rate_pct"]
            )
            row["production_traffic_share_change_pp"] = (
                row["production_traffic_share_pct"] - previous["production_traffic_share_pct"]
            )
            row["change_status"] = "ok"

    task_relations = products.structured.entity_relations(
        dataset_id=DATASET_ID,
        source_id=SOURCE_ID,
        relation_type="has_task",
        as_of=as_of,
        active_only=True,
        limit=1_000_000,
    )
    relations_by_task: dict[str, list[dict]] = defaultdict(list)
    relations_by_occupation: dict[str, list[dict]] = defaultdict(list)
    for relation in task_relations:
        relations_by_task[relation["child_entity_id"]].append(relation)
        relations_by_occupation[relation["parent_entity_id"]].append(relation)
    coverage_diagnostics, occupation_task_coverage = [], []
    distribution_by_period: dict[str, dict] = {}
    latest_period = max((row["period"] for row in period_rows), default="")
    for period in selected_periods:
        task_cells = [
            cell for cell in cells if cell["period"] == period and cell["grain"] == "task"
        ]
        visible_tasks = {cell["entity_id"] for cell in task_cells if cell["visible"]}
        qualified_tasks = {cell["entity_id"] for cell in task_cells if cell["qualified"]}
        mapped = visible_tasks & set(relations_by_task)
        qmapped = qualified_tasks & set(relations_by_task)
        unmapped = visible_tasks - set(relations_by_task)
        qunmapped = qualified_tasks - set(relations_by_task)
        relation_ids = [
            relation["relation_id"] for task in mapped for relation in relations_by_task[task]
        ]
        taxonomy_versions = sorted(
            {relation.get("source_version", "") for relation in task_relations}
        )
        taxonomy_version = (
            taxonomy_versions[0] if len(taxonomy_versions) == 1 else "mixed_taxonomy_versions"
        )
        diagnostic = {
            "period": period,
            "visible_task_count": len(visible_tasks),
            "mapped_task_count": len(mapped),
            "unmapped_task_count": len(unmapped),
            "qualified_mapped_task_count": len(qmapped),
            "qualified_unmapped_task_count": len(qunmapped),
            "mapping_coverage_pct": 100 * len(mapped) / len(visible_tasks)
            if visible_tasks
            else None,
            "representative_unmapped_task_ids": sorted(unmapped)[:20],
            "taxonomy_version": taxonomy_version,
            "quality_status": "accepted"
            if not unmapped and len(taxonomy_versions) <= 1
            else "warning",
            "lower_bound_impact": "unmapped qualified tasks cannot be allocated to an occupation.",
            "lineage": _production_lineage(
                [row for cell in task_cells for row in cell["_observation_rows"]], relation_ids
            ),
        }
        coverage_diagnostics.append(diagnostic)
        eligible_rows = []
        for occupation_id, relations in sorted(relations_by_occupation.items()):
            task_ids = sorted({relation["child_entity_id"] for relation in relations})
            if not task_ids:
                continue
            qualified_here = sorted(set(task_ids) & qualified_tasks)
            relation_ids = sorted({relation["relation_id"] for relation in relations})
            observed_inputs = [
                row
                for task in qualified_here
                for row in (
                    cells_by_key.get(
                        (
                            period,
                            "task",
                            next(
                                (
                                    cell["methodology_version"]
                                    for cell in task_cells
                                    if cell["entity_id"] == task
                                ),
                                "",
                            ),
                            task,
                        ),
                        {},
                    ).get("_observation_rows", [])
                )
            ]
            entity = entities.get(occupation_id, {})
            canonical_name = str(entity.get("canonical_name") or "")
            canonical_is_code = bool(re.fullmatch(r"\d{2}-\d{4}(?:\.\d{2})?", canonical_name))
            if canonical_name and canonical_name != occupation_id and not canonical_is_code:
                occupation_name, occupation_name_status = canonical_name, "taxonomy_title"
            elif (
                occupation_names.get(occupation_id)
                and occupation_names[occupation_id] != occupation_id
            ):
                occupation_name, occupation_name_status = (
                    occupation_names[occupation_id],
                    "monthly_cell_name",
                )
            else:
                occupation_name, occupation_name_status = occupation_id, "stable_id_only"
            coverage = 100 * len(qualified_here) / len(task_ids)
            item = {
                "occupation_id": occupation_id,
                "occupation_name": occupation_name,
                "occupation_name_status": occupation_name_status,
                "period": period,
                "qualified_mapped_task_count": len(qualified_here),
                "taxonomy_task_count": len(task_ids),
                "coverage_lower_bound_pct": coverage,
                # Compatibility field is intentionally retained for stored/query
                # consumers; reader-facing products call this confirmed coverage.
                "confirmed_production_task_coverage_pct": coverage,
                "display_name": "职业任务组合的已确认生产化覆盖",
                "taxonomy_version": taxonomy_version,
                "threshold_version": PRODUCTION_THRESHOLD_VERSION,
                "derivation_version": PRODUCTION_DERIVATION_VERSION,
                "quality_status": "warning" if qunmapped or unmapped else "accepted",
                "lineage": _production_lineage(observed_inputs, relation_ids),
            }
            occupation_task_coverage.append(item)
            eligible_rows.append(item)
        points = []
        eligible_count = len(eligible_rows)
        for threshold in sorted({item["coverage_lower_bound_pct"] for item in eligible_rows}):
            count = sum(item["coverage_lower_bound_pct"] >= threshold for item in eligible_rows)
            points.append(
                {
                    "minimum_coverage_pct": threshold,
                    "occupation_count": count,
                    "eligible_occupation_count": eligible_count,
                    "occupation_share_pct": 100 * count / eligible_count
                    if eligible_count
                    else None,
                    "period": period,
                    "taxonomy_version": taxonomy_version,
                    "point_type": "curve",
                }
            )
        landmarks = []
        for threshold in (10.0, 25.0, 50.0, 75.0, 100.0):
            count = sum(item["coverage_lower_bound_pct"] >= threshold for item in eligible_rows)
            landmarks.append(
                {
                    "minimum_coverage_pct": threshold,
                    "occupation_count": count,
                    "eligible_occupation_count": eligible_count,
                    "occupation_share_pct": 100 * count / eligible_count
                    if eligible_count
                    else None,
                    "period": period,
                    "taxonomy_version": taxonomy_version,
                    "point_type": "landmark",
                }
            )
        distribution_by_period[period] = {
            "points": points,
            "landmarks": landmarks,
            "eligible_occupation_count": eligible_count,
            "taxonomy_version": taxonomy_version,
            "quality_status": "warning" if unmapped else "accepted",
        }
    latest_cells = [cell for cell in cells if cell["period"] == latest_period and cell["qualified"]]
    top_results = {}
    for grain, output_key in (("occupation", "top_occupations"), ("task", "top_tasks")):
        ranked = sorted(
            (cell for cell in latest_cells if cell["grain"] == grain),
            key=lambda cell: (-(_cell_value(cell, "usage_share") or 0.0), cell["entity_id"]),
        )[:top_n]
        items = []
        for rank, cell in enumerate(ranked, start=1):
            prior = next(
                (
                    candidate
                    for candidate in cells
                    if candidate["entity_id"] == cell["entity_id"]
                    and candidate["grain"] == grain
                    and candidate["period"] == _previous_month(latest_period)
                    and candidate["methodology_version"] == cell["methodology_version"]
                ),
                None,
            )
            changes, change_status = _production_change(cell, prior)
            entity = entities.get(cell["entity_id"], {})
            items.append(
                {
                    "rank": rank,
                    "entity_id": cell["entity_id"],
                    "entity_name": entity.get("canonical_name", cell["entity_name"]),
                    "period": latest_period,
                    "usage_share_pct": _cell_value(cell, "usage_share"),
                    "work_use_share_pct": _cell_value(cell, "work_use_share"),
                    "automation_share_pct": _cell_value(cell, "automation_share"),
                    "directive_share_pct": _cell_value(cell, "directive_share"),
                    **changes,
                    "change_status": change_status,
                    "qualified": True,
                    "quality_status": cell["quality_status"],
                    "lineage": cell["lineage"],
                }
            )
        top_results[output_key] = items
    manifest = None
    if snapshot_consumer and all_input_rows:
        manifest = products.snapshot_manifest(
            consumer=snapshot_consumer,
            purpose=snapshot_purpose or f"ai_production_penetration:{latest_period}",
            as_of=as_of or datetime.now().astimezone(),
            rows=all_input_rows,
            metadata={
                "dataset_id": DATASET_ID,
                "claim_id": "ai_core_production_workflow_penetration",
                "source_product": source_product,
                "threshold_version": PRODUCTION_THRESHOLD_VERSION,
                "derivation_version": PRODUCTION_DERIVATION_VERSION,
                "relation_ids": sorted(
                    {
                        rid
                        for row in coverage_diagnostics
                        for rid in row["lineage"]["input_relation_ids"]
                    }
                ),
                "ordered_record_hash": _record_hash(period_rows),
            },
        )
    result = {
        "status": "ok" if period_rows else "no_coverage",
        "dataset_id": DATASET_ID,
        "source_id": SOURCE_ID,
        "source_product": source_product,
        "periods": selected_periods,
        "latest_period": latest_period,
        "as_of": as_of.isoformat() if as_of else None,
        "proxy": PRODUCTION_PROXY,
        "threshold_version": PRODUCTION_THRESHOLD_VERSION,
        "derivation_version": PRODUCTION_DERIVATION_VERSION,
        "core_series": PRODUCTION_CORE_SERIES,
        "period_rows": sorted(
            period_rows, key=lambda row: (row["period"], row["grain"], row["methodology_version"])
        ),
        "cells": [_public_cell(cell) for cell in cells],
        "cell_diagnostics": cell_diagnostics,
        "coverage_diagnostics": coverage_diagnostics,
        "occupation_task_coverage": sorted(
            occupation_task_coverage,
            key=lambda row: (row["period"], -row["coverage_lower_bound_pct"], row["occupation_id"]),
        ),
        "occupation_coverage_distribution": distribution_by_period.get(
            latest_period,
            {
                "points": [],
                "landmarks": [],
                "eligible_occupation_count": 0,
                "quality_status": "no_coverage",
            },
        ),
        **top_results,
        "semantic_boundary": {
            "usage_share": "1P API product traffic share, not employee or firm adoption.",
            "qualified": "A user-defined production-workflow proxy, not confirmed continuous deployment.",
            "coverage": "Taxonomy-dependent lower bound; unpublished or privacy-filtered tasks are not zero.",
        },
        "manifest": manifest,
    }
    result["ordered_record_hash"] = _record_hash(
        {
            key: result[key]
            for key in (
                "period_rows",
                "top_occupations",
                "top_tasks",
                "coverage_diagnostics",
                "occupation_task_coverage",
            )
        }
    )
    if as_frame:
        result["frames"] = _production_frames(result)
    return result


def _monthly_rows(
    products, *, source_product: str, period: str | None = None, as_of: datetime | None = None
) -> list[dict]:
    rows = products.structured.observations(
        dataset_id=DATASET_ID,
        source_id=SOURCE_ID,
        as_of=as_of,
        latest_only=True,
        accepted_only=True,
        limit=1_000_000,
    )
    return [
        row
        for row in rows
        if row.get("period_basis") == "calendar_month"
        and _dims(row).get("source_product") == source_product
        and (period is None or row.get("period") == period)
    ]


def _production_monthly_rows(products, *, as_of: datetime | None = None) -> list[dict]:
    """Fetch only the four proxy inputs; the release has many unrelated metrics."""
    rows = []
    for metric in (USAGE, WORK_USE, AUTOMATION, DIRECTIVE):
        rows.extend(
            products.structured.observations(
                dataset_id=DATASET_ID,
                source_id=SOURCE_ID,
                metric_id=metric,
                as_of=as_of,
                latest_only=True,
                accepted_only=True,
                limit=1_000_000,
            )
        )
    return [
        row
        for row in rows
        if row.get("period_basis") == "calendar_month"
        and _dims(row).get("source_product") == PRODUCTION_SOURCE_PRODUCT
    ]


def _metric_index(rows: list[dict]) -> dict[tuple[str, str], dict]:
    return {(row["entity_id"], row["metric_id"]): row for row in rows}


def _job_rows(rows: list[dict], *, hierarchy: int) -> list[dict]:
    return [
        row
        for row in rows
        if _dims(row).get("classification") == "soc_occupation"
        and _dims(row).get("hierarchy_level") == hierarchy
    ]


def _metric_bundle(index: dict[tuple[str, str], dict], entity_id: str) -> dict:
    return {
        metric: index[(entity_id, metric)]
        for metric in {
            USAGE,
            AUTOMATION,
            AUGMENTATION,
            *COLLABORATION_METRICS,
            "ai.work_adoption.work_use_share",
            "ai.work_adoption.autonomy_mean",
        }
        if (entity_id, metric) in index
    }


def _metric_values(bundle: dict) -> dict:
    return {metric: row["value"] for metric, row in bundle.items()}


def _change(current: dict | None, previous: dict | None) -> dict:
    if not current:
        return {"value": None, "status": "missing_current", "lineage": _lineage(previous)}
    if not previous:
        return {"value": None, "status": "insufficient_history", "lineage": _lineage(current)}
    current_dims, previous_dims = _dims(current), _dims(previous)
    if current_dims.get("methodology_version") != previous_dims.get("methodology_version"):
        return {
            "value": None,
            "status": "methodology_changed",
            "lineage": _lineage(current, previous),
        }
    return {
        "value": round(current["value"] - previous["value"], 10),
        "unit": "percentage_points",
        "status": "ok",
        "lineage": _lineage(current, previous),
    }


def _job_share_of_major_group(usage: dict, index: dict[tuple[str, str], dict]) -> dict:
    major_code = _dims(usage).get("soc_major_group_code", "")
    major = index.get((f"SOC:{major_code}", USAGE)) if major_code else None
    if major is None or not major.get("value"):
        return {
            "value": None,
            "status": "major_group_not_published",
            "lineage": _lineage(usage, major),
        }
    return {
        "value": usage["value"] / major["value"],
        "unit": "ratio_0_1",
        "status": "ok",
        "warning": "Provider privacy filtering can prevent complete job summation.",
        "lineage": _lineage(usage, major),
    }


def _task_structure(
    products,
    *,
    occupation: str,
    source_product: str,
    period: str,
    as_of: datetime | None,
    monthly_rows: list[dict] | None = None,
    monthly_by_entity: dict[str, list[dict]] | None = None,
) -> tuple[list[dict], dict]:
    relations = products.structured.entity_relations(
        dataset_id=DATASET_ID,
        source_id=SOURCE_ID,
        parent_entity_id=occupation,
        relation_type="has_task",
        as_of=as_of,
        active_only=True,
        limit=100_000,
    )
    tasks = {relation["child_entity_id"]: relation for relation in relations}
    rows = (
        monthly_rows
        if monthly_rows is not None
        else _monthly_rows(products, source_product=source_product, period=period, as_of=as_of)
    )
    if monthly_by_entity is None:
        monthly_by_entity = defaultdict(list)
        for row in rows:
            monthly_by_entity[row["entity_id"]].append(row)
    by_task = {
        entity_id: [
            row
            for row in monthly_by_entity.get(entity_id, [])
            if _dims(row).get("classification") == "onet"
        ]
        for entity_id in tasks
    }
    task_items = []
    buckets = {"mostly_automated": 0, "mostly_augmented": 0, "balanced": 0, "unobserved": 0}
    for entity_id, relation in sorted(tasks.items()):
        index = _metric_index(by_task.get(entity_id, []))
        metrics = _metric_bundle(index, entity_id)
        automation, augmentation = metrics.get(AUTOMATION), metrics.get(AUGMENTATION)
        if not by_task.get(entity_id):
            bucket, status = "unobserved", "not_published_or_privacy_filtered"
        elif automation and augmentation:
            bucket = (
                "mostly_automated"
                if automation["value"] > augmentation["value"]
                else "mostly_augmented"
                if augmentation["value"] > automation["value"]
                else "balanced"
            )
            status = "observed"
        else:
            bucket, status = "unobserved", "metrics_incomplete"
        buckets[bucket] += 1
        task_items.append(
            {
                "entity_id": entity_id,
                "relation": relation,
                "metrics": _metric_values(metrics),
                "status": status,
                "bucket": bucket,
                "lineage": _lineage(*metrics.values(), relation_ids=[relation["relation_id"]]),
            }
        )
    total = len(tasks)
    observed = total - buckets["unobserved"]
    summary = {
        "observed_task_coverage": observed / total if total else None,
        "mostly_automated_task_share": buckets["mostly_automated"] / observed if observed else None,
        "mostly_augmented_task_share": buckets["mostly_augmented"] / observed if observed else None,
        "balanced_task_share": buckets["balanced"] / observed if observed else None,
        "unobserved_task_share": buckets["unobserved"] / total if total else None,
        "task_count": total,
        "observed_task_count": observed,
        "definition": "Visible AEI task-cell proxy; not employee adoption.",
        "lineage_relation_ids": [relation["relation_id"] for relation in relations],
    }
    return task_items, summary


def snapshot(
    products,
    *,
    source_product: str,
    period: str = "",
    as_of: datetime | None = None,
    snapshot_consumer: str = "",
    snapshot_purpose: str = "",
) -> dict:
    if source_product not in {"claude_ai", "1p_api"}:
        raise ValueError("source_product must be claude_ai or 1p_api")
    available = _monthly_rows(products, source_product=source_product, as_of=as_of)
    if not period:
        period = max((row["period"] for row in available), default="")
    rows = [row for row in available if row.get("period") == period]
    rows_by_entity: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        rows_by_entity[row["entity_id"]].append(row)
    previous_period = _previous_month(period)
    previous_rows = (
        [row for row in available if row.get("period") == previous_period]
        if previous_period
        else []
    )
    prior_year_period = _previous_year_month(period)
    prior_year_rows = [row for row in available if row.get("period") == prior_year_period]
    index, previous_index, prior_year_index = (
        _metric_index(rows),
        _metric_index(previous_rows),
        _metric_index(prior_year_rows),
    )
    jobs = []
    for usage in sorted(
        (row for row in _job_rows(rows, hierarchy=0) if row["metric_id"] == USAGE),
        key=lambda row: (-row["value"], row["entity_id"]),
    ):
        entity_id = usage["entity_id"]
        bundle = _metric_bundle(index, entity_id)
        major = _dims(usage).get("soc_major_group_code", "")
        major_id = f"SOC:{major}" if major else ""
        automation = bundle.get(AUTOMATION)
        task_items, task_summary = _task_structure(
            products,
            occupation=entity_id,
            source_product=source_product,
            period=period,
            as_of=as_of,
            monthly_rows=rows,
            monthly_by_entity=rows_by_entity,
        )
        jobs.append(
            {
                "entity_id": entity_id,
                "name": _dims(usage).get("node_name", entity_id),
                "soc_code": _dims(usage).get("soc_code", ""),
                "major_group_entity_id": major_id,
                "metrics": _metric_values(bundle),
                "usage_share_change_pp": _change(usage, previous_index.get((entity_id, USAGE))),
                "automation_share_change_pp": _change(
                    automation, previous_index.get((entity_id, AUTOMATION))
                ),
                "usage_share_yoy_pp": _change(usage, prior_year_index.get((entity_id, USAGE))),
                "collaboration_share_change_pp": {
                    metric: _change(bundle.get(metric), previous_index.get((entity_id, metric)))
                    for metric in COLLABORATION_METRICS
                    if metric in bundle
                },
                "job_share_of_major_group": _job_share_of_major_group(usage, index),
                "automated_usage_share": (
                    {
                        "value": usage["value"] * automation["value"] / 100,
                        "lineage": _lineage(usage, automation),
                    }
                    if automation
                    else None
                ),
                "task_structure": task_summary,
                "lineage": _lineage(
                    *bundle.values(), relation_ids=task_summary["lineage_relation_ids"]
                ),
                "task_count_visible": len(task_items),
            }
        )
    prior_usage_rows = [
        row for row in _job_rows(previous_rows, hierarchy=0) if row["metric_id"] == USAGE
    ]
    prior_order = [
        row["entity_id"]
        for row in sorted(prior_usage_rows, key=lambda row: (-row["value"], row["entity_id"]))
    ]
    prior_ranks = {entity_id: rank for rank, entity_id in enumerate(prior_order, start=1)}
    for rank, job in enumerate(jobs, start=1):
        job["usage_rank"] = rank
        previous_rank = prior_ranks.get(job["entity_id"])
        job["usage_rank_change"] = rank - previous_rank if previous_rank is not None else None
    major_groups = []
    for usage in sorted(
        (row for row in _job_rows(rows, hierarchy=1) if row["metric_id"] == USAGE),
        key=lambda row: (-row["value"], row["entity_id"]),
    ):
        bundle = _metric_bundle(index, usage["entity_id"])
        major_groups.append(
            {
                "entity_id": usage["entity_id"],
                "name": _dims(usage).get("node_name", usage["entity_id"]),
                "metrics": _metric_values(bundle),
                "provider_level": 1,
                "lineage": _lineage(*bundle.values()),
            }
        )
    output = {
        "status": "ok" if rows else "no_coverage",
        "dataset_id": DATASET_ID,
        "source_id": SOURCE_ID,
        "source_product": source_product,
        "period": period,
        "as_of": as_of.isoformat() if as_of else None,
        "semantic_boundary": {
            "usage_share": "Share of Claude product usage, not worker adoption.",
            "industry": "SOC occupational major group, not NAICS/GICS industry.",
        },
        "major_groups": major_groups,
        "jobs": jobs,
        "coverage": {
            "observation_count": len(rows),
            "job_count": len(jobs),
            "methodology_versions": sorted(
                {_dims(row).get("methodology_version", "") for row in rows}
            ),
        },
    }
    if snapshot_consumer and rows:
        output["manifest"] = products.snapshot_manifest(
            consumer=snapshot_consumer,
            purpose=snapshot_purpose or f"ai_work_adoption:{source_product}:{period}",
            as_of=as_of or datetime.now().astimezone(),
            rows=rows,
            metadata={
                "dataset_id": DATASET_ID,
                "source_product": source_product,
                "period": period,
                "relation_ids": sorted(
                    {rid for job in jobs for rid in job["task_structure"]["lineage_relation_ids"]}
                ),
                "derivation_version": DERIVATION_VERSION,
            },
        )
    return output


def metric_series(
    products,
    *,
    entity_id: str,
    metric_id: str,
    source_product: str,
    as_of: datetime | None = None,
    as_frame: bool = False,
):
    """Product-isolated monthly series; callers cannot accidentally mix Claude products."""
    if source_product not in {"claude_ai", "1p_api"}:
        raise ValueError("source_product must be claude_ai or 1p_api")
    rows = [
        row
        for row in _monthly_rows(products, source_product=source_product, as_of=as_of)
        if row["entity_id"] == entity_id.upper() and row["metric_id"] == metric_id
    ]
    result = {
        "status": "ok" if rows else "no_coverage",
        "source_product": source_product,
        "entity_id": entity_id.upper(),
        "metric_id": metric_id,
        "rows": rows,
    }
    if not as_frame:
        return result
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required for as_frame=True") from exc
    return pd.DataFrame(rows)


def cross_section(
    products,
    *,
    metric_id: str,
    source_product: str,
    period: str,
    hierarchy_level: int = 0,
    as_of: datetime | None = None,
) -> dict:
    if source_product not in {"claude_ai", "1p_api"}:
        raise ValueError("source_product must be claude_ai or 1p_api")
    rows = [
        row
        for row in _monthly_rows(
            products, source_product=source_product, period=period, as_of=as_of
        )
        if row["metric_id"] == metric_id
        and _dims(row).get("classification") == "soc_occupation"
        and _dims(row).get("hierarchy_level") == hierarchy_level
    ]
    return {
        "status": "ok" if rows else "no_coverage",
        "source_product": source_product,
        "period": period,
        "metric_id": metric_id,
        "hierarchy_level": hierarchy_level,
        "rows": sorted(rows, key=lambda row: (-row["value"], row["entity_id"])),
    }


def job_profile(
    products, *, occupation: str, source_product: str, period: str, as_of: datetime | None = None
) -> dict:
    if source_product not in {"claude_ai", "1p_api"}:
        raise ValueError("source_product must be claude_ai or 1p_api")
    occupation_id = (
        occupation.upper() if occupation.upper().startswith("SOC:") else f"SOC:{occupation.upper()}"
    )
    rows = _monthly_rows(products, source_product=source_product, period=period, as_of=as_of)
    previous_rows = (
        _monthly_rows(
            products, source_product=source_product, period=_previous_month(period), as_of=as_of
        )
        if _previous_month(period)
        else []
    )
    index = _metric_index([row for row in rows if row["entity_id"] == occupation_id])
    previous_index = _metric_index(
        [row for row in previous_rows if row["entity_id"] == occupation_id]
    )
    bundle = _metric_bundle(index, occupation_id)
    relations = products.structured.entity_relations(
        dataset_id=DATASET_ID,
        source_id=SOURCE_ID,
        child_entity_id=occupation_id,
        relation_type="contains_occupation",
        as_of=as_of,
    )
    tasks, task_summary = _task_structure(
        products,
        occupation=occupation_id,
        source_product=source_product,
        period=period,
        as_of=as_of,
    )
    exposure = products.structured.observations(
        dataset_id=DATASET_ID,
        source_id=SOURCE_ID,
        entity_id=occupation_id,
        metric_id=EXPOSURE,
        as_of=as_of,
        latest_only=True,
        accepted_only=True,
        limit=1,
    )
    entity = next(
        (row for row in products.structured.entities() if row["entity_id"] == occupation_id), None
    )
    return {
        "status": "ok" if bundle or tasks else "no_coverage",
        "occupation": entity or {"entity_id": occupation_id},
        "source_product": source_product,
        "period": period,
        "as_of": as_of.isoformat() if as_of else None,
        "major_group": relations[0] if relations else None,
        "metrics": _metric_values(bundle),
        "changes": {
            "usage_share_change_pp": _change(
                bundle.get(USAGE), previous_index.get((occupation_id, USAGE))
            ),
            "automation_share_change_pp": _change(
                bundle.get(AUTOMATION), previous_index.get((occupation_id, AUTOMATION))
            ),
        },
        "tasks": tasks,
        "task_structure": task_summary,
        "observed_exposure": exposure[0] if exposure else None,
        "lineage": _lineage(
            *bundle.values(),
            *(exposure[:1]),
            relation_ids=[row["relation_id"] for row in relations]
            + task_summary["lineage_relation_ids"],
        ),
        "semantic_boundary": "Usage Share is Claude usage share, not occupation worker adoption.",
    }
