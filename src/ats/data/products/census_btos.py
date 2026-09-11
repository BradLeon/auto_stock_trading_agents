"""Governed read model for Census BTOS Core AI adoption (new regime only)."""

from __future__ import annotations

import json
from typing import Any

DATASET_ID = "ai_enterprise_adoption_us"
SOURCE_ID = "us_census_btos"
CURRENT = "ai.enterprise_adoption.current_use_share"
EXPECTED = "ai.enterprise_adoption.expected_use_share"
CURRENT_SE = "ai.enterprise_adoption.current_use_standard_error"
DERIVATION_VERSION = "census_btos/v1"


def _period_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if str(value).isdigit() else (1, str(value))


def snapshot(repository, *, period: str = "", as_of=None, entity_id: str = "",
             include_vintages: bool = False) -> dict[str, Any]:
    rows = repository.observations(source_id=SOURCE_ID, dataset_id=DATASET_ID, entity_id=entity_id or None,
                                   as_of=as_of, latest_only=True,
                                   accepted_only=True, limit=100_000)
    vintage_rows = (repository.observations(source_id=SOURCE_ID, dataset_id=DATASET_ID,
                                            entity_id=entity_id or None, as_of=as_of,
                                            latest_only=False, accepted_only=True, limit=100_000)
                    if include_vintages else [])
    periods = sorted({row["period"] for row in rows}, key=_period_key)
    chosen = period or (periods[-1] if periods else "")
    selected = [row for row in rows if row["period"] == chosen]
    values = {row["metric_id"]: row for row in selected if row["entity_id"] == "BUSINESS_POP:US:ALL"
              and row["metric_id"] in {CURRENT, EXPECTED}}
    current_history = [row for row in rows if row["entity_id"] == "BUSINESS_POP:US:ALL" and row["metric_id"] == CURRENT]
    current_history.sort(key=lambda row: _period_key(row["period"]))
    se_by_period = {row["period"]: row for row in rows
                    if row["entity_id"] == "BUSINESS_POP:US:ALL" and row["metric_id"] == CURRENT_SE}
    for row in current_history:
        if row["period"] in se_by_period:
            row["standard_error"] = se_by_period[row["period"]]["value"]
            row["standard_error_observation_id"] = se_by_period[row["period"]]["observation_id"]
    previous = next((row for row in reversed(current_history)
                     if row["period"].isdigit() and chosen.isdigit()
                     and int(row["period"]) == int(chosen) - 1), None)
    current = values.get(CURRENT)
    latest_four = current_history[-4:]
    current_ids = [row["observation_id"] for row in latest_four]
    four_consecutive = (len(latest_four) == 4 and all(
        int(latest_four[index]["period"]) + 1 == int(latest_four[index + 1]["period"])
        for index in range(3)))
    derivations = {
        "version": DERIVATION_VERSION,
        "current_minus_expected_pp": ({"value": current["value"] - values[EXPECTED]["value"],
                                         "formula": "current_use_share - expected_use_share",
                                         "input_observation_ids": [current["observation_id"], values[EXPECTED]["observation_id"]]}
                                        if current and values.get(EXPECTED) else None),
        "period_change_pp": ({"value": current["value"] - previous["value"],
                               "formula": "current_use_share(t) - current_use_share(t-1)",
                               "input_observation_ids": [previous["observation_id"], current["observation_id"]]}
                              if current and previous else None),
        "four_period_moving_average": ({"value": sum(row["value"] for row in current_history[-4:]) / 4,
                                         "formula": "mean(current_use_share over latest 4 comparable BTOS periods)",
                                         "input_observation_ids": current_ids}
                                       if four_consecutive else None),
    }
    health = next((row for row in repository.source_health() if row["source_id"] == SOURCE_ID), {})
    strata = {"national": [], "industry": [], "employment_size": [], "sector_by_size": []}
    for row in selected:
        dims = json.loads(row.get("dimensions_json") or "{}")
        kind = dims.get("stratum_type", "national")
        bucket = ("sector_by_size" if "_by_" in kind else
                  "employment_size" if kind == "employment_size" else
                  "industry" if kind.startswith("naics") else "national")
        strata[bucket].append(row)
    observation_ids = sorted({row["observation_id"] for row in selected})
    return {"status": "ok" if selected else "no_coverage", "dataset_id": DATASET_ID,
            "source_id": SOURCE_ID, "period": chosen or None, "available_periods": periods,
            "statistical_unit": "US employer business", "denominator": "in-scope employer businesses",
            "methodology_regime": "any_business_function_v2", "current_use": values.get(CURRENT),
            "expected_use": values.get(EXPECTED),
            "derivations": derivations, "strata": strata, "history": current_history,
            "vintages": vintage_rows,
            "quality": {"status": "accepted" if selected else "no_coverage",
                        "suppressed_cells_are_missing_not_zero": True},
            "freshness": {"last_checked_at": health.get("last_checked_at"),
                          "latest_available_period": health.get("latest_available_period")},
            "lineage": {"input_observation_ids": observation_ids,
                        "artifact_ids": sorted({row["artifact_id"] for row in selected})},
            "agent_summary": {"period": chosen or None,
                              "current_use_pct": current["value"] if current else None,
                              "expected_use_pct": values.get(EXPECTED, {}).get("value"),
                              "current_minus_expected_pp": (derivations["current_minus_expected_pp"] or {}).get("value")},
            "rows": selected,
            "limitations": ["企业比例，不是员工、席位或任务采用率", "不含 2025-11-17 前旧口径",
                            "未进行统计显著性检验，期间变化仅为描述性百分点差"]}
