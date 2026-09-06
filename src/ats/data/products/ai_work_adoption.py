"""Read models and reproducible query-time derivations for Claude job adoption."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
import re
from typing import Any


SOURCE_ID = "anthropic_economic_index"
DATASET_ID = "ai_work_adoption"
DERIVATION_VERSION = "ai_work_adoption/v1"
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
    return {"derivation_version": DERIVATION_VERSION, "input_observation_ids": observations,
            "input_relation_ids": relation_ids or []}


def _monthly_rows(products, *, source_product: str, period: str | None = None,
                  as_of: datetime | None = None) -> list[dict]:
    rows = products.structured.observations(
        dataset_id=DATASET_ID, source_id=SOURCE_ID, as_of=as_of,
        latest_only=True, accepted_only=True, limit=1_000_000)
    return [row for row in rows if row.get("period_basis") == "calendar_month"
            and _dims(row).get("source_product") == source_product
            and (period is None or row.get("period") == period)]


def _metric_index(rows: list[dict]) -> dict[tuple[str, str], dict]:
    return {(row["entity_id"], row["metric_id"]): row for row in rows}


def _job_rows(rows: list[dict], *, hierarchy: int) -> list[dict]:
    return [row for row in rows if _dims(row).get("classification") == "soc_occupation"
            and _dims(row).get("hierarchy_level") == hierarchy]


def _metric_bundle(index: dict[tuple[str, str], dict], entity_id: str) -> dict:
    return {metric: index[(entity_id, metric)] for metric in {
        USAGE, AUTOMATION, AUGMENTATION,
        *COLLABORATION_METRICS,
        "ai.work_adoption.work_use_share", "ai.work_adoption.autonomy_mean",
    } if (entity_id, metric) in index}


def _metric_values(bundle: dict) -> dict:
    return {metric: row["value"] for metric, row in bundle.items()}


def _change(current: dict | None, previous: dict | None) -> dict:
    if not current:
        return {"value": None, "status": "missing_current", "lineage": _lineage(previous)}
    if not previous:
        return {"value": None, "status": "insufficient_history", "lineage": _lineage(current)}
    current_dims, previous_dims = _dims(current), _dims(previous)
    if current_dims.get("methodology_version") != previous_dims.get("methodology_version"):
        return {"value": None, "status": "methodology_changed", "lineage": _lineage(current, previous)}
    return {"value": round(current["value"] - previous["value"], 10), "unit": "percentage_points",
            "status": "ok", "lineage": _lineage(current, previous)}


def _job_share_of_major_group(usage: dict, index: dict[tuple[str, str], dict]) -> dict:
    major_code = _dims(usage).get("soc_major_group_code", "")
    major = index.get((f"SOC:{major_code}", USAGE)) if major_code else None
    if major is None or not major.get("value"):
        return {"value": None, "status": "major_group_not_published", "lineage": _lineage(usage, major)}
    return {"value": usage["value"] / major["value"], "unit": "ratio_0_1",
            "status": "ok", "warning": "Provider privacy filtering can prevent complete job summation.",
            "lineage": _lineage(usage, major)}


def _task_structure(products, *, occupation: str, source_product: str, period: str,
                    as_of: datetime | None, monthly_rows: list[dict] | None = None,
                    monthly_by_entity: dict[str, list[dict]] | None = None) -> tuple[list[dict], dict]:
    relations = products.structured.entity_relations(
        dataset_id=DATASET_ID, source_id=SOURCE_ID, parent_entity_id=occupation,
        relation_type="has_task", as_of=as_of, active_only=True, limit=100_000)
    tasks = {relation["child_entity_id"]: relation for relation in relations}
    rows = monthly_rows if monthly_rows is not None else _monthly_rows(
        products, source_product=source_product, period=period, as_of=as_of)
    if monthly_by_entity is None:
        monthly_by_entity = defaultdict(list)
        for row in rows:
            monthly_by_entity[row["entity_id"]].append(row)
    by_task = {
        entity_id: [row for row in monthly_by_entity.get(entity_id, [])
                    if _dims(row).get("classification") == "onet"]
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
            bucket = ("mostly_automated" if automation["value"] > augmentation["value"]
                      else "mostly_augmented" if augmentation["value"] > automation["value"]
                      else "balanced")
            status = "observed"
        else:
            bucket, status = "unobserved", "metrics_incomplete"
        buckets[bucket] += 1
        task_items.append({"entity_id": entity_id, "relation": relation,
                           "metrics": _metric_values(metrics), "status": status, "bucket": bucket,
                           "lineage": _lineage(*metrics.values(), relation_ids=[relation["relation_id"]])})
    total = len(tasks)
    observed = total - buckets["unobserved"]
    summary = {
        "observed_task_coverage": observed / total if total else None,
        "mostly_automated_task_share": buckets["mostly_automated"] / observed if observed else None,
        "mostly_augmented_task_share": buckets["mostly_augmented"] / observed if observed else None,
        "balanced_task_share": buckets["balanced"] / observed if observed else None,
        "unobserved_task_share": buckets["unobserved"] / total if total else None,
        "task_count": total, "observed_task_count": observed,
        "definition": "Visible AEI task-cell proxy; not employee adoption.",
        "lineage_relation_ids": [relation["relation_id"] for relation in relations],
    }
    return task_items, summary


def snapshot(products, *, source_product: str, period: str = "", as_of: datetime | None = None,
             snapshot_consumer: str = "", snapshot_purpose: str = "") -> dict:
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
    previous_rows = [row for row in available if row.get("period") == previous_period] if previous_period else []
    prior_year_period = _previous_year_month(period)
    prior_year_rows = [row for row in available if row.get("period") == prior_year_period]
    index, previous_index, prior_year_index = _metric_index(rows), _metric_index(previous_rows), _metric_index(prior_year_rows)
    jobs = []
    for usage in sorted((row for row in _job_rows(rows, hierarchy=0) if row["metric_id"] == USAGE),
                        key=lambda row: (-row["value"], row["entity_id"])):
        entity_id = usage["entity_id"]
        bundle = _metric_bundle(index, entity_id)
        major = _dims(usage).get("soc_major_group_code", "")
        major_id = f"SOC:{major}" if major else ""
        automation = bundle.get(AUTOMATION)
        task_items, task_summary = _task_structure(products, occupation=entity_id,
                                                    source_product=source_product, period=period, as_of=as_of,
                                                    monthly_rows=rows, monthly_by_entity=rows_by_entity)
        jobs.append({"entity_id": entity_id, "name": _dims(usage).get("node_name", entity_id),
                     "soc_code": _dims(usage).get("soc_code", ""), "major_group_entity_id": major_id,
                     "metrics": _metric_values(bundle),
                     "usage_share_change_pp": _change(usage, previous_index.get((entity_id, USAGE))),
                     "automation_share_change_pp": _change(automation, previous_index.get((entity_id, AUTOMATION))),
                     "usage_share_yoy_pp": _change(usage, prior_year_index.get((entity_id, USAGE))),
                     "collaboration_share_change_pp": {
                         metric: _change(bundle.get(metric), previous_index.get((entity_id, metric)))
                         for metric in COLLABORATION_METRICS if metric in bundle},
                     "job_share_of_major_group": _job_share_of_major_group(usage, index),
                     "automated_usage_share": ({"value": usage["value"] * automation["value"] / 100,
                                                  "lineage": _lineage(usage, automation)} if automation else None),
                     "task_structure": task_summary,
                     "lineage": _lineage(*bundle.values(), relation_ids=task_summary["lineage_relation_ids"]),
                     "task_count_visible": len(task_items)})
    prior_usage_rows = [row for row in _job_rows(previous_rows, hierarchy=0) if row["metric_id"] == USAGE]
    prior_order = [row["entity_id"] for row in sorted(
        prior_usage_rows, key=lambda row: (-row["value"], row["entity_id"]))]
    prior_ranks = {entity_id: rank for rank, entity_id in enumerate(prior_order, start=1)}
    for rank, job in enumerate(jobs, start=1):
        job["usage_rank"] = rank
        previous_rank = prior_ranks.get(job["entity_id"])
        job["usage_rank_change"] = rank - previous_rank if previous_rank is not None else None
    major_groups = []
    for usage in sorted((row for row in _job_rows(rows, hierarchy=1) if row["metric_id"] == USAGE),
                        key=lambda row: (-row["value"], row["entity_id"])):
        bundle = _metric_bundle(index, usage["entity_id"])
        major_groups.append({"entity_id": usage["entity_id"], "name": _dims(usage).get("node_name", usage["entity_id"]),
                             "metrics": _metric_values(bundle), "provider_level": 1,
                             "lineage": _lineage(*bundle.values())})
    output = {
        "status": "ok" if rows else "no_coverage", "dataset_id": DATASET_ID,
        "source_id": SOURCE_ID, "source_product": source_product, "period": period,
        "as_of": as_of.isoformat() if as_of else None,
        "semantic_boundary": {"usage_share": "Share of Claude product usage, not worker adoption.",
                              "industry": "SOC occupational major group, not NAICS/GICS industry."},
        "major_groups": major_groups, "jobs": jobs,
        "coverage": {"observation_count": len(rows), "job_count": len(jobs),
                     "methodology_versions": sorted({_dims(row).get("methodology_version", "") for row in rows})},
    }
    if snapshot_consumer and rows:
        output["manifest"] = products.snapshot_manifest(
            consumer=snapshot_consumer, purpose=snapshot_purpose or f"ai_work_adoption:{source_product}:{period}",
            as_of=as_of or datetime.now().astimezone(), rows=rows,
            metadata={"dataset_id": DATASET_ID, "source_product": source_product, "period": period,
                      "relation_ids": sorted({rid for job in jobs for rid in job["task_structure"]["lineage_relation_ids"]}),
                      "derivation_version": DERIVATION_VERSION})
    return output


def metric_series(products, *, entity_id: str, metric_id: str, source_product: str,
                  as_of: datetime | None = None, as_frame: bool = False):
    """Product-isolated monthly series; callers cannot accidentally mix Claude products."""
    if source_product not in {"claude_ai", "1p_api"}:
        raise ValueError("source_product must be claude_ai or 1p_api")
    rows = [row for row in _monthly_rows(products, source_product=source_product, as_of=as_of)
            if row["entity_id"] == entity_id.upper() and row["metric_id"] == metric_id]
    result = {"status": "ok" if rows else "no_coverage", "source_product": source_product,
              "entity_id": entity_id.upper(), "metric_id": metric_id, "rows": rows}
    if not as_frame:
        return result
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required for as_frame=True") from exc
    return pd.DataFrame(rows)


def cross_section(products, *, metric_id: str, source_product: str, period: str,
                  hierarchy_level: int = 0, as_of: datetime | None = None) -> dict:
    if source_product not in {"claude_ai", "1p_api"}:
        raise ValueError("source_product must be claude_ai or 1p_api")
    rows = [row for row in _monthly_rows(products, source_product=source_product, period=period, as_of=as_of)
            if row["metric_id"] == metric_id and _dims(row).get("classification") == "soc_occupation"
            and _dims(row).get("hierarchy_level") == hierarchy_level]
    return {"status": "ok" if rows else "no_coverage", "source_product": source_product,
            "period": period, "metric_id": metric_id, "hierarchy_level": hierarchy_level,
            "rows": sorted(rows, key=lambda row: (-row["value"], row["entity_id"]))}


def job_profile(products, *, occupation: str, source_product: str, period: str,
                as_of: datetime | None = None) -> dict:
    if source_product not in {"claude_ai", "1p_api"}:
        raise ValueError("source_product must be claude_ai or 1p_api")
    occupation_id = occupation.upper() if occupation.upper().startswith("SOC:") else f"SOC:{occupation.upper()}"
    rows = _monthly_rows(products, source_product=source_product, period=period, as_of=as_of)
    previous_rows = _monthly_rows(products, source_product=source_product,
                                  period=_previous_month(period), as_of=as_of) if _previous_month(period) else []
    index = _metric_index([row for row in rows if row["entity_id"] == occupation_id])
    previous_index = _metric_index([row for row in previous_rows if row["entity_id"] == occupation_id])
    bundle = _metric_bundle(index, occupation_id)
    relations = products.structured.entity_relations(dataset_id=DATASET_ID, source_id=SOURCE_ID,
                                                     child_entity_id=occupation_id,
                                                     relation_type="contains_occupation", as_of=as_of)
    tasks, task_summary = _task_structure(products, occupation=occupation_id,
                                          source_product=source_product, period=period, as_of=as_of)
    exposure = products.structured.observations(dataset_id=DATASET_ID, source_id=SOURCE_ID,
                                                entity_id=occupation_id, metric_id=EXPOSURE,
                                                as_of=as_of, latest_only=True, accepted_only=True, limit=1)
    entity = next((row for row in products.structured.entities() if row["entity_id"] == occupation_id), None)
    return {
        "status": "ok" if bundle or tasks else "no_coverage", "occupation": entity or {"entity_id": occupation_id},
        "source_product": source_product, "period": period, "as_of": as_of.isoformat() if as_of else None,
        "major_group": relations[0] if relations else None,
        "metrics": _metric_values(bundle),
        "changes": {"usage_share_change_pp": _change(bundle.get(USAGE), previous_index.get((occupation_id, USAGE))),
                    "automation_share_change_pp": _change(bundle.get(AUTOMATION), previous_index.get((occupation_id, AUTOMATION)))},
        "tasks": tasks, "task_structure": task_summary,
        "observed_exposure": exposure[0] if exposure else None,
        "lineage": _lineage(*bundle.values(), *(exposure[:1]),
                            relation_ids=[row["relation_id"] for row in relations] + task_summary["lineage_relation_ids"]),
        "semantic_boundary": "Usage Share is Claude usage share, not occupation worker adoption.",
    }
