from datetime import datetime, timezone
import json
from pathlib import Path

from ats.agents.evidence.layer_runner import (
    OBSERVER_RUNNER_EXTENSIONS, _configured_observers, run_registered_layer_observers,
)
from ats.agents.evidence.raw_capability import observe_ai_raw_capability, render_ai_raw_capability_markdown
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.products.frontier_ai_capability import evaluate_a, evaluate_b
from ats.data.sources.frontier_ai_capability import (
    FrontierAICapabilityAdapter, OfficialLabReleaseAdapter, classify_method_change, normalize_payload,
    _parse_aa_dataset_documents,
    _parse_livebench_csv, _parse_readme_scores, _parse_terminal_submission,
    _parse_structured_html_scores, _parse_toolathlon_verified,
    _parse_terminal_bench_science_json, _parse_osworld_json,
)
from ats.data.stores.structured.repository import SQLiteStructuredRepository

FIXTURE = Path(__file__).parent / "fixtures" / "frontier_ai_raw_capability" / "sample_capability_payload.json"
NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


def products(tmp_path):
    repo = SQLiteStructuredRepository(tmp_path / "cap.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    result = IngestionPipeline(repo).run(
        FrontierAICapabilityAdapter(fixture_path=FIXTURE, clock=lambda: NOW),
        FetchRequest(source_id="frontier_ai_capability", dataset_id="frontier_ai_capability_benchmarks",
                     query_scope={"test_only": True}),
    )
    assert result["status"] == "succeeded"
    return DataProducts(structured_repository=repo)


def test_adapter_normalizes_fixed_panel_and_preserves_source_identity():
    records, entities, diagnostics = normalize_payload(FIXTURE.read_bytes(), fetched_at=NOW)
    assert len(entities) == 9 and len(records) == 12
    assert {r.dimensions["source_type"] for r in records} == {
        "third_party_evaluation", "lab_self_reported", "competitor_reported"
    }
    assert sum(r.dimensions["uniform_matrix"] for r in records) == 11
    assert sum(r.dimensions["event_only"] for r in records) == 1
    assert diagnostics["cohort_version"]


def test_matrix_is_always_eleven_by_nine_with_na(tmp_path):
    matrix = products(tmp_path).frontier_ai_capability_matrix()
    assert len(matrix["matrix"]) == 99
    assert matrix["coverage"]["numeric"] == 11
    assert sum(cell["score"] is None for cell in matrix["matrix"]) == 88
    assert len(matrix["event_observations"]) == 1


def test_b_only_eligible_benchmarks_and_three_level_thresholds(tmp_path):
    packet = products(tmp_path).frontier_ai_capability_evidence_bundle()
    by_id = {row["benchmark_id"]: row for row in packet["b"]["benchmarks"]}
    assert by_id["livebench"]["status"] == "not_applicable"
    assert by_id["terminal_bench_4"]["status"] in {"provisional_crossing", "not_crossed", "not_evaluated"}
    assert by_id["terminal_bench_4"]["levels"][0]["level_id"] == "majority_task_unlock"


def test_harbor_terminal_row_preserves_best_config_evidence_and_confidence():
    row = _parse_terminal_submission(
        {
            "metadata": {
                "date": "2026-09-03",
                "agent_display": {"label": "Codex"},
                "model_display": {"label": "GPT-6 Astra"},
                "model_org": {"label": "OpenAI"},
                "reasoning_effort": "max",
            },
            "metrics": {
                "accuracy": 58.18,
                "accuracy_ci95_half_width": 2.79,
                "n_trials": 330,
            },
        },
        source_url="https://example.test/functions/v1/leaderboard-read",
        fetched_date="2026-09-18",
        source_transport="harbor_leaderboard_api",
    )
    assert row is not None
    assert row["model_name"] == "GPT-6 Astra"
    assert row["score"] == 58.18
    assert row["reasoning_effort"] == "max"
    assert row["harness"] == "Codex"
    assert row["sample_size"] == 330
    assert row["confidence_low"] == 55.39
    assert row["confidence_high"] == 60.97
    assert row["source_transport"] == "harbor_leaderboard_api"


