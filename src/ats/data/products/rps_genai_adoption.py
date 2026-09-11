"""Governed RPS/FRED work-only GenAI adoption and persistence read model."""

from __future__ import annotations

import json
from typing import Any

SOURCE_ID = "rps_genai_adoption"
DATASET_ID = "ai_worker_adoption_us"
ENTITY_ID = "WORKER_POP:US:EMPLOYED_18_64"
DERIVATION_VERSION = "rps_genai_adoption/v1"

METRICS = {
    "adoption": "ai.worker_adoption.work_use_share",
    "last_week": "ai.worker_adoption.last_week_work_use_share",
    "daily": "ai.worker_adoption.daily_work_use_share",
    "assisted_hours": "ai.worker_adoption.assisted_work_hours",
    "time_saved": "ai.worker_adoption.time_saved_hours",
}


def _quarter_index(period: str) -> int:
    try:
        year, quarter = period.split("-Q")
        return int(year) * 4 + int(quarter) - 1
    except (ValueError, AttributeError):
        return -1


def _delta(current: dict[str, Any], previous: dict[str, Any], formula: str) -> dict[str, Any]:
    return {"value": float(current["value"]) - float(previous["value"]), "unit": "percentage_point",
            "formula": formula, "derivation_version": DERIVATION_VERSION,
            "input_observation_ids": [previous["observation_id"], current["observation_id"]]}


def snapshot(repository, *, as_of=None, period: str = "", include_vintages: bool = False) -> dict[str, Any]:
    rows = repository.observations(source_id=SOURCE_ID, dataset_id=DATASET_ID, entity_id=ENTITY_ID,
                                   as_of=as_of, latest_only=True, accepted_only=True, limit=10_000)
    vintages = (repository.observations(source_id=SOURCE_ID, dataset_id=DATASET_ID, entity_id=ENTITY_ID,
                                        as_of=as_of, latest_only=False, accepted_only=True, limit=10_000)
                if include_vintages else [])
    histories = {name: sorted((row for row in rows if row["metric_id"] == metric),
                              key=lambda row: _quarter_index(row["period"]))
                 for name, metric in METRICS.items()}
    latest_periods = {name: values[-1]["period"] if values else "" for name, values in histories.items()}
    available = sorted({row["period"] for row in rows}, key=_quarter_index)
    chosen = period or (available[-1] if available else "")
    latest = {name: next((row for row in reversed(values) if row["period"] == chosen), None)
              for name, values in histories.items()}
    ordered = all(latest[name] is not None for name in ("adoption", "last_week", "daily")) and (
        float(latest["daily"]["value"]) <= float(latest["last_week"]["value"])
        <= float(latest["adoption"]["value"]))

    derivations: dict[str, Any] = {"version": DERIVATION_VERSION}
    for numerator, output_name in (("last_week", "weekly_persistence_proxy"),
                                   ("daily", "daily_persistence_proxy")):
        left, adoption = latest[numerator], latest["adoption"]
        derivations[output_name] = ({
            "value": float(left["value"]) / float(adoption["value"]), "unit": "ratio_0_1",
            "label": "persistence_proxy_not_cohort_retention",
            "formula": f"{numerator}_work_use_share / work_adoption_share",
            "derivation_version": DERIVATION_VERSION,
            "input_observation_ids": [left["observation_id"], adoption["observation_id"]],
        } if ordered and adoption and float(adoption["value"]) > 0 else None)
    changes: dict[str, Any] = {}
    yoy: dict[str, Any] = {}
    for name, current in latest.items():
        if not current:
            changes[name] = yoy[name] = None
            continue
        history = histories[name]
        previous = next((row for row in reversed(history)
                         if _quarter_index(row["period"]) == _quarter_index(chosen) - 1), None)
        year_ago = next((row for row in reversed(history)
                         if _quarter_index(row["period"]) == _quarter_index(chosen) - 4), None)
        changes[name] = (_delta(current, previous, f"{name}(t) - {name}(t-1)") if previous else None)
        yoy[name] = (_delta(current, year_ago, f"{name}(t) - {name}(t-4)") if year_ago else None)
    derivations["quarter_change_pp"] = changes
    derivations["year_over_year_change_pp"] = yoy

    health = next((row for row in repository.source_health() if row["source_id"] == SOURCE_ID), {})
    observation_ids = sorted({row["observation_id"] for row in rows})
    notes = sorted({json.loads(row.get("dimensions_json") or "{}").get("notes", "") for row in rows} - {""})
    aligned = len({value for value in latest_periods.values() if value}) <= 1 and all(latest_periods.values())
    return {
        "status": "ok" if any(latest.values()) else "no_coverage", "source_id": SOURCE_ID,
        "dataset_id": DATASET_ID, "period": chosen or None, "available_periods": available,
        "statistical_unit": "US employed adults age 18-64",
        "denominator": "nationally representative employed adults age 18-64",
        "technology_scope": "self-reported generative AI use for work",
        "latest": latest, "history": histories, "vintages": vintages,
        "derivations": derivations,
        "series_alignment": {"aligned": bool(aligned), "latest_periods": latest_periods},
        "quality": {"status": "accepted" if ordered else "warning",
                    "daily_lte_last_week_lte_adoption": bool(ordered),
                    "persistence_derivations_available": bool(ordered)},
        "freshness": {"last_checked_at": health.get("last_checked_at"),
                      "latest_available_period": health.get("latest_available_period"),
                      "source_native_cadence": "quarterly"},
        "lineage": {"input_observation_ids": observation_ids,
                    "artifact_ids": sorted({row["artifact_id"] for row in rows})},
        "source_notes": notes,
        "agent_summary": {"period": chosen or None,
                          **{f"{name}_pct": (row["value"] if row else None) for name, row in latest.items()},
                          "weekly_persistence_proxy": (derivations["weekly_persistence_proxy"] or {}).get("value"),
                          "daily_persistence_proxy": (derivations["daily_persistence_proxy"] or {}).get("value")},
        "limitations": ["自报工作使用不等于企业批准、付费席位或正式部署",
                        "持续使用代理是总体比例之比，不是 cohort retention",
                        "自报节省工时是受访者反事实估计"],
    }
