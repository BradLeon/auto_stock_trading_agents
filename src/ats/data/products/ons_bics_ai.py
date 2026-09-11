"""Governed ONS BICS AI adoption and organizational-embedding snapshot."""

from __future__ import annotations

import json
import re
from typing import Any

SOURCE_ID = "ons_bics_ai"
DATASET_ID = "ai_enterprise_adoption_uk"

METRICS = {
    "adoption": "ai.uk_enterprise_adoption.use_share",
    "average_technologies": "ai.uk_enterprise_adoption.average_technologies",
    "extensive": "ai.uk_enterprise_adoption.extensive_use_share",
    "limited": "ai.uk_enterprise_adoption.limited_use_share",
    "pilot": "ai.uk_enterprise_adoption.pilot_use_share",
    "employee_daily_majority": "ai.uk_enterprise_adoption.employee_daily_use_share",
    "responses": "ai.uk_enterprise_adoption.response_share",
}


def _wave_key(period: str) -> int:
    match = re.search(r"(\d+)", str(period))
    return int(match.group(1)) if match else -1


def snapshot(repository, *, wave: str = "", as_of=None, include_vintages: bool = False) -> dict[str, Any]:
    rows = repository.observations(source_id=SOURCE_ID, dataset_id=DATASET_ID, as_of=as_of,
                                   latest_only=True, accepted_only=True, limit=100_000)
    vintages = (repository.observations(source_id=SOURCE_ID, dataset_id=DATASET_ID, as_of=as_of,
                                        latest_only=False, accepted_only=True, limit=100_000)
                if include_vintages else [])
    periods = sorted({row["period"] for row in rows}, key=_wave_key)
    chosen = wave if str(wave).startswith("wave-") else (f"wave-{wave}" if wave else (periods[-1] if periods else ""))
    selected = [row for row in rows if row["period"] == chosen]
    national = [row for row in selected if row["entity_id"] == "BUSINESS_POP:UK:ALL"]
    latest = {name: next((row for row in national if row["metric_id"] == metric), None)
              for name, metric in METRICS.items() if name != "responses"}
    histories = {name: sorted((row for row in rows if row["entity_id"] == "BUSINESS_POP:UK:ALL"
                               and row["metric_id"] == metric), key=lambda row: _wave_key(row["period"]))
                 for name, metric in METRICS.items() if name != "responses"}
    dims = [json.loads(row.get("dimensions_json") or "{}") for row in selected]
    regimes = sorted({item.get("question_regime", "") for item in dims} - {""})
    trend_status = {name: ("comparable_history" if len({row["period"] for row in history}) >= 2
                           and len({json.loads(row.get("dimensions_json") or "{}").get("question_regime", "")
                                    for row in history}) == 1 else "insufficient_history")
                    for name, history in histories.items()}
    health = next((row for row in repository.source_health() if row["source_id"] == SOURCE_ID), {})
    return {
        "status": "ok" if selected else "no_coverage", "source_id": SOURCE_ID, "dataset_id": DATASET_ID,
        "wave": chosen or None, "available_waves": periods, "latest": latest, "history": histories,
        "trend_status": trend_status, "vintages": vintages,
        "statistical_unit": "UK business", "geography_role": "UK_supplement",
        "coverage": {"observation_count": len(selected),
                     "suppressed_cells_are_missing_not_zero": True,
                     "entities": len({row["entity_id"] for row in selected})},
        "strata": {"national": national,
                   "industry": [row for row in selected if ":SIC:" in row["entity_id"]],
                   "employment_size": [row for row in selected if ":EMP:" in row["entity_id"]]},
        "question_regimes": regimes,
        "questionnaire_references": sorted({item.get("questionnaire_url", "") for item in dims} - {""}),
        "quality": {"status": "accepted" if selected else "no_coverage",
                    "conditional_denominators_explicit": all(item.get("denominator_scope") for item in dims),
                    "official_statistics_in_development": True},
        "freshness": {"last_checked_at": health.get("last_checked_at"),
                      "latest_available_period": health.get("latest_available_period"),
                      "source_native_cadence": "conditional_wave"},
        "lineage": {"input_observation_ids": sorted({row["observation_id"] for row in selected}),
                    "artifact_ids": sorted({row["artifact_id"] for row in selected})},
        "agent_summary": {"wave": chosen or None,
                          **{f"{name}_pct": (row["value"] if row else None) for name, row in latest.items()},
                          "history_status": trend_status},
        "limitations": ["英国补充证据，不与美国 BTOS 默认相减或合并",
                        "BICS 为自愿调查且部分行业不在覆盖范围",
                        "条件题以 AI 使用企业或题目指定 universe 为分母",
                        "单一同口径 wave 仅作为 snapshot，不外推趋势"],
    }