def test_osworld_strict_variant_can_enter_b_while_partial_stays_a_only():
    payload = {
        "models": [{"lab_id": "OPENAI", "model_id": "gpt-6-astra", "display_name": "GPT-6 Astra"}],
        "scores": [{"lab_id": "OPENAI", "model_id": "gpt-6-astra", "benchmark_id": "osworld",
                    "score": 62, "score_semantics": "binary reward", "b_eligible": True,
                    "score_as_of": "2026-09-15"}],
    }
    records, _, _ = normalize_payload(payload, fetched_at=NOW)
    assert records[0].dimensions["b_eligible"] is True
    assert records[0].dimensions["comparability_group"] == "osworld_2/strict"


def test_osworld_strict_alias_sets_b_eligibility_without_semantics_field():
    payload = {
        "models": [{"lab_id": "OPENAI", "model_id": "gpt-6-astra", "display_name": "GPT-6 Astra"}],
        "scores": [{"lab_id": "OPENAI", "model_id": "gpt-6-astra", "benchmark_id": "osworld_strict",
                    "score": 62, "score_as_of": "2026-09-15"}],
    }
    records, _, _ = normalize_payload(payload, fetched_at=NOW)
    assert records[0].dimensions["b_eligible"] is True
    assert records[0].dimensions["comparability_group"] == "osworld_2/strict"


def test_a_without_previous_baseline_is_explicitly_insufficient(tmp_path):
    packet = products(tmp_path).frontier_ai_capability_evidence_bundle()
    assert packet["a"]["status"] == "insufficient_history"
    assert all(row["status"] == "insufficient_history" for row in packet["a"]["benchmarks"])


def test_a_uses_published_score_dates_as_history_when_available():
    rows = [
        {"benchmark_id": "terminal_bench_4", "score": 40.0, "score_as_of": "2026-08-01",
         "model_id": "old", "model_name": "Old", "model_release_name": "Old",
         "comparability_group": "terminal_bench/4.0", "uniform_matrix": True},
        {"benchmark_id": "terminal_bench_4", "score": 59.0, "score_as_of": "2026-09-01",
         "model_id": "new", "model_name": "New", "model_release_name": "New",
         "comparability_group": "terminal_bench/4.0", "uniform_matrix": True},
    ]
    result = evaluate_a({"candidate_observations": rows})
    item = next(row for row in result["benchmarks"] if row["benchmark_id"] == "terminal_bench_4")
    assert item["status"] == "provisional_expansion"
    assert item["delta_pp"] == 19.0


def test_method_change_is_conservative():
    assert classify_method_change({"harness": "a"}, {"harness": "b"})["classification"] == "breaking"
    assert classify_method_change({"harness": "a"}, {"harness": "a"})["classification"] == "compatible"
    assert classify_method_change({"method_version": "v1", "harness": "a"},
                                 {"method_version": "v2", "harness": "a"})["classification"] == "compatible"


