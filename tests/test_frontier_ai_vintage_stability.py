"""Re-collecting unchanged public benchmark pages must not append observation vintages.

Drives the real adapters and the real ingestion pipeline; only the HTTP opener and the clock are fake. A reading that
differs in the score or in how it was measured still appends.
"""
from datetime import datetime, timedelta, timezone
import json

import pytest

from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.products import frontier_ai_capability as product
from ats.data.sources.frontier_ai_capability import (
    PUBLIC_ROUTES, FrontierAICapabilityAdapter, PublicBenchmarkAdapter,
)
from ats.data.stores.structured.repository import SQLiteStructuredRepository

DATASET = "frontier_ai_capability_benchmarks"
FIRST_RUN = datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _public_route_policy(monkeypatch):
    for name in ("ATS_FRONTIER_AI_PUBLIC_ROUTES", "ATS_FRONTIER_AI_FLAGSHIP_OVERRIDES",
                 "ATS_FRONTIER_AI_USE_AA", "ATS_FRONTIER_AI_CAPABILITY_FIXTURE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ATS_FRONTIER_AI_ALLOW_PUBLIC_STRUCTURED_PAGES", "1")
    monkeypatch.setenv("ATS_FRONTIER_AI_PUBLIC_TERMS_APPROVED", "1")
    monkeypatch.setenv("ATS_FRONTIER_AI_PUBLIC_HTML_BENCHMARKS", "scicode")


def _osworld_page(score: float, **result) -> bytes:
    return json.dumps({"benchmarkVersion": "OSWorld 2.0", "updatedAt": "2026-09-17", "results": [
        {"model": "GPT-6 Astra", "modelFamily": "OpenAI", "partialScore": score, **result}]}).encode()


def _scicode_page() -> bytes:
    """A score page that states no score date, like the Artificial Analysis evaluation pages."""
    model = {"slug": "gpt-6-astra", "name": "GPT-6 Astra (max)",
             "creator": {"slug": "openai", "name": "OpenAI"}, "scicode": 0.625}
    flight = '1:{"initialModels":' + json.dumps([model]) + "}"
    return ("<script>self.__next_f.push([1," + json.dumps(flight) + "]) </script>").encode()


