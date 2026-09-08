"""Hermetic contracts for the AI production-workflow proxy."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pandas as pd

from ats.agents.evidence.production_visualization import render_production_charts
from ats.agents.evidence.work_adoption import (
    _trend_status,
    observe_ai_production_penetration,
    render_ai_production_markdown,
)
from ats.data.products.ai_work_adoption import (
    AUTOMATION,
    DIRECTIVE,
    USAGE,
    WORK_USE,
    _production_cells,
)
from ats.data.products.base import DataProducts


def _row(
    metric,
    value,
    *,
    period="2026-05",
    entity="SOC:15-2031.00",
    product="1p_api",
    methodology="m1",
    observation_id=None,
):
    return {
        "observation_id": observation_id or f"{entity}:{metric}",
        "entity_id": entity,
        "metric_id": metric,
        "value": value,
        "unit": "percent",
        "period": period,
        "known_at": "2026-06-26T00:00:00+00:00",
        "fetched_at": "2026-06-26T00:00:00+00:00",
        "dimensions": {
            "source_product": product,
            "methodology_version": methodology,
            "classification": "soc_occupation",
            "hierarchy_level": 0,
            "node_name": "Operations Research Analysts",
        },
    }


class _FakeStructured:
    """Small governed fixture: observations and relations, never Provider/raw files."""

    def __init__(self, rows, relations):
        self.rows, self.relations = rows, relations

    def observations(self, **filters):
        rows = list(self.rows)
        if filters.get("metric_id"):
            rows = [row for row in rows if row["metric_id"] == filters["metric_id"]]
        as_of = filters.get("as_of")
        if as_of:
            boundary = as_of.isoformat()
            rows = [row for row in rows if row["known_at"] <= boundary]
        if filters.get("latest_only"):
            latest = {}
            for row in rows:
                key = (row["entity_id"], row["metric_id"], row["period"])
                if key not in latest or row["known_at"] > latest[key]["known_at"]:
                    latest[key] = row
            rows = list(latest.values())
        return rows

    def entity_relations(self, **filters):
        rows = list(self.relations)
        for key in ("parent_entity_id", "child_entity_id", "relation_type"):
            if filters.get(key):
                rows = [row for row in rows if row.get(key) == filters[key]]
        if filters.get("as_of"):
            boundary = filters["as_of"].isoformat()
            rows = [row for row in rows if row.get("known_at", "") <= boundary]
        return rows

    def entities(self):
        return [
            {"entity_id": "SOC:1", "canonical_name": "Qualified occupation"},
            {"entity_id": "SOC:2", "canonical_name": "Other occupation"},
            {"entity_id": "ONET_TASK:1", "canonical_name": "Qualified task"},
            {"entity_id": "ONET_TASK:2", "canonical_name": "Other task"},
        ]

    def create_snapshot(self, **kwargs):
        return {"snapshot_id": "fixture-snapshot", **kwargs}


def _governed_products():
    rows = []
    for period, factor in (("2026-04", 0.0), ("2026-05", 1.0)):
        for entity, classification, usage, directive in (
            ("SOC:1", "soc_occupation", 10 + factor, 90),
            ("SOC:2", "soc_occupation", 5, 40),
            ("ONET_TASK:1", "onet", 20 + factor, 90),
            ("ONET_TASK:2", "onet", 2, 40),
            ("ONET_TASK:UNMAPPED", "onet", 4, 90),
        ):
            for metric, value in (
                (USAGE, usage),
                (WORK_USE, 90),
                (AUTOMATION, 90),
                (DIRECTIVE, directive),
            ):
                row = _row(
                    metric,
                    value,
                    period=period,
                    entity=entity,
                    observation_id=f"{period}:{entity}:{metric}",
                )
                row["dimensions"]["classification"] = classification
                row["dimensions"]["node_name"] = entity
                row["dimensions"]["release_date"] = "2026-06-26"
                row["published_at"] = "2026-06-26T00:00:00+00:00"
                row["period_basis"] = "calendar_month"
                rows.append(row)
    relations = [
        {
            "relation_id": "r1",
            "parent_entity_id": "SOC:1",
            "child_entity_id": "ONET_TASK:1",
            "relation_type": "has_task",
            "source_version": "t1",
            "known_at": "2026-06-01T00:00:00+00:00",
        },
        {
            "relation_id": "r2",
            "parent_entity_id": "SOC:1",
            "child_entity_id": "ONET_TASK:2",
            "relation_type": "has_task",
            "source_version": "t1",
            "known_at": "2026-06-01T00:00:00+00:00",
        },
        {
            "relation_id": "r3",
            "parent_entity_id": "SOC:1",
            "child_entity_id": "ONET_TASK:3",
            "relation_type": "has_task",
            "source_version": "t1",
            "known_at": "2026-06-01T00:00:00+00:00",
        },
        {
            "relation_id": "r4",
            "parent_entity_id": "SOC:2",
            "child_entity_id": "ONET_TASK:1",
            "relation_type": "has_task",
            "source_version": "t1",
            "known_at": "2026-06-01T00:00:00+00:00",
        },
        {
            "relation_id": "r5",
            "parent_entity_id": "SOC:1",
            "child_entity_id": "ONET_TASK:UNMAPPED",
            "relation_type": "has_task",
            "source_version": "t2",
            "known_at": "2026-07-01T00:00:00+00:00",
        },
    ]
    return DataProducts(structured_repository=_FakeStructured(rows, relations))


def test_proxy_uses_exact_boundaries_and_fails_closed():
    rows = [_row(USAGE, 0.01), _row(WORK_USE, 80), _row(AUTOMATION, 80), _row(DIRECTIVE, 50)]
    cells, _diagnostics = _production_cells(rows)
    assert len(cells) == 1 and cells[0]["qualified"] is True
    rows[-1] = _row(DIRECTIVE, 49.99)
    cells, _diagnostics = _production_cells(rows)
    assert cells[0]["qualified"] is False
    assert "directive_share_below_threshold" in cells[0]["reason_codes"]
    cells, _diagnostics = _production_cells(rows[:-1])
    assert cells[0]["qualified"] is False
    assert any(code.startswith("missing_") for code in cells[0]["reason_codes"])


def test_proxy_does_not_join_different_methodology_or_products():
    rows = [
        _row(USAGE, 1),
        _row(WORK_USE, 90),
        _row(AUTOMATION, 90, methodology="m2"),
        _row(DIRECTIVE, 90),
    ]
    cells, _ = _production_cells(rows)
    assert len(cells) == 2
    assert not any(cell["qualified"] for cell in cells)


def test_proxy_rejects_invalid_and_conflicting_values_without_raising():
    rows = [_row(USAGE, 1), _row(WORK_USE, 90), _row(AUTOMATION, "bad"), _row(DIRECTIVE, 90)]
    cells, _ = _production_cells(rows)
    assert cells[0]["qualified"] is False
    assert "invalid_automation_share" in cells[0]["reason_codes"]

    rows = [
        _row(USAGE, 1),
        _row(USAGE, 2, observation_id="usage-revision"),
        _row(WORK_USE, 90),
        _row(AUTOMATION, 90),
        _row(DIRECTIVE, 90),
    ]
    cells, _ = _production_cells(rows)
    assert "conflicting_usage_share" in cells[0]["reason_codes"]


class _ProductionProducts:
    def ai_production_penetration(self, **kwargs):
        rows = []
        for period, occupation_breadth, task_breadth, occupation_depth, task_depth in (
            ("2026-04", 44.95, 32.08, 68.77, 59.40),
            ("2026-05", 46.67, 31.72, 75.36, 68.66),
        ):
            rows.extend(
                [
                    {
                        "period": period,
                        "grain": "occupation",
                        "visible_production_rate_pct": occupation_breadth,
                        "production_traffic_share_pct": occupation_depth,
                        "visible_count": 100,
                        "qualified_count": 45,
                        "methodology_version": "m1",
                    },
                    {
                        "period": period,
                        "grain": "task",
                        "visible_production_rate_pct": task_breadth,
                        "production_traffic_share_pct": task_depth,
                        "visible_count": 200,
                        "qualified_count": 64,
                        "methodology_version": "m1",
                    },
                ]
            )
        output = {
            "status": "ok",
            "periods": ["2026-04", "2026-05"],
            "latest_period": "2026-05",
            "period_rows": rows,
            "proxy": {"version": "core_production_workflow_proxy_v1"},
            "threshold_version": "core_production_workflow_proxy_v1",
            "derivation_version": "v1",
            "coverage_diagnostics": [
                {
                    "period": "2026-05",
                    "taxonomy_version": "t1",
                    "mapped_task_count": 10,
                    "unmapped_task_count": 1,
                    "mapping_coverage_pct": 90,
                }
            ],
            "top_occupations": [],
            "top_tasks": [],
            "occupation_task_coverage": [],
            "occupation_coverage_distribution": {"points": [], "landmarks": []},
            "manifest": {"snapshot_id": "s1"},
            "semantic_boundary": {},
        }
        if kwargs.get("as_frame"):
            output["frames"] = {}
        return output


def test_production_observer_is_history_conservative_and_has_a_domain_only_method_card():
    packet = observe_ai_production_penetration(products=_ProductionProducts())
    assert packet["overall_status"] == "insufficient_history"
    assert packet["monthly_comparison"]["breadth"] == "mixed"
    assert packet["monthly_comparison"]["depth"] == "increased"
    assert packet["methodology_card"]["source_product"] == "1p_api"
    assert "持续趋势" in " ".join(packet["warnings"])


def test_methodology_card_is_complete_and_renderer_warning_is_nonfatal(monkeypatch):
    monkeypatch.setattr(
        "ats.agents.evidence.production_visualization.render_production_charts",
        lambda **_kwargs: {"descriptors": [], "visualization_warning": "fixture renderer failed"},
    )
    packet = observe_ai_production_penetration(products=_ProductionProducts(), chart_dir="fixture")
    card = packet["methodology_card"]
    assert {
        "provider",
        "source_product",
        "periods",
        "visible_samples",
        "qualified_samples",
        "taxonomy",
        "threshold_version",
        "methodology_versions",
        "derivation_version",
        "history_status",
        "quality_status",
        "manifest_id",
        "limitations",
    } <= set(card)
    assert "fixture renderer failed" in packet["warnings"]
    assert "accuracy" not in card and "论文" not in json.dumps(card, ensure_ascii=False)


def test_review_markdown_orders_distribution_before_tail_disclosure_and_topn():
    packet = observe_ai_production_penetration(products=_ProductionProducts())
    packet["workflow_scope"] = {
        "sector": "ai_hardware",
        "sector_label": "AI硬件",
        "layer": "L1_app",
        "layer_label": "L1 AI应用层（Token经济）",
    }
    packet["high_coverage_occupations"] = [
        {
            "occupation_name": "Fixture occupation",
            "qualified_mapped_task_count": 5,
            "taxonomy_task_count": 9,
            "confirmed_production_task_coverage_pct": 55.555555,
        }
    ]
    markdown = render_ai_production_markdown(packet)
    assert markdown.index("## 方法卡") < markdown.index("## 命题状态与四项核心指标")
    assert (
        markdown.index("## 职业任务组合的已确认生产化覆盖分布")
        < markdown.index("### 分布尾部的完整披露：已确认覆盖至少 50% 的职业（最新月）")
        < markdown.index("## 高流量生产化职业 TOP10（典型使用单元）")
    )
    for name in (
        "职业可见单元生产化率",
        "任务可见单元生产化率",
        "职业生产化流量份额",
        "任务生产化流量份额",
    ):
        assert name in markdown
    assert "Fixture occupation" in markdown and "5|9|55.56%" in markdown
    assert "不声称存在可观测的“上限”" in markdown
    assert "这不是典型职业样本" in markdown
    assert "高流量使用的例子" in markdown
    assert "职业与任务是同一 1P API 产品流量的两种分类视角" in markdown


def test_trend_classifier_requires_three_continuous_comparable_months():
    assert (
        _trend_status([{"period": "2026-04", "x": 1}, {"period": "2026-05", "x": 2}], "x")
        == "insufficient_history"
    )
    assert (
        _trend_status(
            [
                {"period": "2026-04", "x": 1},
                {"period": "2026-05", "x": 2},
                {"period": "2026-06", "x": 3},
            ],
            "x",
        )
        == "directional_up"
    )
    assert (
        _trend_status(
            [
                {"period": "2026-04", "x": 3},
                {"period": "2026-05", "x": 2},
                {"period": "2026-06", "x": 1},
            ],
            "x",
        )
        == "directional_down"
    )
    assert (
        _trend_status(
            [
                {"period": "2026-04", "x": 1},
                {"period": "2026-05", "x": 1},
                {"period": "2026-06", "x": 1},
            ],
            "x",
        )
        == "flat"
    )
    assert (
        _trend_status(
            [
                {"period": "2026-04", "x": 1},
                {"period": "2026-05", "x": 3},
                {"period": "2026-06", "x": 2},
            ],
            "x",
        )
        == "mixed"
    )


def test_renderer_writes_deterministic_sidecar_and_nonfatal_failure(tmp_path):
    frames = {
        "summary": pd.DataFrame(
            [
                {
                    "period": "2026-04",
                    "grain": "occupation",
                    "methodology_version": "m1",
                    "visible_production_rate_pct": 44.95,
                    "production_traffic_share_pct": 68.77,
                },
                {
                    "period": "2026-05",
                    "grain": "occupation",
                    "methodology_version": "m1",
                    "visible_production_rate_pct": 46.67,
                    "production_traffic_share_pct": 75.36,
                },
                {
                    "period": "2026-04",
                    "grain": "task",
                    "methodology_version": "m1",
                    "visible_production_rate_pct": 32.08,
                    "production_traffic_share_pct": 59.40,
                },
                {
                    "period": "2026-05",
                    "grain": "task",
                    "methodology_version": "m1",
                    "visible_production_rate_pct": 31.72,
                    "production_traffic_share_pct": 68.66,
                },
            ]
        ),
        "occupation_coverage_distribution": pd.DataFrame(
            [
                {
                    "point_type": "curve",
                    "minimum_coverage_pct": 0.0,
                    "occupation_share_pct": 100.0,
                    "eligible_occupation_count": 4,
                    "period": "2026-05",
                    "taxonomy_version": "t1",
                },
                {
                    "point_type": "curve",
                    "minimum_coverage_pct": 50.0,
                    "occupation_share_pct": 25.0,
                    "eligible_occupation_count": 4,
                    "period": "2026-05",
                    "taxonomy_version": "t1",
                },
            ]
        ),
    }
    rendered = render_production_charts(
        frames=frames,
        output_dir=tmp_path,
        manifest={"snapshot_id": "s1"},
        metadata={"threshold_version": "v1"},
    )
    assert len(rendered["descriptors"]) == 2
    sidecar = json.loads((tmp_path / "ai_production_penetration.json").read_text())
    assert sidecar["chart_type"] == "monthly_comparison" and sidecar["manifest_id"] == "s1"
    assert sidecar["data_hash"] == rendered["descriptors"][0]["data_hash"]
    three = {
        **frames,
        "summary": pd.concat(
            [
                frames["summary"],
                pd.DataFrame(
                    [
                        {
                            "period": "2026-06",
                            "grain": "occupation",
                            "methodology_version": "m2",
                            "visible_production_rate_pct": 50.0,
                            "production_traffic_share_pct": 80.0,
                        },
                        {
                            "period": "2026-06",
                            "grain": "task",
                            "methodology_version": "m2",
                            "visible_production_rate_pct": 35.0,
                            "production_traffic_share_pct": 70.0,
                        },
                    ]
                ),
            ],
            ignore_index=True,
        ),
    }
    rendered_three = render_production_charts(
        frames=three, output_dir=tmp_path / "three", manifest=None, metadata={}
    )
    assert rendered_three["descriptors"][0]["chart_type"] == "time_series"
    failed = render_production_charts(
        frames=frames, output_dir="/dev/null/fail", manifest=None, metadata={}
    )
    assert failed["descriptors"] == [] and "visualization_warning" in failed


def test_governed_product_aggregates_lineage_taxonomy_and_dataframes():
    result = _governed_products().ai_production_penetration(
        periods=["2026-04", "2026-05"],
        top_n=10,
        as_frame=True,
        snapshot_consumer="test",
        snapshot_purpose="fixture",
        as_of=datetime(2026, 6, 26, tzinfo=UTC),
    )
    may = {(row["grain"]): row for row in result["period_rows"] if row["period"] == "2026-05"}
    assert may["occupation"]["visible_count"] == 2
    assert may["occupation"]["qualified_count"] == 1
    assert may["occupation"]["visible_production_rate_pct"] == 50.0
    assert may["occupation"]["production_traffic_share_pct"] == 11.0
    assert may["task"]["visible_count"] == 3 and may["task"]["qualified_count"] == 2
    assert may["task"]["production_traffic_share_pct"] == 25.0
    assert result["top_occupations"][0]["entity_id"] == "SOC:1"
    assert result["top_occupations"][0]["usage_share_change_pp"] == 1.0
    assert result["top_occupations"][0]["change_status"] == "ok"
    assert len(result["top_occupations"][0]["lineage"]["input_observation_ids"]) == 4
    coverage = next(
        row
        for row in result["occupation_task_coverage"]
        if row["period"] == "2026-05" and row["occupation_id"] == "SOC:1"
    )
    assert coverage["taxonomy_task_count"] == 3 and coverage["qualified_mapped_task_count"] == 1
    assert coverage["coverage_lower_bound_pct"] == 100 / 3
    diagnostics = next(row for row in result["coverage_diagnostics"] if row["period"] == "2026-05")
    assert diagnostics["qualified_unmapped_task_count"] == 1
    points = result["occupation_coverage_distribution"]["points"]
    assert all(
        left["occupation_share_pct"] >= right["occupation_share_pct"]
        for left, right in pairwise(points)
    )
    assert list(result["frames"]["summary"].columns)[:4] == [
        "period",
        "grain",
        "visible_count",
        "qualified_count",
    ]
    assert str(result["frames"]["summary"]["visible_count"].dtype) == "Int64"
    assert str(result["frames"]["summary"]["production_traffic_share_pct"].dtype) == "Float64"
    assert result["manifest"]["snapshot_id"] == "fixture-snapshot"


def test_product_rejects_claude_ai_and_keeps_grains_separate():
    products = _governed_products()
    rejected = products.ai_production_penetration(source_product="claude_ai")
    assert rejected["status"] == "unsupported_source_product"
    result = products.ai_production_penetration(periods=["2026-05"], top_n=10)
    occupation = next(row for row in result["period_rows"] if row["grain"] == "occupation")
    task = next(row for row in result["period_rows"] if row["grain"] == "task")
    assert occupation["production_traffic_share_pct"] != task["production_traffic_share_pct"]


def test_taxonomy_relation_as_of_changes_only_supplemental_coverage():
    products = _governed_products()
    old = products.ai_production_penetration(
        periods=["2026-05"], as_of=datetime(2026, 6, 26, tzinfo=UTC)
    )
    newer = products.ai_production_penetration(
        periods=["2026-05"], as_of=datetime(2026, 7, 2, tzinfo=UTC)
    )
    assert old["period_rows"] == newer["period_rows"]
    old_diagnostic, new_diagnostic = (
        old["coverage_diagnostics"][0],
        newer["coverage_diagnostics"][0],
    )
    assert old_diagnostic["qualified_unmapped_task_count"] == 1
    assert new_diagnostic["qualified_unmapped_task_count"] == 0


def test_as_of_replays_a_saved_snapshot_after_a_newer_vintage_arrives():
    products = _governed_products()
    old_as_of = datetime(2026, 6, 26, tzinfo=UTC)
    initial = products.ai_production_penetration(
        periods=["2026-05"], as_of=old_as_of, snapshot_consumer="test", snapshot_purpose="replay"
    )
    revised = dict(
        next(
            row
            for row in products.structured.rows
            if row["period"] == "2026-05"
            and row["entity_id"] == "SOC:1"
            and row["metric_id"] == USAGE
        )
    )
    revised.update(
        {"observation_id": "newer-vintage", "value": 99.0, "known_at": "2026-08-01T00:00:00+00:00"}
    )
    products.structured.rows.append(revised)
    replayed = products.ai_production_penetration(periods=["2026-05"], as_of=old_as_of)
    current = products.ai_production_penetration(periods=["2026-05"])
    assert replayed["ordered_record_hash"] == initial["ordered_record_hash"]
    assert current["ordered_record_hash"] != initial["ordered_record_hash"]


def test_production_observer_has_no_provider_or_physical_store_dependency():
    source = Path("src/ats/agents/evidence/work_adoption.py").read_text(encoding="utf-8")
    names = {
        node.names[0].name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import) and node.names
    }
    imported = {
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        "anthropic" in name.lower() or "repository" in name.lower() for name in names | imported
    )
    assert "员工采用率" in source and "岗位替代" in source and "交易信号" in source
