"""Artificial Analysis free-API adapter and read product (ant-data-onboarding 10.4)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from ats.data.adapters.structured.registry import validate_source_registration
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest, IngestionStatus
from ats.data.model_releases import UNREGISTERED_LABEL
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.sources.artificial_analysis_indices import (
    ArtificialAnalysisError,
    ArtificialAnalysisIndicesAdapter,
    parse_pages,
)
from ats.data.stores.structured.repository import SQLiteStructuredRepository

DATASET = "artificial_analysis_model_indices"
SOURCE = "artificial_analysis_indices"
NOW = datetime(2026, 11, 27, 7, 50, tzinfo=UTC)
KEY = "test-only-not-a-real-key"
ANTHROPIC = "f0aa413f-e8ae-4fcd-9c48-0e049f4f3128"
OPENAI = "e67e56e3-15cd-43db-b679-da4660a69f41"


def model(uid, name, creator, index=None, legal=None, per_task=None, total=None,
          listed="2026-11-09"):
    row = {"id": uid, "name": name, "slug": uid, "release_date": listed,
           "model_creator": {"id": creator, "name": "Anthropic" if creator == ANTHROPIC
                             else "Other"},
           "evaluations": {"artificial_analysis_intelligence_index": index,
                           "artificial_analysis_coding_index": None,
                           "artificial_analysis_legal_index": legal},
           "pricing": {"price_1m_input_tokens": 1.0}}
    row["artificial_analysis_intelligence_index_cost"] = (
        None if per_task is None and total is None
        else {"total_cost": total, "cost_per_task": {"total_cost": per_task}})
    return row


def rows(fable=64.8):
    return [
        model("m-fable-max", "Claude Fable 5.2 (Max, Default Fallback)", ANTHROPIC,
              fable, 63.8, 26.5, 22191.4),
        model("m-opus", "Claude Opus 5.5 (Max)", ANTHROPIC, 59.1, None, 10.0, 8909.7,
              "2026-09-22"),
        model("m-astra", "GPT-6.2 Astra (Max)", OPENAI, 62.2, 55.0, 4.0, 4530.5, "2026-11-17"),
        model("m-small", "Unlisted Small", "creator-unknown", None, 12.0),
        model("m-empty", "Nothing Measured", OPENAI),
    ]


def pages(items=None, version=5, per_page=2):
    items = rows() if items is None else items
    chunks = [items[i:i + per_page] for i in range(0, len(items), per_page)]
    return [{"tier": "free", "intelligence_index_version": version, "data": chunk,
             "pagination": {"page": n, "page_size": per_page, "total_pages": len(chunks),
                            "has_more": n < len(chunks)}}
            for n, chunk in enumerate(chunks, 1)]


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.payload


def opener_for(document, seen=None):
    def open_page(request, timeout=30):
        if seen is not None:
            seen.append(request)
        number = int(request.full_url.rsplit("=", 1)[1])
        return _Response(json.dumps(document[number - 1]).encode())

    return open_page


def adapter(opener, moment=NOW, key=KEY):
    return ArtificialAnalysisIndicesAdapter(
        api_key=key, opener=opener, clock=lambda: moment, min_models=1, min_indexed=1)


def ingest(repository, opener, moment=NOW, expect="succeeded", key=KEY):
    result = IngestionPipeline(repository).run(
        adapter(opener, moment, key), FetchRequest(source_id=SOURCE, dataset_id=DATASET))
    assert result["status"] == expect, result
    return result


def parse(document):
    return parse_pages(document, fetched_at=NOW, min_models=1, min_indexed=1)


@pytest.fixture
def repository(tmp_path):
    store = SQLiteStructuredRepository(tmp_path / "aa.sqlite", artifact_root=tmp_path / "artifacts")
    store.bootstrap_catalog(StructuredCatalog.load())
    return store


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "releases.yaml"
    path.write_text(
        "version: 1\nofficial_domains: {ANTHROPIC: [anthropic.com]}\nreleases:\n"
        "  - {lab_id: ANTHROPIC, model_id: claude-fable-5.2, display_name: Claude Fable 5.2,\n"
        "     tier: general_flagship, release_date: 2026-11-04, status: confirmed,\n"
        "     announcement_url: 'https://www.anthropic.com/news/test-only',\n"
        "     registered_at: 2026-11-05T00:00:00Z}\n")
    monkeypatch.setattr("ats.data.model_releases.registry_path", lambda: path)


def test_source_registration_is_governed():
    assert validate_source_registration(SOURCE)["valid"]
    metrics = {item.id for item in StructuredCatalog.load().metrics()}
    assert {"ai.aa.intelligence_index", "ai.aa.legal_index",
            "ai.aa.intelligence_index_cost_per_task"} <= metrics


def test_unmeasured_values_are_left_out_not_zeroed():
    records, diagnostics = parse(pages())
    by_model: dict[str, dict[str, float]] = {}
    for record in records:
        by_model.setdefault(record.dimensions["model_id"], {})[record.provider_field] = record.value
    assert by_model["m-fable-max"] == {
        "intelligence_index": 64.8, "legal_index": 63.8,
        "intelligence_index_cost_per_task": 26.5, "intelligence_index_run_cost": 22191.4}
    assert "legal_index" not in by_model["m-opus"]
    assert by_model["m-small"] == {"legal_index": 12.0}
    assert "m-empty" not in by_model
    assert (diagnostics["listed_models"], diagnostics["indexed_models"]) == (5, 3)


def test_only_index_and_cost_carry_the_index_version():
    records, _ = parse(pages(version=5.1))
    fields = {record.provider_field: record.dimensions for record in records
              if record.dimensions["model_id"] == "m-fable-max"}
    assert fields["intelligence_index"]["index_version"] == "5.1"
    assert fields["intelligence_index_cost_per_task"]["index_version"] == "5.1"
    assert "index_version" not in fields["legal_index"]
    assert fields["legal_index"]["lab_id"] == "ANTHROPIC"
    cost = next(r for r in records if r.provider_field == "intelligence_index_run_cost")
    assert (cost.unit, cost.currency) == ("USD", "USD")


def _broken(change):
    document = pages()
    change(document)
    return document


@pytest.mark.parametrize("document, reason", [
    (pages(version=6), "index_major_version"),
    (pages(version=None), "index_version_missing"),
    (_broken(lambda d: d.pop()), "incomplete_list"),
    (_broken(lambda d: d[1]["pagination"].update(total_pages=9)), "pagination_changed"),
    (_broken(lambda d: d[1].update(intelligence_index_version=5.1)), "index_version_changed"),
    (pages([*rows(), rows()[0]]), "duplicate_model"),
    (pages([model("m", "Out", OPENAI, 140.0)]), "intelligence_index_out_of_range"),
    (pages([model("m", "Text", OPENAI, "high")]), "intelligence_index_not_numeric"),
    (pages([model("m", "Dated", OPENAI, 1.0, listed="soon")]), "date_invalid"),
    (pages([model("", "Anonymous", OPENAI, 1.0)]), "identity_missing"),
    (pages([model("m", "No index", OPENAI, None, 3.0)]), "too_few_indexed_models"),
])
def test_unusable_list_is_rejected(document, reason):
    with pytest.raises(ArtificialAnalysisError, match=reason):
        parse(document)


def test_short_list_is_rejected():
    with pytest.raises(ArtificialAnalysisError, match="model_list_too_short"):
        parse_pages(pages(), fetched_at=NOW)


def test_key_goes_in_the_documented_header_and_is_never_stored(repository, tmp_path):
    seen: list = []
    ingest(repository, opener_for(pages(), seen))
    assert [r.full_url for r in seen] == [
        f"https://artificialanalysis.ai/api/v2/language/models/free?page={n}" for n in (1, 2, 3)]
    assert all(r.get_header("X-api-key") == KEY for r in seen)
    assert all(KEY not in r.full_url for r in seen)
    stored = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    assert KEY.encode() not in stored


@pytest.mark.parametrize("code, status", [
    (401, IngestionStatus.UNAUTHORIZED), (403, IngestionStatus.UNAUTHORIZED),
    (429, IngestionStatus.STALE), (500, IngestionStatus.UNREACHABLE),
])
def test_api_refusals_publish_nothing(repository, code, status):
    def refuse(request, timeout=30):
        raise HTTPError(request.full_url, code, "refused", {}, None)

    ingest(repository, refuse, expect=status.value)
    assert DataProducts(structured_repository=repository
                        ).artificial_analysis_model_indices()["status"] == "no_data"


def test_missing_key_makes_no_request(repository):
    def forbidden(request, timeout=30):
        raise AssertionError("no request without a key")

    ingest(repository, forbidden, expect=IngestionStatus.UNAUTHORIZED.value, key="")


def test_outage_on_a_later_page_publishes_nothing(repository):
    good = opener_for(pages())

    def flaky(request, timeout=30):
        if request.full_url.endswith("=2"):
            raise URLError("offline")
        return good(request, timeout)

    ingest(repository, flaky, expect=IngestionStatus.UNREACHABLE.value)
    assert DataProducts(structured_repository=repository
                        ).artificial_analysis_model_indices()["status"] == "no_data"


def test_snapshots_keep_history_and_rank_the_latest(repository):
    ingest(repository, opener_for(pages()))
    ingest(repository, opener_for(pages()), expect="no_change")  # same day, same list
    later = datetime(2026, 12, 4, 7, 50, tzinfo=UTC)
    ingest(repository, opener_for(pages(rows(fable=61.0))), later)
    products = DataProducts(structured_repository=repository)

    now = products.artificial_analysis_model_indices()
    assert (now["snapshot_date"], now["history_start"]) == ("2026-12-04", "2026-11-27")
    assert [r["model_id"] for r in now["rows"]] == ["m-astra", "m-fable-max", "m-opus", "m-small"]
    assert now["lab_best"]["ANTHROPIC"]["intelligence_index"] == 61.0
    assert now["index_version"] == "5"
    assert "internal use only" in now["terms"] and "Artificial Analysis" in now["attribution"]

    then = products.artificial_analysis_model_indices(as_of=datetime(2026, 11, 28, tzinfo=UTC))
    assert then["snapshot_date"] == "2026-11-27"
    assert then["lab_best"]["ANTHROPIC"]["intelligence_index"] == 64.8


def test_release_date_and_flagship_come_from_the_registry_only(repository):
    ingest(repository, opener_for(pages()))
    packet = DataProducts(structured_repository=repository
                          ).artificial_analysis_model_indices(lab_id="ANTHROPIC")
    by_id = {row["model_id"]: row for row in packet["rows"]}
    fable, opus = by_id["m-fable-max"], by_id["m-opus"]
    assert fable["variant"] == "Max, Default Fallback"
    assert fable["release_date"] == "2026-11-04" != fable["aa_listed_release_date"]
    assert fable["flagship"] is True
    assert (fable["intelligence_index_cost_per_task"], fable["legal_index"]) == (26.5, 63.8)
    assert opus["release_date"] == "" and opus["release_date_label"] == UNREGISTERED_LABEL
    assert opus["flagship"] is False and opus["legal_index"] is None


def test_index_scores_stay_out_of_the_percent_matrix(repository):
    ingest(repository, opener_for(pages()))
    matrix = DataProducts(structured_repository=repository).frontier_ai_capability_matrix()
    assert (matrix["coverage"]["numeric"], matrix["coverage"]["total"]) == (0, 99)
    assert matrix["candidate_observations"] == []


def test_no_model_consumer_declares_the_dataset():
    root = Path(__file__).resolve().parents[1]
    coverage = (root / "config/data/target_dataflow_coverage.yaml").read_text()
    assert DATASET not in coverage
    callers = [p for p in (root / "src/ats").rglob("*.py")
               if "artificial_analysis_model_indices(" in p.read_text(encoding="utf-8")]
    assert [p.relative_to(root).as_posix() for p in callers] == ["src/ats/data/products/base.py"]


def test_committed_schedule_runs_the_registered_job():
    text = (Path(__file__).resolve().parents[1] / "config/data/schedules.yaml").read_text()
    assert f"ats data ingest --source {SOURCE} --dataset {DATASET}" in text