class _Response:
    def __init__(self, body: bytes):
        self.body, self.headers = body, {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return self.body


@pytest.fixture
def collect(tmp_path):
    repo = SQLiteStructuredRepository(tmp_path / "cap.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())

    def run(benchmarks, at, *, osworld_score: float = 54.89, raw_extra: dict | None = None, **osworld_result):
        pages = {PUBLIC_ROUTES["osworld_2"]["url"]: _osworld_page(osworld_score, **osworld_result),
                 PUBLIC_ROUTES["scicode"]["url"]: _scicode_page()}
        adapter = PublicBenchmarkAdapter(clock=lambda: at,
                                         opener=lambda request, **_kw: _Response(pages[request.full_url]))
        if raw_extra:
            fetch = adapter.fetch

            def fetch_with_extra(request):
                batch = fetch(request)
                return batch.model_copy(update={"records": [
                    record.model_copy(update={"raw": {**record.raw, **raw_extra}}) for record in batch.records]})

            adapter.fetch = fetch_with_extra
        return IngestionPipeline(repo).run(adapter, FetchRequest(
            source_id="frontier_ai_capability", dataset_id=DATASET,
            query_scope={"benchmarks": list(benchmarks)}))

    run.repo = repo
    return run


def _stored(repo) -> int:
    return len(repo.observations(dataset_id=DATASET, latest_only=False))


def _assert_second_run_unchanged(collect, first, second, *, expected: int):
    assert (second["accepted"], second["unchanged"]) == (0, expected), second
    assert second["status"] == "no_change"
    assert _stored(collect.repo) == first["accepted"]


@pytest.mark.parametrize("delay", [timedelta(minutes=2), timedelta(days=7)], ids=["same_day", "next_week"])
def test_dated_source_refetched_unchanged_appends_nothing(collect, delay):
    first = collect(["osworld_2"], FIRST_RUN)
    assert first["accepted"] == 1
    _assert_second_run_unchanged(collect, first, collect(["osworld_2"], FIRST_RUN + delay), expected=1)


@pytest.mark.parametrize("delay", [timedelta(minutes=2), timedelta(days=7)], ids=["same_day", "next_week"])
def test_undated_source_refetched_unchanged_appends_nothing(collect, delay):
    first = collect(["scicode"], FIRST_RUN)
    assert first["accepted"] == 1
    _assert_second_run_unchanged(collect, first, collect(["scicode"], FIRST_RUN + delay), expected=1)


def test_probe_with_narrower_route_scope_appends_nothing(collect):
    first = collect(["osworld_2", "scicode"], FIRST_RUN)
    assert first["accepted"] == 2
    second = collect(["scicode"], FIRST_RUN + timedelta(minutes=2))
    _assert_second_run_unchanged(collect, first, second, expected=1)


def test_revised_score_still_appends_a_vintage(collect):
    collect(["osworld_2"], FIRST_RUN)
    second = collect(["osworld_2"], FIRST_RUN + timedelta(days=7), osworld_score=56.0)
    assert (second["accepted"], second["unchanged"]) == (1, 0)
    assert _stored(collect.repo) == 2


def test_score_reverting_to_an_earlier_value_becomes_current_again(collect):
    for week, score in enumerate((54.89, 56.0, 54.89)):
        collect(["osworld_2"], FIRST_RUN + timedelta(days=7 * week), osworld_score=score)
    assert [row["value"] for row in collect.repo.observations(dataset_id=DATASET)] == [54.89]


def test_full_audit_after_a_probe_appends_nothing(collect):
    scopes = (["osworld_2", "scicode"], ["scicode"], ["osworld_2", "scicode"])
    results = [collect(scope, FIRST_RUN + timedelta(minutes=2 * index)) for index, scope in enumerate(scopes)]
    assert [(r["accepted"], r["unchanged"]) for r in results] == [(2, 0), (0, 1), (0, 2)]
    assert _stored(collect.repo) == 2


def test_unchanged_reading_is_still_recorded_against_its_own_run(collect):
    """The refresh ledger finds a run's admission and raw artifact through its candidates."""
    first = collect(["osworld_2"], FIRST_RUN)
    second = collect(["osworld_2"], FIRST_RUN + timedelta(days=7))
    assert second["results"][0]["observation_id"] == first["results"][0]["observation_id"]
    run_ids = collect.repo.conn.execute(
        "SELECT run_id FROM structured_candidates ORDER BY created_at").fetchall()
    assert [row[0] for row in run_ids] == [first["run_id"], second["run_id"]]


@pytest.mark.parametrize("change", [{"reasoning": "max"}, {"toolSetting": "batch tool"}, {"stepBudget": 500}],
                         ids=["effort", "harness", "inference_config"])
def test_changed_measurement_setup_appends_a_vintage(collect, change):
    collect(["osworld_2"], FIRST_RUN)
    second = collect(["osworld_2"], FIRST_RUN + timedelta(days=7), **change)
    assert (second["accepted"], second["unchanged"]) == (1, 0)


def test_new_raw_field_about_the_score_appends_once(collect):
    collect(["osworld_2"], FIRST_RUN)
    cost = {"cost": {"value": 1.5, "currency": "USD", "basis": "per_task"}}
    added = collect(["osworld_2"], FIRST_RUN + timedelta(days=7), raw_extra=cost)
    repeated = collect(["osworld_2"], FIRST_RUN + timedelta(days=14), raw_extra=cost)
    assert (added["accepted"], repeated["accepted"], repeated["unchanged"]) == (1, 0, 1)


def test_setting_and_clearing_a_flagship_override_each_append(collect, monkeypatch):
    collect(["osworld_2"], FIRST_RUN)
    monkeypatch.setenv("ATS_FRONTIER_AI_FLAGSHIP_OVERRIDES", json.dumps({"OPENAI": "gpt-6-astra"}))
    pinned = collect(["osworld_2"], FIRST_RUN + timedelta(days=7))
    held = collect(["osworld_2"], FIRST_RUN + timedelta(days=14))
    monkeypatch.delenv("ATS_FRONTIER_AI_FLAGSHIP_OVERRIDES")
    cleared = collect(["osworld_2"], FIRST_RUN + timedelta(days=21))
    assert [r["accepted"] for r in (pinned, held, cleared)] == [1, 0, 1]


def test_return_to_an_earlier_value_appends_even_when_the_payload_is_identical(tmp_path):
    """Without a fetch time anywhere in the payload, the third reading equals the first byte for byte."""
    repo = SQLiteStructuredRepository(tmp_path / "fixed.sqlite", artifact_root=tmp_path / "fixed-artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    fixture = tmp_path / "scores.json"
    created = []
    for week, score in enumerate((50, 60, 50, 50)):
        fixture.write_text(json.dumps({
            "models": [{"lab_id": "OPENAI", "model_id": "gpt-6-astra"}],
            "scores": [{"lab_id": "OPENAI", "model_id": "gpt-6-astra", "benchmark_id": "scicode",
                        "score": score, "score_as_of": "2026-09-17"}]}), encoding="utf-8")
        at = FIRST_RUN + timedelta(days=7 * week)
        created.append(IngestionPipeline(repo).run(
            FrontierAICapabilityAdapter(fixture_path=fixture, clock=lambda at=at: at),
            FetchRequest(source_id="frontier_ai_capability", dataset_id=DATASET,
                         query_scope={"test_only": True}))["accepted"])
    assert created == [1, 1, 1, 0]
    assert [row["value"] for row in repo.observations(dataset_id=DATASET)] == [50.0]


def test_stored_registry_flag_does_not_outlive_a_registry_change(collect, monkeypatch):
    collect(["osworld_2"], FIRST_RUN)
    monkeypatch.setitem(product.DEFAULT_FLAGSHIP_MODEL_IDS, "OPENAI", "gpt-7")
    monkeypatch.setitem(product.DEFAULT_FLAGSHIP_MODEL_NAMES, "OPENAI", "GPT-7")
    matrix = product.capability_matrix(DataProducts(structured_repository=collect.repo))
    assert matrix["by_lab"]["OPENAI"]["model"]["model_release_id"] == "gpt-7"
