from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from ats.data.adapters.structured.registry import validate_source_registration
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.sources.openrouter_rankings import (
    OpenRouterDataError, OpenRouterRankingsAdapter, parse_rankings_payload,
    validate_rankings_records,
)
from ats.data.products.openrouter_rankings import canonical_model_group
from ats.data.stores.structured.repository import SQLiteStructuredRepository


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


def _payload(start=date(2026, 9, 1), days=14):
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        rows.extend([
            {"date": day.isoformat(), "model_permaslug": "openai/gpt-4o", "total_tokens": str(100 + offset)},
            {"date": day.isoformat(), "model_permaslug": "anthropic/claude-3", "total_tokens": str(50 + offset)},
            {"date": day.isoformat(), "model_permaslug": "newco/unknown-model", "total_tokens": str(25 + offset)},
            {"date": day.isoformat(), "model_permaslug": "other", "total_tokens": "20"},
        ])
    return json.dumps({"data": rows, "meta": {"as_of": "2026-09-16T02:00:00Z", "version": "v1"}}).encode()


class _Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self):
        return self.payload


def _products(tmp_path, payload=None):
    payload = payload or _payload()
    root = Path(tmp_path)
    repository = SQLiteStructuredRepository(root / "openrouter.sqlite", artifact_root=root / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    adapter = OpenRouterRankingsAdapter(
        api_key="fixture",
        opener=lambda _request, timeout=30: _Response(payload),
        clock=lambda: NOW,
    )
    result = IngestionPipeline(repository).run(
        adapter,
        FetchRequest(source_id="openrouter_rankings", dataset_id="openrouter_rankings_daily",
                     query_scope={"start_date": "2026-09-01", "end_date": "2026-09-14"}),
    )
    assert result["status"] == "succeeded"
    return DataProducts(structured_repository=repository)


def test_catalog_and_mapping_are_governed():
    catalog = StructuredCatalog.load()
    assert validate_source_registration("openrouter_rankings")["valid"]
    assert "openrouter_rankings_daily" in {item.id for item in catalog.datasets()}
    assert "ai.openrouter.total_tokens" in {item.id for item in catalog.metrics()}


def test_parser_preserves_other_unknown_and_free_route():
    payload = json.dumps({"data": [
        {"date": "2026-09-01", "model_permaslug": "newco/model:free", "total_tokens": "42"},
        {"date": "2026-09-01", "model_permaslug": "other", "total_tokens": "8"},
    ], "meta": {"version": "v1"}})
    records, diagnostics = parse_rankings_payload(payload)
    assert records[0].dimensions["author"] == "unknown_author"
    assert records[0].dimensions["is_free_route"] is True
    assert records[1].dimensions["is_other"] is True
    assert diagnostics["license"] == "CC BY 4.0"


def test_model_group_merges_release_date_suffixes():
    assert canonical_model_group("deepseek/deepseek-v4-flash-20260423") == (
        "deepseek/deepseek-v4-flash", "deepseek-v4-flash")
    assert canonical_model_group("openai/gpt-5.6-luna-20260709:free") == (
        "openai/gpt-5.6-luna", "gpt-5.6-luna")
    assert canonical_model_group("other") == ("other", "Other")


def test_checked_in_fixture_is_offline_replayable():
    fixture = Path(__file__).parent / "fixtures" / "openrouter_rankings" / "rankings_daily.json"
    records, diagnostics = parse_rankings_payload(fixture.read_bytes())
    assert len(records) == 28
    assert diagnostics["dates"] == ["2026-09-01", "2026-09-02", "2026-09-03",
                                     "2026-09-04", "2026-09-05", "2026-09-06",
                                     "2026-09-07"]
    assert not validate_rankings_records(records)


def test_parser_rejects_duplicate_identity_and_negative_tokens():
    duplicate = json.dumps({"data": [
        {"date": "2026-09-01", "model_permaslug": "openai/a", "total_tokens": "1"},
        {"date": "2026-09-01", "model_permaslug": "openai/a", "total_tokens": "2"},
    ]})
    with pytest.raises(OpenRouterDataError, match="duplicate_model_date"):
        parse_rankings_payload(duplicate)
    rows, _ = parse_rankings_payload(json.dumps({"data": [{"date": "2026-09-01", "model_permaslug": "openai/a", "total_tokens": "1"}]}))
    rows[0].value = -1
    assert any("token_negative" in item for item in validate_rankings_records(rows))


def test_parser_rejects_methodology_drift():
    payload = json.dumps({"data": [{"date": "2026-09-01", "model_permaslug": "openai/a", "total_tokens": "1"}],
                          "meta": {"version": "v2"}})
    with pytest.raises(OpenRouterDataError, match="methodology_drift:version"):
        parse_rankings_payload(payload)


def test_parser_rejects_declared_total_conservation_failure():
    payload = json.dumps({"data": [{"date": "2026-09-01", "model_permaslug": "openai/a", "total_tokens": "1"}],
                          "meta": {"version": "v1", "total_tokens": "2"}})
    with pytest.raises(OpenRouterDataError, match="token_conservation_failed"):
        parse_rankings_payload(payload)


def test_dataproducts_exclude_incomplete_week_and_emit_derived_series(tmp_path):
    products = _products(tmp_path)
    volume = products.openrouter_token_volume_series()
    assert volume["status"] == "ok"
    assert len(volume["rows"]) == 1
    assert volume["rows"][0]["complete"] is True
    authors = products.openrouter_author_share_series()
    assert {row["author"] for row in authors["rows"]} >= {"openai", "anthropic", "unknown_author", "unattributed_other"}
    leaderboard = products.openrouter_model_leaderboard()
    assert leaderboard["rows"][0]["model_permaslug"] == "openai/gpt-4o"
    assert {"previous_tokens", "token_change", "share_change"} <= set(leaderboard["rows"][0])
    ranking = products.openrouter_model_ranking_series(top_n=2)
    assert ranking["status"] == "ok"
    assert ranking["selected_models"] == ["openai/gpt-4o", "anthropic/claude-3"]
    assert {"rank", "tokens", "share"} <= set(ranking["rows"][0])
    assert any(row["is_other_series"] for row in ranking["rows"])
    concentration = products.openrouter_concentration_series()
    assert 0 <= concentration["rows"][0]["top3_share"] <= 1
    assert concentration["rows"][0]["other_share"] > 0


def test_missing_key_is_structured_unauthorized(tmp_path):
    repository = SQLiteStructuredRepository(Path(tmp_path) / "x.sqlite", artifact_root=Path(tmp_path) / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    result = IngestionPipeline(repository).run(
        OpenRouterRankingsAdapter(api_key="", clock=lambda: NOW),
        FetchRequest(source_id="openrouter_rankings", dataset_id="openrouter_rankings_daily",
                     query_scope={"start_date": "2026-09-01", "end_date": "2026-09-01"}),
    )
    assert result["status"] == "unauthorized"


def test_artifact_metadata_and_idempotent_replay(tmp_path):
    repository = SQLiteStructuredRepository(Path(tmp_path) / "x.sqlite", artifact_root=Path(tmp_path) / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    payload = (Path(__file__).parent / "fixtures" / "openrouter_rankings" / "rankings_daily.json").read_bytes()
    adapter = OpenRouterRankingsAdapter(
        api_key="fixture",
        opener=lambda _request, timeout=30: _Response(payload),
        clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    request = FetchRequest(source_id="openrouter_rankings", dataset_id="openrouter_rankings_daily",
                           query_scope={"start_date": "2026-09-01", "end_date": "2026-09-07", "period": "day"})
    pipeline = IngestionPipeline(repository)
    first = pipeline.run(adapter, request)
    second = pipeline.run(adapter, request)
    assert first["status"] == "succeeded"
    assert second["status"] == "no_change"
    artifact = repository.artifacts_for(source_id="openrouter_rankings", dataset_id="openrouter_rankings_daily", limit=1)[0]
    metadata = json.loads(artifact["metadata_json"])
    assert metadata["payload_sha256"]
    assert metadata["credential_redacted"] is True
    assert metadata["license"] == "CC BY 4.0"
    assert "Authorization" not in json.dumps(metadata)
