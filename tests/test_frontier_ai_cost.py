"""Published evaluation cost travels with its score (ant-data-onboarding 10.2)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.sources.frontier_ai_capability import (
    FrontierAICapabilityAdapter,
    _parse_aa_flight_models,
    _parse_osworld_json,
    _parse_terminal_bench_science_json,
    _parse_terminal_submission,
    normalize_payload,
)
from ats.data.stores.structured.repository import SQLiteStructuredRepository

NOW = datetime(2026, 11, 20, tzinfo=UTC)
URL = "https://example.test/leaderboard"


def harbor_row(model, **metrics):
    return {"metadata": {"model_display": {"label": model}, "model_org": {"label": "Anthropic"}},
            "metrics": {"accuracy": 40.0, "tasks": 210, **metrics}}


def test_harbor_rows_keep_the_published_full_run_cost():
    payload = {"rows": [harbor_row("Claude Priced", total_cost_usd=4997.461181),
                        harbor_row("Claude Unpriced")]}
    rows, _ = _parse_terminal_bench_science_json(
        json.dumps(payload).encode(), source_url=URL, fetched_date="2026-11-20")
    assert rows[0]["cost"] == {"value": 4997.461181, "currency": "USD", "basis": "full_evaluation"}
    assert rows[1]["cost"] is None
    row = _parse_terminal_submission(harbor_row("Claude Priced", total_cost_usd=12.5),
                                     source_url=URL, fetched_date="2026-11-20")
    assert row["cost"] == {"value": 12.5, "currency": "USD", "basis": "full_evaluation"}


def test_osworld_uses_measured_cost_per_task_and_ignores_estimates():
    results = [
        {"model": "Claude Measured", "modelFamily": "Claude", "partialScore": 59.0,
         "costPerTaskUsd": 14.34},
        {"model": "Claude Estimated", "modelFamily": "Claude", "partialScore": 50.0,
         "estimatedCostUsd": 9.0},
    ]
    rows, _ = _parse_osworld_json(
        json.dumps({"benchmarkVersion": "OSWorld 2.0", "results": results}).encode(),
        source_url=URL, fetched_date="2026-11-20")
    assert rows[0]["cost"] == {"value": 14.34, "currency": "USD", "basis": "per_task"}
    assert rows[1]["cost"] is None


def test_page_with_token_prices_but_no_published_cost_stays_without_cost():
    model = {"slug": "claude-opus-5-5", "name": "Claude Opus 5.5", "creator": {"name": "Anthropic"},
             "scicode": 0.66, "price1mInputTokens": 4, "price1mOutputTokens": 20,
             "canonicalEvalTokenCounts": {"scicode": {"input": 1222503, "answer": 366615}}}
    flight = '1:{"initialModels":' + json.dumps([model]) + "}"
    html = ("<script>self.__next_f.push([1," + json.dumps(flight) + "]) </script>").encode()
    rows, _ = _parse_aa_flight_models(html, source_url=URL, fetched_date="2026-11-20",
                                      benchmark_id="scicode")
    records, _, _ = normalize_payload(
        {"models": [{"lab_id": "ANTHROPIC", "model_id": rows[0]["model_id"], "flagship": False}],
         "scores": rows}, fetched_at=NOW)
    assert "cost" not in records[0].raw


def test_cost_is_stored_and_read_with_the_same_evaluation(tmp_path):
    def score(model_id, benchmark, **over):
        return {"lab_id": "ANTHROPIC", "model_id": model_id, "benchmark_id": benchmark,
                "score": 40.0, "score_as_of": "2026-11-20", "source_url": URL, **over}

    payload = {
        "meta": {"fixture": True, "version": "v1"},
        "models": [{"lab_id": "ANTHROPIC", "model_id": "claude-priced", "flagship": False}],
        "scores": [
            score("claude-priced", "terminal_bench_science_01",
                  cost={"value": 100.0, "currency": "USD", "basis": "full_evaluation"}),
            score("claude-priced", "scicode"),
            score("claude-priced", "critpt", cost={"value": -1, "basis": "per_task"}),
        ],
    }
    fixture = tmp_path / "scores.json"
    fixture.write_text(json.dumps(payload))
    repo = SQLiteStructuredRepository(tmp_path / "cap.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    result = IngestionPipeline(repo).run(
        FrontierAICapabilityAdapter(fixture_path=fixture, clock=lambda: NOW),
        FetchRequest(source_id="frontier_ai_capability", dataset_id="frontier_ai_capability_benchmarks",
                     query_scope={"test_only": True}))
    assert result["status"] == "succeeded"

    rows = {row["benchmark_id"]: row
            for row in DataProducts(structured_repository=repo).frontier_ai_capability_scores()}
    priced = rows["terminal_bench_science_01"]
    assert (priced["cost_value"], priced["cost_currency"], priced["cost_basis"],
            priced["cost_source_url"]) == (100.0, "USD", "full_evaluation", URL)
    for benchmark in ("scicode", "critpt"):  # none published / not a usable amount
        assert rows[benchmark]["cost_value"] is None and rows[benchmark]["cost_basis"] == ""
