import json

import yaml

from ats.agents.evidence.analyst_context import (
    analyst_context_paths,
    build_layer_analyst_context,
    load_layer_analyst_context,
    render_analyst_llm_input,
)
from ats.agents.evidence.layer_runner import write_layer_evidence_outputs
from ats.agents.evidence.work_adoption import PRODUCTION_CLAIM_ID


def _result():
    return {
        "status": "ok",
        "workflow_scope": {"sector": "ai_hardware", "layer": "L1_app"},
        "packets": [
            {
                "claim_id": PRODUCTION_CLAIM_ID,
                "claim_definition_version": "v3",
                "claim_text": "生产化是否扩散？",
                "status": "ok",
                "overall_status": "partial_expansion",
                "overall_interpretation": "企业采购扩大；各轴分母不同。",
                "axis_overview": [
                    {"axis_id": "paid_procurement", "axis_label": "企业付费采购与支出",
                     "source_id": "ramp_ai_index", "period": "2026-08", "headline_value": 56.1,
                     "headline_unit": "percent", "trend_status": "expanding",
                     "trend_net_change_pp": 48.4, "comparable_period_count": 44,
                     "statistical_unit": "Ramp enterprise cohort",
                     "denominator": "Ramp cohort", "geography": "Ramp network",
                     "input_observation_ids": ["ramp-1"]},
                ],
                "warnings": [], "facts": [],
            },
            {
                "claim_id": "ai_frontier_labs_commercialization",
                "claim_definition_version": "v1", "claim_text": "使用能否变成收入？",
                "status": "ok", "overall_status": "revenue_monetization_expanding_but_economics_unverified",
                "overall_interpretation": "收入扩大，但留存与单位经济尚未验证。",
                "companies": [{"company": "OpenAI", "entity_id": "openai",
                               "headline": {"record": {"period": "2026-06", "value_usd_bn": 12.34,
                                                        "metric_id": "annualized_revenue_run_rate",
                                                        "observation_identity": "third_party_estimate"}},
                               "trend": {"status": "expanding", "net_change_rate": .5,
                                         "comparable_point_count": 3, "span_days": 150,
                                         "input_observation_ids": ["rev-1"]}}],
                "openrouter": {"status": "ok", "directional_status": "expanding",
                               "facts": {"latest_complete_week": "2026-09-07",
                                         "latest_total_tokens": 126760000000000,
                                         "trend": {"status": "expanding", "change_rate": .7306},
                                         "concentration": {"top3_share": .535, "hhi": .1426}}},
                "coverage": {"retention": "not_yet_observed"}, "warnings": [],
            },
            {
                "claim_id": "ai_frontier_raw_capability",
                "claim_definition_version": "v1", "claim_text": "能力边界是否外扩？",
                "status": "ok", "matrix": {"coverage": {"numeric": 74, "total": 99, "ratio": .7475}},
                "a": {"status": "expanding", "benchmarks": [{
                    "benchmark_id": "terminal_bench_4", "status": "confirmed_expansion",
                    "current": {"model_name": "GPT-6 Astra", "score": 59.09,
                                "confidence_low": 56.0, "score_as_of": "2026-09-15"},
                    "previous": {"model_name": "Claude Fable 5.1", "score": 52.02,
                                 "confidence_high": 54.0}, "delta_pp": 7.07, "lcb_delta_pp": 2.0}]},
                "b": {"status": "crossed", "framework": ["majority_task_unlock"], "benchmarks": [{
                    "benchmark_id": "terminal_bench_4", "status": "confirmed_crossing",
                    "current": {"model_name": "GPT-6 Astra", "score": 59.09},
                    "levels": [{"level_id": "majority_task_unlock", "label": "多数任务解锁",
                                "threshold_pct": 50, "score": 59.09,
                                "confidence_low": 56.0, "status": "confirmed_crossing"}]}]},
                "warnings": [], "limitations": [], "rows_hash": "abc",
                "facts": [],
            },
        ],
    }


def test_context_is_compact_semantic_contract_with_boundaries():
    context = build_layer_analyst_context(_result(), human_report="L1_APP_PLATFORM_REPORT.md")
    assert context["context_role"] == "primary_input_for_analyst_llm"
    assert context["merge_policy"]["cross_observer_merge_allowed"] is False
    assert len(context["observers"]) == 3
    production = context["observers"][0]
    ramp = production["evidence"][0]
    assert ramp["latest"] == {"period": "2026-08", "value": 56.1, "unit": "percent"}
    assert ramp["trend"]["status"] == "expanding"
    assert ramp["scope"]["denominator"] == "Ramp cohort"
    assert ramp["cross_evidence_numeric_merge_allowed"] is False
    commercialization = context["observers"][1]
    assert "把 token 换算为收入" in commercialization["does_not_support"]
    raw = context["observers"][2]
    assert raw["evidence"][1]["values"][0]["levels"][0]["confidence_low_pct"] == 56
    assert context["context_char_count"] < 30000


def test_report_writer_also_writes_equivalent_json_and_yaml(tmp_path):
    report = tmp_path / "L1_APP_PLATFORM_REPORT.md"
    markdown_paths = write_layer_evidence_outputs(_result(), report)
    assert markdown_paths[0] == report
    json_path, yaml_path = analyst_context_paths(report)
    assert json_path.name == "L1_APP_PLATFORM_CONTEXT.json"
    assert yaml_path.name == "L1_APP_PLATFORM_CONTEXT.yaml"
    json_payload = json.loads(json_path.read_text(encoding="utf-8"))
    yaml_payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert json_payload == yaml_payload
    assert json_payload["human_report"] == "L1_APP_PLATFORM_REPORT.md"
    assert json_payload["context_hash_sha256"]
    assert load_layer_analyst_context(json_path) == json_payload
    assert load_layer_analyst_context(yaml_path) == yaml_payload
    assert json.loads(render_analyst_llm_input(json_payload))["schema_version"] == \
        "l1_analyst_context/v1"
    report_body = report.read_text(encoding="utf-8")
    assert "Canonical JSON context" in report_body
    assert "L1_APP_PLATFORM_CONTEXT.json" in report_body


def test_context_reader_rejects_tampering(tmp_path):
    report = tmp_path / "L1_APP_PLATFORM_REPORT.md"
    write_layer_evidence_outputs(_result(), report)
    json_path, _ = analyst_context_paths(report)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["observers"][0]["status"] = "fabricated"
    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        load_layer_analyst_context(json_path)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("tampered context must not be accepted")