def test_official_release_rows_preserve_alias_withdrawal_and_non_comparable_state(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "frontier_ai_raw_capability" / "official_lab_release_sample.json"
    batch = OfficialLabReleaseAdapter(fixture_path=fixture, clock=lambda: NOW).fetch(
        FetchRequest(source_id="frontier_ai_capability", dataset_id="frontier_ai_capability_benchmarks"))
    assert len(batch.entities) == 6 and len(batch.records) == 3
    deepseek = next(item for item in batch.entities if item.metadata.get("model_id") == "deepseek-v4.1-flash")
    assert deepseek.metadata["available"] is False
    qwen = next(item for item in batch.records if item.dimensions["lab_id"] == "ALIBABA")
    assert "non_comparable" in qwen.dimensions["comparability_group"]


def test_no_key_fallback_is_truthful_no_coverage():
    adapter = FrontierAICapabilityAdapter(api_key="", clock=lambda: NOW)
    batch = adapter.fetch(FetchRequest(source_id="frontier_ai_capability", dataset_id="frontier_ai_capability_benchmarks"))
    assert batch.status.value == "no_coverage"
    assert not batch.records


def test_public_source_parsers_emit_real_source_lineage_and_scope():
    livebench = b"model,math,code\nDeepSeek V4 Flash,80,70\n"
    rows, meta = _parse_livebench_csv(livebench, source_url="https://raw.githubusercontent.com/LiveBench/new-livebench/main/public/table_2026_06_25.csv", fetched_date="2026-09-17")
    assert rows[0]["lab_id"] == "DEEPSEEK"
    assert rows[0]["measurement_scope"] == "model_capability_proxy"
    assert rows[0]["source_transport"] == "git_csv"
    assert meta["record_count"] == 1
    submission = {"metadata": {"model_display": {"label": "Fable 5.1"},
                                "model_org": {"label": "Anthropic"}, "date": "2026-09-16",
                                "agent_display": {"label": "Claude Code"}, "reasoning_effort": "max"},
                  "metrics": {"accuracy": 57.88, "accuracy_ci95_half_width": 3.76, "n_trials": 330}}
    row = _parse_terminal_submission(submission, source_url="https://github.com/harbor-framework/terminal-bench", fetched_date="2026-09-17")
    assert row["measurement_scope"] == "model_agent_stack_capability"
    assert row["confidence_low"] == 54.12
    readme = b"| Model | Pass Rate |\n|---|---:|\n| Kimi K3 | 46.67% |\n"
    rows, _ = _parse_readme_scores(readme, source_url="https://github.com/zapier/AutomationBench/blob/main/README.md", fetched_date="2026-09-17", benchmark_id="automationbench_aa")
    assert rows[0]["lab_id"] == "MOONSHOT"
    assert rows[0]["measurement_scope"] == "model_agent_stack_capability"


def test_public_structured_pages_are_explicit_json_only_and_keep_uniform_matrix_flag():
    html = b'''<html><script type="application/json">
      {"data":[{"model":"Qwen 3.8 Max","provider":"Alibaba","accuracy":62.5,"date":"2026-09-15"}]}
    </script></html>'''
    rows, meta = _parse_structured_html_scores(
        html, source_url="https://example.test/evaluations/toolathlon",
        fetched_date="2026-09-17", benchmark_id="toolathlon_verified",
        measurement_scope="model_agent_stack_capability", event_only=False,
    )
    assert meta["structured_only"] is True
    assert rows[0]["lab_id"] == "ALIBABA"
    assert rows[0]["uniform_matrix"] is True
    assert rows[0]["source_transport"] == "public_html_structured"


def test_aa_details_url_binds_exact_release_without_losing_evaluation_variant():
    documents = [{"name": "SciCode: Score", "data": [{
        "label": "GPT-6 Astra (max)", "SciCode": 0.56481481,
        "detailsUrl": "/models/gpt-6-astra",
    }]}]
    rows, meta = _parse_aa_dataset_documents(
        documents, source_url="https://artificialanalysis.ai/evaluations/scicode",
        fetched_date="2026-09-17", benchmark_id="scicode",
        measurement_scope="model_capability_proxy",
    )
    assert meta["parser_drift"] is False
    assert rows[0]["model_id"] == "gpt-6-astra-max"
    assert rows[0]["model_release_id"] == "gpt-6-astra"
    assert rows[0]["reasoning_effort"] == "max"


def test_matrix_joins_exact_release_variant_and_keeps_variant_lineage():
    from types import SimpleNamespace
    payload = {
        "models": [
            {"lab_id": "OPENAI", "model_id": "gpt-6-astra-high",
             "display_name": "GPT-6 Astra (high)"},
            {"lab_id": "OPENAI", "model_id": "gpt-6-astra-max",
             "display_name": "GPT-6 Astra (max)"},
        ],
        "scores": [{"lab_id": "OPENAI", "model_id": "gpt-6-astra-max",
                    "model_release_id": "gpt-6-astra", "benchmark_id": "scicode",
                    "score": 56.48, "reasoning_effort": "max"},
                   {"lab_id": "OPENAI", "model_id": "gpt-6-astra-high",
                    "model_release_id": "gpt-6-astra", "benchmark_id": "scicode",
                    "score": 54.0, "reasoning_effort": "high"}],
    }
    records, _, _ = normalize_payload(payload, fetched_at=NOW)
    rows = [{"observation_id": f"o-{index}", "artifact_id": "a", "period": "2026-09-17",
             "known_at": "2026-09-17T00:00:00+00:00", "value": record.value,
             "dimensions": record.dimensions}
            for index, record in enumerate(records)]
    structured = SimpleNamespace(observations=lambda **kwargs: rows, entities=lambda: [],
                                 frontier_capability_coverage=lambda: [])
    from ats.data.products.frontier_ai_capability import capability_matrix
    matrix = capability_matrix(SimpleNamespace(structured=structured))
    cell = next(item for item in matrix["matrix"]
                if item["lab_id"] == "OPENAI" and item["benchmark_id"] == "scicode")
    assert cell["score"] == 56.48
    assert cell["model_release_id"] == "gpt-6-astra"
    assert cell["evaluation_variant_id"] == "gpt-6-astra-max"
    assert "highest_observed_configuration_for_exact_release" in cell["selection_reason"]


def test_toolathlon_unknown_html_fails_closed_without_inventing_rows():
    rows, meta = _parse_toolathlon_verified(
        b"<html><body>leaderboard temporarily unavailable</body></html>",
        source_url="https://toolathlon.xyz/docs/leaderboard", fetched_date="2026-09-17",
    )
    assert rows == []
    assert meta["record_count"] == 0


def test_toolathlon_structured_payload_preserves_verification_and_harness_fields():
    html = b'''<script type="application/json">{"data":[
      {"model":"Kimi K3","provider":"Moonshot","score":76.5,
       "verified_status":"verified","harness":"toolathlon-v1",
       "task_set":"verified-public","grader":"exact-checks",
       "n_trials":120,"date":"2026-09-15"}
    ]}</script>'''
    rows, _ = _parse_structured_html_scores(
        html, source_url="https://toolathlon.xyz/docs/leaderboard",
        fetched_date="2026-09-17", benchmark_id="toolathlon_verified",
        measurement_scope="model_agent_stack_capability", event_only=False,
    )
    assert rows[0]["verified_status"] == "verified"
    assert rows[0]["harness"] == "toolathlon-v1"
    assert rows[0]["task_set"] == "verified-public"
    assert rows[0]["sample_size"] == 120


def test_public_nested_terminal_science_schema_is_parsed_without_filling_missing_models():
    payload = {"rows": [{
        "metadata": {"model_display": {"label": "Fable 5.1"},
                     "model_org": {"label": "Anthropic"},
                     "model_release_date": "2026-09-01",
                     "agent_display": {"label": "Claude Code"},
                     "reasoning_effort": "max"},
        "metrics": {"accuracy": 40.0, "accuracy_stderr": 3.38, "tasks": 210},
    }]}
    rows, meta = _parse_terminal_bench_science_json(
        json.dumps(payload).encode(), source_url="https://example.test/science",
        fetched_date="2026-09-17")
    assert rows[0]["lab_id"] == "ANTHROPIC"
    assert rows[0]["score"] == 40.0
    assert rows[0]["sample_size"] == 210
    assert meta["parser_drift"] is False


def test_public_nested_osworld_schema_uses_partial_score_route():
    payload = {"benchmarkVersion": "OSWorld 2.0", "updatedAt": "2026-09-03",
               "results": [{"model": "GPT-6 Astra", "modelFamily": "OpenAI",
                             "partialScore": 54.89, "binaryAccuracy": 22.32,
                             "reasoning": "low", "toolSetting": "batch tool",
                             "stepBudget": 500, "releaseVersion": "v2026.08.08"}]}
    rows, meta = _parse_osworld_json(
        json.dumps(payload).encode(), source_url="https://example.test/osworld",
        fetched_date="2026-09-17")
    assert rows[0]["score"] == 54.89
    assert rows[0]["score_as_of"] == "2026-09-03"
    assert rows[0]["metric_semantic"] == "partial completion percent"
    assert meta["metric"] == "partialScore"


def test_exact_model_identity_does_not_fill_nearby_variant():
    payload = {
        "models": [{"lab_id": "ZAI", "model_id": "glm-5.3-flash", "display_name": "GLM-5.3-Flash"}],
        "scores": [{"lab_id": "ZAI", "model_id": "glm-5.3-flash", "benchmark_id": "livebench", "score": 61}],
    }
    records, _, _ = normalize_payload(payload, fetched_at=NOW)
    assert records[0].dimensions["model_id"] == "glm-5.3-flash"
    assert records[0].dimensions["model_id"] != "glm-5.3"


def test_nearby_variant_without_flagship_identity_leaves_configured_column_na():
    from types import SimpleNamespace
    payload = {
        "models": [{"lab_id": "ZAI", "model_id": "glm-5.3-flash", "display_name": "GLM-5.3-Flash"}],
        "scores": [{"lab_id": "ZAI", "model_id": "glm-5.3-flash", "benchmark_id": "livebench", "score": 61}],
    }
    records, _, _ = normalize_payload(payload, fetched_at=NOW)
    rows = [{"observation_id": "o", "artifact_id": "a", "period": "2026-09-15", "known_at": "2026-09-16",
             "value": records[0].value, "dimensions": records[0].dimensions}]
    structured = SimpleNamespace(observations=lambda **kwargs: rows, entities=lambda: [], frontier_capability_coverage=lambda: [])
    from ats.data.products.frontier_ai_capability import capability_matrix
    matrix = capability_matrix(SimpleNamespace(structured=structured))
    cell = next(item for item in matrix["matrix"] if item["lab_id"] == "ZAI" and item["benchmark_id"] == "livebench")
    assert cell["score"] is None
    assert cell["coverage_state"] == "not_evaluated"


def test_source_priority_prefers_independent_result_without_averaging():
    from types import SimpleNamespace

    base = {
        "observation_id": "obs", "artifact_id": "art", "period": "2026-09-15",
        "known_at": "2026-09-16T00:00:00+00:00", "value": 40.0, "source_id": "frontier_ai_capability",
        "quality_status": "accepted", "dimensions": {
            "lab_id": "OPENAI", "lab_label": "OpenAI", "model_id": "gpt-6-astra",
            "model_name": "GPT-6 Astra", "model_lineage": "gpt-6-astra", "flagship": True,
            "available": True, "benchmark_id": "terminal_bench_4", "benchmark_label": "Terminal-Bench 4.0",
            "method_version": "4.0", "comparability_group": "terminal_bench/4.0",
            "score_as_of": "2026-09-15", "source_transport": "fixture", "uniform_matrix": True,
            "coverage_state": "observed", "b_eligible": True,
        },
    }
    third_party = {**base, "value": 40.0,
                   "dimensions": {**base["dimensions"], "source_type": "third_party_evaluation"}}
    self_report = {**base, "value": 90.0,
                   "observation_id": "obs-self",
                   "dimensions": {**base["dimensions"], "source_type": "lab_self_reported"}}
    structured = SimpleNamespace(
        observations=lambda **kwargs: [third_party, self_report],
        entities=lambda: [],
        frontier_capability_coverage=lambda: [],
    )
    from ats.data.products.frontier_ai_capability import capability_matrix
    matrix = capability_matrix(SimpleNamespace(structured=structured))
    cell = next(item for item in matrix["matrix"]
                 if item["lab_id"] == "OPENAI" and item["benchmark_id"] == "terminal_bench_4")
    assert cell["score"] == 40.0
    assert cell["source_type"] == "third_party_evaluation"
    from ats.data.products.frontier_ai_capability import query_scores
    candidates = query_scores(SimpleNamespace(structured=structured), include_vintages=True)
    assert {row["source_type"] for row in candidates} == {"third_party_evaluation", "lab_self_reported"}


def test_public_structured_page_policy_gate_fails_closed(monkeypatch):
    monkeypatch.setenv("ATS_FRONTIER_AI_ALLOW_PUBLIC_STRUCTURED_PAGES", "0")
    adapter = FrontierAICapabilityAdapter(public=True, clock=lambda: NOW)
    adapter._public_route = lambda benchmark: {"transport": "official_html", "url": f"https://example.test/{benchmark}"}
    payload, source_url, access_path, meta = adapter._load_public(NOW)
    assert payload["scores"] == []
    assert all(item.get("status") == "source_policy_blocked"
               for item in meta["sources"] if item["benchmark_id"] in {
                   "automationbench_aa", "scicode", "critpt", "mmmu_pro",
                   "toolathlon_verified", "spreadsheetbench_2", "humanitys_last_exam"})


def test_public_structured_page_terms_gate_fails_closed(monkeypatch):
    monkeypatch.setenv("ATS_FRONTIER_AI_ALLOW_PUBLIC_STRUCTURED_PAGES", "1")
    monkeypatch.setenv("ATS_FRONTIER_AI_PUBLIC_TERMS_APPROVED", "0")
    adapter = FrontierAICapabilityAdapter(public=True, clock=lambda: NOW)
    adapter._public_route = lambda benchmark: {"transport": "official_html", "url": f"https://example.test/{benchmark}"}
    payload, source_url, access_path, meta = adapter._load_public(NOW)
    assert payload["scores"] == []
    blocked = [item for item in meta["sources"] if item["benchmark_id"] == "toolathlon_verified"]
    assert blocked and blocked[0]["status"] == "source_policy_blocked"
    assert "terms_or_robots_not_approved" in blocked[0]["error"]


def test_blocked_source_degrades_only_its_own_benchmark_slice(monkeypatch):
    """A policy-blocked HTML route must not suppress an independent JSON route."""
    import ats.data.sources.frontier_ai_capability as capability_source

    monkeypatch.setattr(
        capability_source, "BENCHMARKS",
        ("toolathlon_verified", "spreadsheetbench_2"),
    )
    monkeypatch.setenv("ATS_FRONTIER_AI_ALLOW_PUBLIC_STRUCTURED_PAGES", "0")
    adapter = FrontierAICapabilityAdapter(public=True, clock=lambda: NOW)
    adapter._public_route = lambda benchmark: {
        "toolathlon_verified": {
            "transport": "official_html",
            "url": "https://example.test/toolathlon",
        },
        "spreadsheetbench_2": {
            "transport": "official_json_event",
            "url": "https://example.test/spreadsheetbench.json",
        },
    }[benchmark]
    adapter._read_url = lambda url, **kwargs: (
        json.dumps([{"model": "GPT-5.2", "score": 26.8}]).encode("utf-8"),
        {"etag": '"spreadsheet-v2"'},
    )

    payload, _source_url, _access_path, meta = adapter._load_public(NOW)

    by_benchmark = {item["benchmark_id"]: item for item in meta["sources"]}
    assert by_benchmark["toolathlon_verified"]["status"] == "source_policy_blocked"
    assert by_benchmark["spreadsheetbench_2"].get("status") is None
    assert any(row["benchmark_id"] == "spreadsheetbench_2" for row in payload["scores"])
    assert not any(row["benchmark_id"] == "toolathlon_verified" for row in payload["scores"])


def test_duplicate_active_flagships_are_quarantined(tmp_path):
    payload = {
        "models": [
            {"lab_id": "OPENAI", "model_id": "gpt-6-astra", "flagship": True},
            {"lab_id": "OPENAI", "model_id": "gpt-6-astra-mini", "flagship": True},
        ],
        "scores": [
            {"lab_id": "OPENAI", "model_id": "gpt-6-astra", "benchmark_id": "livebench", "score": 60},
            {"lab_id": "OPENAI", "model_id": "gpt-6-astra-mini", "benchmark_id": "livebench", "score": 55},
        ],
    }
    fixture = tmp_path / "duplicate.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    repo = SQLiteStructuredRepository(tmp_path / "cap.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    result = IngestionPipeline(repo).run(
        FrontierAICapabilityAdapter(fixture_path=fixture, clock=lambda: NOW),
        FetchRequest(source_id="frontier_ai_capability", dataset_id="frontier_ai_capability_benchmarks",
                     query_scope={"test_only": True}),
    )
    assert result["status"] == "validation_failed"
    assert result["quarantined"] == 2
    assert all("duplicate_active_flagship" in row["reason_codes"] for row in result["results"])


def test_report_has_natural_language_before_matrix_and_method_cards(tmp_path):
    product_set = products(tmp_path)
    packet = observe_ai_raw_capability(products=product_set, chart_dir=str(tmp_path / "charts"))
    text = render_ai_raw_capability_markdown(packet)
    assert text.index("## 先给结论") < text.index("## 九家 Labs × 十一项 benchmark 当前矩阵")
    assert "NA" in text and "评估方向" in text
    assert "同一模型版本的配置归并" in text
    assert "不各占一列" in text
    assert "Tencent Hy4" in text
    assert "相对 50% 幅度" not in text
    assert "三级门槛" in text
    assert "**61.0%**" in text
    assert packet["visualization_descriptors"]
    assert packet["manifest"]["snapshot_id"]
    replay = product_set.replay_frontier_ai_capability_snapshot(packet["manifest"]["snapshot_id"])
    assert replay["replayable_without_network"] is True
    assert replay["derived_payload_present"] is True
    assert replay["matrix"]["rows_hash"] == packet["matrix"]["rows_hash"]
    assert replay["a"]["status"] == packet["a"]["status"]
    assert replay["b"]["status"] == packet["b"]["status"]
    assert len(replay["chart_data"]["coverage_heatmap"]) == 99
    assert len(replay["chart_data"]["event_ledger"]) == 1
    assert all(Path(item["png_path"]).exists() for item in packet["visualization_descriptors"])


def test_layer_runner_resolves_extension_without_changing_legacy_runner_set():
    assert "ai_raw_capability" in OBSERVER_RUNNER_EXTENSIONS
    refs = [type("Ref", (), {"claim_id": "ai_frontier_raw_capability", "runner": "ai_raw_capability", "enabled": True, "claim_definition_version": "v1", "supplemental_sources": (), "supplemental_claims": (), "evidence_sections": ()})()]
    resolved, error = _configured_observers(refs)
    assert error is None and "ai_frontier_raw_capability" in resolved
