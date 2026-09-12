"""Contracts for independently runnable, read-only Evidence layers."""

from __future__ import annotations

import json

from ats.agents.evidence.layer_runner import run_registered_layer_observers
from ats.config import load_sector_config
from ats.runtime.cli import run_evidence
from ats.schemas.sector import EvidenceObserverRef
from ats.agents.evidence.work_adoption import observe_ai_production_penetration, render_ai_production_markdown


class _Layer:
    def __init__(self, key: str, label: str, evidence_observers=None):
        self.key = key
        self.label = label
        self.evidence_observers = evidence_observers or []


class _Config:
    name = "ai_hardware"
    label = "AI硬件"

    def __init__(self, output_dir: str):
        self.output_dir = output_dir

    @staticmethod
    def layer_by_key(key):
        if key == "L1_app":
            return _Layer(
                "L1_app",
                "L1 AI应用层（Token经济）",
                [
                    EvidenceObserverRef(
                        claim_id="ai_core_production_workflow_penetration",
                        runner="ai_production_penetration",
                    )
                ],
            )
        if key == "L2_compute":
            return _Layer("L2_compute", "L2 算力层")
        return None


def test_layer_dispatcher_runs_only_requested_scope(monkeypatch):
    called = []

    def _l1(**kwargs):
        called.append(("L1_app", kwargs["workflow_scope"]))
        return {"claim_id": "l1_claim", "status": "ok"}

    def _other(**kwargs):
        called.append(("other", kwargs["workflow_scope"]))
        return {"claim_id": "other_claim", "status": "ok"}

    monkeypatch.setattr(
        "ats.agents.evidence.layer_runner.OBSERVER_RUNNERS",
        {"l1_runner": ("l1_claim", _l1), "other_runner": ("other_claim", _other)},
    )
    result = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L1_app",
        layer_label="L1 AI应用层",
        observer_refs=[EvidenceObserverRef(claim_id="l1_claim", runner="l1_runner")],
    )
    assert result["status"] == "ok"
    assert [item[0] for item in called] == ["L1_app"]
    assert called[0][1]["layer"] == "L1_app"


def test_layer_dispatcher_distinguishes_no_observer_from_wrong_claim(monkeypatch):
    monkeypatch.setattr("ats.agents.evidence.layer_runner.OBSERVER_RUNNERS", {})
    empty = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L2_compute",
        layer_label="L2 算力层",
    )
    wrong_claim = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L2_compute",
        layer_label="L2 算力层",
        claim_ids=["ai_core_production_workflow_penetration"],
    )
    assert empty["status"] == "no_registered_observers"
    assert wrong_claim["status"] == "claim_not_registered_for_scope"


def test_layer_dispatcher_rejects_unknown_or_duplicate_configured_runner(monkeypatch):
    monkeypatch.setattr("ats.agents.evidence.layer_runner.OBSERVER_RUNNERS", {})
    unknown = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L1_app",
        layer_label="L1 AI应用层",
        observer_refs=[EvidenceObserverRef(claim_id="x", runner="unknown")],
    )
    duplicate = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L1_app",
        layer_label="L1 AI应用层",
        observer_refs=[
            EvidenceObserverRef(claim_id="same", runner="unknown"),
            EvidenceObserverRef(claim_id="same", runner="unknown"),
        ],
    )
    assert unknown["status"] == "unknown_observer_runner"
    assert duplicate["status"] == "invalid_observer_configuration"


def test_disabled_configured_observer_is_not_registered(monkeypatch):
    monkeypatch.setattr(
        "ats.agents.evidence.layer_runner.OBSERVER_RUNNERS",
        {"known": ("claim", lambda **_kwargs: {"claim_id": "claim", "status": "ok"})},
    )
    result = run_registered_layer_observers(
        sector="ai_hardware",
        sector_label="AI硬件",
        layer="L1_app",
        layer_label="L1 AI应用层",
        observer_refs=[EvidenceObserverRef(claim_id="claim", runner="known", enabled=False)],
    )
    assert result["status"] == "no_registered_observers"


