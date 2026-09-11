from datetime import datetime, timezone
import pytest

from ats.data.products.ai_adoption_bundle import build, _overall, _trend


NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _row(source, dataset, metric, period, value, suffix, regime="r1"):
    return {"observation_id": f"{source}-{suffix}", "artifact_id": f"a-{source}",
            "source_id": source, "dataset_id": dataset, "entity_id": "all",
            "metric_id": metric, "period": period, "value": value,
            "published_at": "2026-09-01T00:00:00+00:00",
            "known_at": "2026-09-02T00:00:00+00:00", "dimensions_json": "{}",
            "regime": regime}


class _Structured:
    def __init__(self, rows): self.rows = {row["observation_id"]: row for row in rows}
    def observation(self, observation_id): return self.rows.get(observation_id)


class _Products:
    def __init__(self, statuses=None):
        statuses = statuses or {}
        self.b = [_row("us_census_btos", "ai_enterprise_adoption_us", "current", str(100+i), v,
                       f"b{i}") for i, v in enumerate((10, 11, 12))]
        self.r = [_row("rps_genai_adoption", "ai_worker_adoption_us", "last", f"202{4+i}-Q2", v,
                       f"r{i}") for i, v in enumerate((30, 31, 32))]
        self.a = [_row("anthropic_economic_index", "ai_work_adoption", "usage", f"2026-0{4+i}", v,
                       f"a{i}") for i, v in enumerate((2, 3, 4))]
        self.statuses = statuses
        self.structured = _Structured(self.b + self.r + self.a)

    def census_btos_ai_snapshot(self, **kwargs):
        rows = [] if self.statuses.get("btos") == "missing" else self.b
        return {"source_id": "us_census_btos", "period": rows[-1]["period"] if rows else None,
                "current_use": rows[-1] if rows else None, "history": rows, "rows": rows,
                "methodology_regime": "any_business_function_v2", "freshness": {}}

    def rps_genai_adoption_snapshot(self, **kwargs):
        rows = self.r
        return {"source_id": "rps_genai_adoption", "period": rows[-1]["period"],
                "latest": {"last_week": rows[-1]}, "history": {"last_week": rows},
                "derivations": {}, "freshness": {}}

    def ons_bics_ai_snapshot(self, **kwargs):
        raise AssertionError("ONS must not be called by the L1 three-axis bundle")

    def ai_production_penetration(self, **kwargs):
        summaries = [{"period": row["period"], "grain": "task", "methodology_version": "r1",
                      "production_traffic_share_pct": row["value"],
                      "lineage": {"input_observation_ids": [row["observation_id"]]}}
                     for row in self.a]
        return {"source_id": "anthropic_economic_index", "latest_period": self.a[-1]["period"],
                "period_rows": summaries, "threshold_version": "t1", "derivation_version": "d1"}


def test_bundle_keeps_periods_independent_and_never_scores_sources():
    result = build(_Products(), as_of=NOW)
    assert result["periods_are_asynchronous"] is True
    assert result["no_forward_fill"] and result["no_interpolation"]
    assert "score" not in result and result["overall"]["status"] == "broadening_and_deepening"
    assert all(pair["allowed_use"] == "directional_context_only"
               for pair in result["comparability"]["pairs"])
    assert {axis["trend"]["status"] for axis in result["axes"].values()} == {"expanding"}


def test_bundle_handles_missing_source_without_calling_archived_ons_axis():
    result = build(_Products({"btos": "missing"}), as_of=NOW)
    assert result["axes"]["enterprise_breadth"]["trend"]["status"] == "unavailable"
    assert "organizational_embedding" not in result["axes"]
    assert result["overall"]["status"] == "insufficient_history"


def test_bundle_reports_directional_conflict_without_numeric_fusion():
    products = _Products()
    for index, row in enumerate(products.r): row["value"] = 32 - index
    result = build(products, as_of=NOW)
    assert result["axes"]["worker_persistence"]["trend"]["status"] == "contracting"
    assert result["overall"]["status"] == "mixed_evidence"
    assert result["comparability"]["numeric_comparison_allowed_pairs"] == []


def test_overall_rules_distinguish_breadth_provider_and_insufficient_history():
    def axes(states):
        return {key: {"trend": {"status": states.get(key, "insufficient_history")}}
                for key in ("enterprise_breadth", "worker_persistence", "task_production")}
    assert _overall(axes({"enterprise_breadth": "expanding"}))["status"] == "breadth_without_confirmed_depth"
    assert _overall(axes({"task_production": "expanding"}))["status"] == "provider_telemetry_only"
    assert _overall(axes({}))["status"] == "insufficient_history"


def test_medium_term_trend_tolerates_one_reversal_and_explains_rule():
    values = [(period, value, f"o{index}") for index, (period, value) in enumerate((
        ("2024-Q3", 28.2), ("2024-Q4", 26.4), ("2025-Q1", 29.3),
        ("2025-Q2", 30.5), ("2025-Q3", 32.0), ("2025-Q4", 35.2),
        ("2026-Q1", 37.8), ("2026-Q2", 39.2)))]
    result = _trend(values)
    assert result["status"] == "expanding"
    assert result["net_change_pp"] == pytest.approx(11.0)
    assert result["linear_slope_pp_per_period"] > 0
    assert result["direction_consistency"] >= .8
    assert any("描述性趋势=expanding" in step for step in result["steps"])
