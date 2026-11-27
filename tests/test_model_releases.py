"""Model release registry (ant-data-onboarding 10.1 / 11.1)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.model_releases import (
    UNREGISTERED_LABEL,
    ModelReleaseError,
    ModelReleaseRegistry,
    load_model_releases,
)
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.sources.frontier_ai_capability import FrontierAICapabilityAdapter, normalize_payload
from ats.data.stores.structured.repository import SQLiteStructuredRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
SCORED = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)


def release(**over):
    return {"lab_id": "ANTHROPIC", "model_id": "claude-example-1", "display_name": "Claude Example 1",
            "tier": "general_flagship", "release_date": "2026-01-24",
            "announcement_url": "https://www.anthropic.com/news/claude-example-1",
            "status": "confirmed", "registered_at": "2026-01-22T09:00:00Z", **over}


def registry(*releases):
    return ModelReleaseRegistry.from_payload({
        "version": 1,
        "official_domains": {"ANTHROPIC": ["anthropic.com"], "ALIBABA": ["github.com/QwenLM"]},
        "releases": list(releases)})


def test_committed_registry_loads():
    load_model_releases(REPO_ROOT / "config" / "data" / "model_releases.yaml")


def test_confirmed_record_is_queryable_by_lab_and_date():
    reg = registry(release())
    assert [r.model_id for r in reg.confirmed(lab_id="ANTHROPIC")] == ["claude-example-1"]
    assert reg.flagship("ANTHROPIC", as_of="2026-01-24").release_date == "2026-01-24"
    assert reg.flagship("ANTHROPIC", as_of="2026-01-23") is None  # not released yet
    assert reg.confirmed(as_of="2026-01-21") == []  # not registered yet


def test_candidate_cannot_start_a_clock():
    reg = registry(release(status="candidate", release_date=None,
                           announcement_url="https://news.example.com/rumour"))
    assert reg.confirmed() == []
    assert reg.flagship("ANTHROPIC") is None
    assert reg.resolve("ANTHROPIC", ["claude-example-1"]) is None


@pytest.mark.parametrize("url", [
    "", "https://news.example.com/anthropic-launch", "http://www.anthropic.com/news/x",
    "https://anthropic.com.example.com/news/x", "https://github.com/someone-else/Qwen",
])
def test_confirmed_record_needs_an_official_announcement(url):
    with pytest.raises(ModelReleaseError, match="lab's own domain"):
        registry(release(announcement_url=url))


def test_organisation_page_on_a_shared_host_is_matched_by_path():
    reg = registry(release(lab_id="ALIBABA", model_id="qwen-x",
                           announcement_url="https://github.com/QwenLM/qwen-x"))
    assert reg.flagship("ALIBABA").model_id == "qwen-x"


def test_correction_replays_old_value_at_the_old_time():
    reg = registry(release(
        release_date="2026-01-22",
        corrections=[{"corrected_at": "2026-02-01T00:00:00Z", "reason": "公告页日期为 01-22",
                      "previous": {"release_date": "2026-01-24"}}]))
    old = reg.confirmed(as_of="2026-01-23")[0]
    assert old.release_date == "2026-01-24" and old.corrections == ()
    now = reg.confirmed()[0]
    assert now.release_date == "2026-01-22"
    assert now.corrections[0].reason == "公告页日期为 01-22"


def test_correction_without_a_reason_is_rejected():
    with pytest.raises(ModelReleaseError, match="reason"):
        registry(release(corrections=[{"corrected_at": "2026-02-01T00:00:00Z",
                                       "previous": {"release_date": "2026-01-23"}}]))


def test_flagship_is_the_latest_general_flagship_only():
    reg = registry(
        release(),
        release(model_id="claude-example-2", release_date="2026-03-01",
                registered_at="2026-03-01T00:00:00Z"),
        release(model_id="claude-example-mini", tier="other", release_date="2026-05-01",
                registered_at="2026-05-01T00:00:00Z"))
    assert reg.flagship("ANTHROPIC").model_id == "claude-example-2"
    assert reg.flagship("ANTHROPIC", as_of="2026-02-01").model_id == "claude-example-1"


def test_resolve_is_exact_and_only_removes_evaluation_settings():
    reg = registry(
        release(source_aliases=["claude-example-1-0"]),
        release(lab_id="ALIBABA", model_id="qwen3.8-max",
                announcement_url="https://github.com/QwenLM/qwen"))
    assert reg.resolve("ANTHROPIC", ["claude-example-1-max-effort"]).model_id == "claude-example-1"
    assert reg.resolve("ANTHROPIC", ["claude-example-1-0"]).model_id == "claude-example-1"
    assert reg.resolve("ANTHROPIC", ["claude-example-1-flash"]) is None
    # ``max`` in a registered product name is not an effort setting.
    assert reg.resolve("ALIBABA", ["qwen3.8-max-high"]).model_id == "qwen3.8-max"
    assert reg.resolve("ALIBABA", ["qwen3.8"]) is None


def test_one_name_cannot_point_at_two_releases():
    with pytest.raises(ModelReleaseError, match="names both"):
        registry(release(), release(model_id="claude-example-2", source_aliases=["claude-example-1"]))


def _scored_products(tmp_path):
    """Anthropic scores as the Artificial Analysis pages deliver them: no release date."""
    payload = {
        "meta": {"as_of": SCORED.isoformat(), "version": "v1", "fixture": True},
        "models": [
            {"lab_id": "ANTHROPIC", "model_id": "claude-example-1-max", "display_name": "Claude Example 1 (max)",
             "model_release_id": "claude-example-1-max", "flagship": False,
             # A date supplied by a score source must not survive.
             "release_date": "2026-11-20"},
            {"lab_id": "ANTHROPIC", "model_id": "claude-unlisted", "display_name": "Claude Unlisted",
             "flagship": False},
        ],
        "scores": [
            {"lab_id": "ANTHROPIC", "model_id": "claude-example-1-max", "benchmark_id": "scicode",
             "score": 61.0, "score_as_of": "2026-11-20"},
            {"lab_id": "ANTHROPIC", "model_id": "claude-unlisted", "benchmark_id": "critpt",
             "score": 22.0, "score_as_of": "2026-11-20"},
        ],
    }
    fixture = tmp_path / "scores.json"
    import json
    fixture.write_text(json.dumps(payload))
    repo = SQLiteStructuredRepository(tmp_path / "cap.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    result = IngestionPipeline(repo).run(
        FrontierAICapabilityAdapter(fixture_path=fixture, clock=lambda: SCORED),
        FetchRequest(source_id="frontier_ai_capability", dataset_id="frontier_ai_capability_benchmarks",
                     query_scope={"test_only": True}))
    assert result["status"] == "succeeded"
    return payload, DataProducts(structured_repository=repo)


def test_stored_scores_carry_no_release_date(tmp_path):
    payload, _ = _scored_products(tmp_path)
    records, entities, _ = normalize_payload(payload, fetched_at=SCORED)
    assert all("release_date" not in record.dimensions for record in records)
    assert all("release_date" not in entity.metadata for entity in entities)


def test_release_date_comes_from_the_registry_never_the_score_date(tmp_path, monkeypatch):
    _, products = _scored_products(tmp_path)
    path = tmp_path / "releases.yaml"
    path.write_text(
        "version: 1\nofficial_domains: {ANTHROPIC: [anthropic.com]}\nreleases:\n"
        "  - {lab_id: ANTHROPIC, model_id: claude-example-1, display_name: Claude Example 1,\n"
        "     tier: general_flagship, release_date: 2026-11-04, status: confirmed,\n"
        "     announcement_url: 'https://www.anthropic.com/news/claude-example-1',\n"
        "     registered_at: 2026-11-05T00:00:00Z}\n")
    monkeypatch.setattr("ats.data.model_releases.registry_path", lambda: path)

    matrix = products.frontier_ai_capability_matrix()
    rows = {row["model_id"]: row for row in matrix["candidate_observations"]}
    listed, unlisted = rows["claude-example-1-max"], rows["claude-unlisted"]
    assert listed["release_date"] == "2026-11-04" != listed["score_as_of"]
    assert listed["model_release_id"] == "claude-example-1" and listed["flagship"] is True
    assert unlisted["release_date"] == "" and unlisted["flagship"] is False
    assert unlisted["release_date_label"] == UNREGISTERED_LABEL
    assert matrix["unregistered_release_models"] == [
        {"lab_id": "ANTHROPIC", "model_release_id": "claude-unlisted", "reason": UNREGISTERED_LABEL}]
    anthropic = matrix["by_lab"]["ANTHROPIC"]
    assert anthropic["model"]["model_name"] == "Claude Example 1"
    assert next(c for c in anthropic["cells"] if c["benchmark_id"] == "scicode")["score"] == 61.0
    assert "OPENAI" in matrix["unregistered_flagship_labs"]
    assert {row["release_date"] for row in products.frontier_ai_capability_scores()} == {"2026-11-04", ""}


def test_empty_registry_leaves_every_flagship_column_unregistered(tmp_path, monkeypatch):
    _, products = _scored_products(tmp_path)
    path = tmp_path / "empty.yaml"
    path.write_text("version: 1\nofficial_domains: {ANTHROPIC: [anthropic.com]}\nreleases: []\n")
    monkeypatch.setattr("ats.data.model_releases.registry_path", lambda: path)

    matrix = products.frontier_ai_capability_matrix()
    assert len(matrix["unregistered_flagship_labs"]) == 9
    assert matrix["coverage"]["numeric"] == 0
    cell = matrix["by_lab"]["ANTHROPIC"]["cells"][0]
    assert cell["na_reason"] == "flagship_unregistered" and cell["display"] == "NA"
    assert matrix["by_lab"]["ANTHROPIC"]["model"]["model_name"] == "Anthropic（旗舰未登记）"