def test_ai_hardware_l1_declares_data_observer_outside_chain_claims():
    l1 = load_sector_config("ai_hardware").layer_by_key("L1_app")
    assert l1 is not None
    assert [(item.claim_id, item.runner) for item in l1.evidence_observers] == [
        ("ai_core_production_workflow_penetration", "ai_production_penetration")
    ]
    assert l1.evidence_observers[0].claim_definition_version == "v2"
    assert l1.evidence_observers[0].supplemental_sources == ["ramp_ai_index"]
    assert "ai_core_production_workflow_penetration" not in {claim.id for claim in l1.claims}


def test_layer_workflow_writes_default_markdown_and_shortcut_uses_same_dispatcher(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(
        "ats.memory.get_store",
        lambda: (_ for _ in ()).throw(
            AssertionError("Layer workflow must not read legacy Evidence memory")
        ),
    )
    monkeypatch.setattr("ats.config.load_sector_config", lambda _sector: _Config(str(tmp_path)))
    captured = []

    def _run(**kwargs):
        captured.append(kwargs)
        return {
            "status": "no_registered_observers",
            "workflow_scope": {
                "sector": "ai_hardware",
                "sector_label": "AI硬件",
                "layer": "L2_compute",
                "layer_label": "L2 算力层",
            },
            "packets": [],
            "reason": "fixture",
        }

    monkeypatch.setattr("ats.agents.evidence.run_registered_layer_observers", _run)
    rc = run_evidence("layer", sector="ai_hardware", layer="L2_compute")
    output = capsys.readouterr().out
    written = next(tmp_path.glob("Evidence-ai_hardware-L2_compute-*.md"))
    assert rc == 0
    assert "已写入层级 Evidence 审阅文档" in output
    assert written.exists() and "no_registered_observers" in written.read_text()
    assert captured[0]["claim_ids"] is None

    rc = run_evidence(
        "ai-production",
        sector="ai_hardware",
        layer="L2_compute",
        output=str(tmp_path / "shortcut.md"),
    )
    assert rc == 0
    assert captured[1]["claim_ids"] == ["ai_core_production_workflow_penetration"]


def test_layer_workflow_fails_for_invalid_scope_and_keeps_json_machine_interface(capsys):
    rc = run_evidence("layer", sector="", layer="L1_app")
    output = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert output["status"] == "invalid_scope"


def test_l1_v2_consumes_only_bundle_and_renders_three_axes(tmp_path):
    class Products:
        def ai_adoption_evidence_bundle(self, **kwargs):
            axes = {}
            for key, label, value in (("enterprise_breadth", "企业采用广度", 10),
                                      ("worker_persistence", "员工持续使用", 20),
                                      ("task_production", "任务生产化", 40)):
                axes[key] = {"axis_id": key, "label": label, "source_id": key,
                    "period": f"p-{key}", "headline": {"value": value, "unit": "percent"},
                    "trend": {"status": "expanding", "input_observation_ids": [f"o-{key}"]},
                    "statistical_unit": key, "denominator": f"denominator-{key}",
                    "geography": "independent", "technology_scope": key,
                    "reference_period": key, "frequency": key, "methodology_regimes": ["r1"],
                    "source_status": "available", "detail": {}}
            return {"status": "ok", "bundle_version": "v1", "axes": axes,
                    "overall": {"status": "broadening_and_deepening", "steps": ["three axes"]},
                    "comparability": {"pairs": []}, "periods_are_asynchronous": True,
                    "manifest": {"snapshot_id": "manifest-v2"}, "content_hash": "hash"}

        def ai_production_penetration(self, **kwargs):
            raise AssertionError("v2 observer must not bypass the governed bundle")

    packet = observe_ai_production_penetration(
        products=Products(), workflow_scope={"sector": "ai_hardware", "layer": "L1_app"})
    assert packet["claim_definition_version"] == "v2"
    assert packet["overall_status"] == "broadening_and_deepening"
    assert packet["context"]["budget_status"] == "ok"
    assert packet["manifest"]["snapshot_id"] == "manifest-v2"
    report = render_ai_production_markdown(packet)
    assert report.index("三轴总览") < report.index("TOP10 生产化职业")
    assert "ONS" not in report and "判断轨迹" not in report
    assert "BTOS 美国企业 AI 采用广度" in report and "不计算跨来源综合分数" in report


def test_l1_v2_adds_ramp_as_supplement_without_changing_three_axis_state(tmp_path):
    class Products:
        def ai_adoption_evidence_bundle(self, **kwargs):
            axes = {}
            for key, label, value in (("enterprise_breadth", "企业采用广度", 10),
                                      ("worker_persistence", "员工持续使用", 20),
                                      ("task_production", "任务生产化", 40)):
                axes[key] = {"axis_id": key, "label": label, "source_id": key,
                    "period": "2026-08", "headline": {"value": value, "unit": "percent"},
                    "trend": {"status": "expanding", "input_observation_ids": [f"o-{key}"]},
                    "statistical_unit": key, "denominator": f"denominator-{key}",
                    "geography": "independent", "technology_scope": key,
                    "reference_period": key, "frequency": key, "methodology_regimes": ["r1"],
                    "source_status": "available", "detail": {}}
            return {"status": "ok", "bundle_version": "v1", "axes": axes,
                    "overall": {"status": "broadening_and_deepening", "interpretation": "three axes"},
                    "comparability": {"pairs": []}, "periods_are_asynchronous": False,
                    "manifest": {"snapshot_id": "manifest-v2"}, "content_hash": "hash"}

        def ramp_paid_adoption_snapshot(self, *, scope, **kwargs):
            return {"status": "ok", "scope": scope, "period": "2026-08-01", "periods": ["2026-08"],
                    "rows": [{"observation_id": f"r-{scope}", "artifact_id": f"a-{scope}",
                              "period": "2026-08-01", "entity_id": "RAMP_OVERALL",
                              "entity_name": "Ramp Overall", "metric_id": "ai.ramp.paid_business_adoption_share",
                              "value": 56.13, "unit": "percent", "provider_monthly_change_pp": 1.2,
                              "provider_yearly_change_pp": 8.5, "statistical_unit": "Ramp businesses",
                              "denominator_scope": "positive AI transaction", "quality_status": "accepted"}],
                    "quality": {"status": "accepted"}, "freshness": {},
                    "lineage": {"input_observation_ids": [f"r-{scope}"], "artifact_ids": [f"a-{scope}"]},
                    "manifest": {"snapshot_id": f"m-{scope}"}}

        def ramp_spend_per_employee_series(self, **kwargs):
            return {"status": "no_coverage", "scope": "spend_per_employee_overall", "rows": []}

        def ramp_model_market_share_series(self, **kwargs):
            return {"status": "no_coverage", "scope": "model_market_share_overall", "rows": []}

    packet = observe_ai_production_penetration(
        products=Products(), workflow_scope={"sector": "ai_hardware", "layer": "L1_app"})
    assert packet["overall_status"] == "broadening_and_deepening"
    ramp = packet["supplemental_signals"]["ramp_paid_adoption"]
    assert ramp["status"] == "partial" and ramp["slices"]["adoption_overall"]["rows"][0]["value"] == 56.13
    assert "Ramp 付费企业采用与 AI 支出补充证据" in render_ai_production_markdown(packet)
    compact_context = json.loads(packet["context"]["compact"])
    assert compact_context["ramp_paid_adoption"]["slices"]["adoption_overall"]["rows"]


def test_v2_renderer_writes_tables_charts_and_complete_sidecars(tmp_path):
    from ats.agents.evidence.adoption_visualization import render_ai_adoption_charts
    history = [{"period": "100", "period_end": "2026-01-11", "value": 12.0,
                "observation_id": "o1", "input_observation_ids": ["o1"]}]
    rows = [{"axis_id": "enterprise_breadth", "axis_label": "企业采用广度",
             "period": "100", "headline_value": 12.0, "trend_status": "expanding",
             "input_observation_ids": ["o1"]}]
    packet = {"axis_overview": rows, "manifest": {"snapshot_id": "m1"},
              "axes": {"enterprise_breadth": {"detail": {"history": history}}},
              "top_occupations": [], "top_tasks": []}
    result = render_ai_adoption_charts(packet=packet, output_dir=tmp_path)
    assert result.get("visualization_warning") is None
    assert result["descriptors"] and result["tables"]
    assert not (tmp_path / "four_axis_status_period_matrix.json").exists()
    sidecar = json.loads((tmp_path / "btos_enterprise_breadth.json").read_text())
    assert sidecar["title"] == "BTOS 美国企业 AI 采用广度"
    assert sidecar["manifest_id"] == "m1" and sidecar["observation_ids"] == ["o1"]
    assert sidecar["rows_hash"] == result["descriptors"][0]["rows_hash"]


def test_ramp_renderer_uses_same_rows_hash_for_tables_png_and_sidecar(tmp_path):
    from ats.agents.evidence.ramp_visualization import render_ramp_charts
    row = {"observation_id": "r1", "artifact_id": "a1", "period": "2026-08-01",
           "entity_id": "RAMP_OVERALL", "entity_name": "Ramp Overall", "value": 56.13,
           "unit": "percent", "scope": "adoption_overall"}
    packet = {"supplemental_signals": {"ramp_paid_adoption": {"status": "ok", "slices": {
        "adoption_overall": {"rows": [row], "manifest": {"snapshot_id": "m-ramp"}},
    }}}}
    result = render_ramp_charts(packet=packet, output_dir=tmp_path)
    assert result["descriptors"] and result["tables"]
    descriptor = result["descriptors"][0]
    table = result["tables"][0]
    assert descriptor["rows_hash"] == table["rows_hash"]
    assert descriptor["manifest_id"] == "m-ramp"
    assert descriptor["chart_slug"] == "ramp_adoption_overall"
    assert (tmp_path / "ramp_adoption_overall.png").exists()


def test_btos_chart_sorts_numeric_waves_and_report_embeds_chart(tmp_path):
    from ats.agents.evidence.adoption_visualization import render_ai_adoption_charts
    history = [{"period": period, "period_end": end, "value": value, "observation_id": f"o-{period}",
                "input_observation_ids": [f"o-{period}"]}
               for period, end, value in (("100", "2026-01-04", 20.0), ("107", "2026-04-12", 22.0),
                                           ("88", "2025-07-20", 17.0), ("99", "2025-12-21", 19.0))]
    packet = {"status": "ok", "claim_definition_version": "v2", "claim_text": "claim",
              "overall_status": "expanding", "axis_overview": [], "manifest": {"snapshot_id": "m1"},
              "axes": {"enterprise_breadth": {"detail": {"history": history}}},
              "top_occupations": [], "top_tasks": [], "warnings": []}
    rendered = render_ai_adoption_charts(packet=packet, output_dir=tmp_path)
    descriptor = next(item for item in rendered["descriptors"] if item["title"].startswith("BTOS"))
    assert descriptor["periods"] == ["88", "99", "100", "107"]
    from ats.agents.evidence.adoption_visualization import _period_label
    assert _period_label(history[0]) == "2026-01-04"
    assert "W" not in _period_label(history[0])
    packet["visualization_descriptors"] = rendered["descriptors"]
    packet["table_descriptors"] = rendered["tables"]
    report = render_ai_production_markdown(packet)
    assert "![BTOS 美国企业 AI 采用广度]" in report
    assert "[CSV](" in report and "[sidecar](" in report


def test_anthropic_four_metrics_and_coverage_are_rendered_and_embedded(tmp_path):
    from ats.agents.evidence.adoption_visualization import render_ai_adoption_charts
    summary = []
    for period, occupation_rate, task_rate, occupation_traffic, task_traffic in (
        ("2026-04", 44.95, 32.08, 68.77, 59.40),
        ("2026-05", 46.67, 31.72, 75.36, 68.66),
    ):
        for grain, rate, traffic in (("occupation", occupation_rate, occupation_traffic),
                                     ("task", task_rate, task_traffic)):
            summary.append({"period": period, "grain": grain,
                            "visible_production_rate_pct": rate,
                            "production_traffic_share_pct": traffic,
                            "visible_production_rate_change_pp": (None if period == "2026-04" else
                                (1.72 if grain == "occupation" else -0.36)),
                            "production_traffic_share_change_pp": (None if period == "2026-04" else
                                (6.59 if grain == "occupation" else 9.26)),
                            "lineage": {"input_observation_ids": [f"o-{period}-{grain}"]}})
    coverage = {"points": [
        {"minimum_coverage_pct": 0.0, "occupation_share_pct": 100.0,
         "eligible_occupation_count": 974, "period": "2026-05"},
        {"minimum_coverage_pct": 50.0, "occupation_share_pct": 0.31,
         "eligible_occupation_count": 974, "period": "2026-05"}],
        "landmarks": []}
    detail = {"period_rows": summary, "occupation_coverage_distribution": coverage,
              "occupation_task_coverage": [{"period": "2026-05",
                  "lineage": {"input_observation_ids": ["coverage-o1"]}}]}
    packet = {"status": "ok", "claim_definition_version": "v2", "claim_text": "claim",
              "overall_status": "insufficient_history", "axis_overview": [],
              "manifest": {"snapshot_id": "m-anthropic"},
              "axes": {"task_production": {"detail": detail}},
              "summary_table": summary, "occupation_coverage_distribution": coverage,
              "top_occupations": [], "top_tasks": [], "warnings": []}
    rendered = render_ai_adoption_charts(packet=packet, output_dir=tmp_path)
    titles = {item["title"] for item in rendered["descriptors"]}
    assert "Anthropic 职业/任务可见单元生产化率与生产化流量份额" in titles
    assert "Anthropic 职业内已确认生产化任务覆盖分布" in titles
    assert (tmp_path / "anthropic_production_four_metrics.png").exists()
    assert (tmp_path / "anthropic_occupation_task_coverage_ccdf.png").exists()
    coverage_sidecar = json.loads(
        (tmp_path / "anthropic_occupation_task_coverage_ccdf.json").read_text())
    assert coverage_sidecar["observation_ids"] == ["coverage-o1"]
    packet["visualization_descriptors"] = rendered["descriptors"]
    packet["table_descriptors"] = rendered["tables"]
    report = render_ai_production_markdown(packet)
    assert "四项核心指标月度比较（2026-04 → 2026-05）" in report
    assert "|职业可见单元生产化率|44.95%|46.67%|+1.72pp|" in report
    assert "|任务生产化流量份额|59.40%|68.66%|+9.26pp|" in report
    assert report.index("Anthropic 职业内已确认生产化任务覆盖分布") < report.index("TOP10 生产化职业")


def test_config_rollback_to_v1_does_not_delete_v2_data(monkeypatch):
    calls = []
    def observer(**kwargs):
        calls.append(kwargs["claim_definition_version"])
        return {"status": "ok", "claim_id": "ai_core_production_workflow_penetration"}
    monkeypatch.setattr("ats.agents.evidence.layer_runner.OBSERVER_RUNNERS",
                        {"ai_production_penetration": ("ai_core_production_workflow_penetration", observer)})
    ref = EvidenceObserverRef(claim_id="ai_core_production_workflow_penetration",
                              claim_definition_version="v1", runner="ai_production_penetration")
    result = run_registered_layer_observers(sector="ai_hardware", sector_label="AI硬件",
        layer="L1_app", layer_label="L1", observer_refs=[ref])
    assert result["status"] == "ok" and calls == ["v1"]
