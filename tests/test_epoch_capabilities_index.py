"""Epoch Capabilities Index adapter and read product (ant-data-onboarding 10.4)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

import pytest

from ats.data.adapters.structured.registry import validate_source_registration
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest, IngestionStatus
from ats.data.model_releases import UNREGISTERED_LABEL
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.sources.epoch_capabilities_index import (
    EpochCapabilitiesIndexAdapter,
    EpochIndexError,
    parse_scores_payload,
)
from ats.data.stores.structured.repository import SQLiteStructuredRepository

DATASET = "epoch_capabilities_index_models"
NOW = datetime(2026, 11, 27, 7, 40, tzinfo=UTC)
HEADER = ("Model,Display name,eci,eci_ci_low,eci_ci_high,date,Organization,"
          "Country (of organization),Model accessibility,Accessibility group,model_versions")


def line(model, score, listed="2026-11-09", org="Anthropic", low="", high=""):
    organization = f'"{org}"' if "," in org else org
    return f"{model},{model},{score},{low},{high},{listed},{organization},,API access,Closed weights,"


def csv_bytes(fable="173.4", extra=(), anchors=("130.0", "150.0")):
    rows = [
        line("Claude Fable 5.2", fable, low="170.04", high="177.22"),
        line("Claude Opus 5.5", "168.67", "2026-09-22"),
        line("GPT-6.2 Astra", "172.82", "2026-11-17", "OpenAI"),
        line("Gemini 4 Carbon", "169.68", "2026-11-10", "Google DeepMind,Google"),
        line("Claude 3.5 Sonnet", anchors[0], "2024-06-20"),
        line("GPT-5", anchors[1], "2025-08-07", "OpenAI"),
        line("vicuna-13b", "80.1", "2023-03-30", ""),
        *extra,
    ]
    return ("﻿" + "\n".join([HEADER, *rows]) + "\n").encode()


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.payload


def parse(payload):
    return parse_scores_payload(payload, fetched_at=NOW, min_models=1)


def ingest(repository, payload, moment=NOW, expect="succeeded"):
    adapter = EpochCapabilitiesIndexAdapter(
        opener=lambda _request, timeout=30: _Response(payload), min_models=1,
        clock=lambda: moment)
    result = IngestionPipeline(repository).run(
        adapter, FetchRequest(source_id="epoch_capabilities_index", dataset_id=DATASET))
    assert result["status"] == expect, result
    return result


@pytest.fixture
def repository(tmp_path):
    store = SQLiteStructuredRepository(tmp_path / "eci.sqlite", artifact_root=tmp_path / "artifacts")
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
    assert validate_source_registration("epoch_capabilities_index")["valid"]
    metrics = {item.id for item in StructuredCatalog.load().metrics()}
    assert "ai.epoch.capabilities_index" in metrics


def test_each_model_becomes_one_dated_snapshot_record():
    records, diagnostics = parse(csv_bytes())
    by_model = {record.dimensions["model_name"]: record for record in records}
    fable = by_model["Claude Fable 5.2"]
    assert (fable.period, fable.value, fable.unit) == ("2026-11-27", 173.4, "index_points")
    assert fable.dimensions["epoch_listed_date"] == "2026-11-09"
    assert "release_date" not in fable.dimensions
    assert fable.raw["eci_ci_low"] == 170.04 and fable.raw["eci_ci_high"] == 177.22
    assert by_model["Claude Opus 5.5"].raw["eci_ci_low"] is None
    assert by_model["Gemini 4 Carbon"].dimensions["lab_id"] == "GOOGLE"
    assert by_model["vicuna-13b"].dimensions["lab_id"] == ""
    assert diagnostics["license"] == "CC BY 4.0" and diagnostics["listed_models"] == 7


@pytest.mark.parametrize("payload, reason", [
    (b"Model,eci\nX,1\n", "columns_missing"),
    (csv_bytes(anchors=("131.2", "150.0")), "calibration_changed:Claude 3.5 Sonnet"),
    (csv_bytes(anchors=("130.0", "151.0")), "calibration_changed:GPT-5"),
    (csv_bytes(fable="n/a"), "eci_not_numeric"),
    (csv_bytes(extra=[line("GPT-5", "150.0")]), "duplicate_model"),
    (csv_bytes(extra=[line("Reversed", "1", low="3", high="2")]), "interval_reversed"),
    (csv_bytes(extra=[line("Undated", "1", listed="soon")]), "date_invalid"),
    (csv_bytes(extra=["Short,Short,1"]), "row_incomplete"),
])
def test_unusable_file_is_rejected(payload, reason):
    with pytest.raises(EpochIndexError, match=reason):
        parse(payload)


def test_truncated_file_is_rejected():
    with pytest.raises(EpochIndexError, match="model_list_too_short"):
        parse_scores_payload(csv_bytes(), fetched_at=NOW)


def test_recalibration_and_outage_publish_nothing(repository):
    ingest(repository, csv_bytes(anchors=("131.2", "150.0")),
           expect=IngestionStatus.METHODOLOGY_DRIFT.value)

    def offline(_request, timeout=30):
        raise URLError("offline")

    result = IngestionPipeline(repository).run(
        EpochCapabilitiesIndexAdapter(opener=offline, clock=lambda: NOW),
        FetchRequest(source_id="epoch_capabilities_index", dataset_id=DATASET))
    assert result["status"] == IngestionStatus.UNREACHABLE.value
    assert DataProducts(structured_repository=repository).epoch_capability_index()["status"] == "no_data"


def test_refit_keeps_the_earlier_snapshot_and_reads_the_latest(repository):
    ingest(repository, csv_bytes())
    ingest(repository, csv_bytes(), expect="no_change")  # same day, same file
    later = datetime(2026, 12, 4, 7, 40, tzinfo=UTC)
    ingest(repository, csv_bytes(fable="171.9"), later)
    products = DataProducts(structured_repository=repository)

    now = products.epoch_capability_index()
    assert (now["snapshot_date"], now["history_start"]) == ("2026-12-04", "2026-11-27")
    assert len(now["rows"]) == 7
    assert now["lab_best"]["ANTHROPIC"]["eci"] == 171.9
    assert now["lab_best"]["OPENAI"]["model_name"] == "GPT-6.2 Astra"
    assert now["rows"][0]["model_name"] == "GPT-6.2 Astra"  # ranked by score after the refit
    assert "CC BY 4.0" == now["license"] and "Epoch AI" in now["attribution"]

    then = products.epoch_capability_index(as_of=datetime(2026, 11, 28, tzinfo=UTC))
    assert then["snapshot_date"] == "2026-11-27"
    assert then["lab_best"]["ANTHROPIC"]["eci"] == 173.4


def test_release_date_and_flagship_come_from_the_registry_only(repository):
    ingest(repository, csv_bytes())
    packet = DataProducts(structured_repository=repository).epoch_capability_index(lab_id="ANTHROPIC")
    rows = {row["model_name"]: row for row in packet["rows"]}
    listed, other = rows["Claude Fable 5.2"], rows["Claude Opus 5.5"]
    assert listed["release_date"] == "2026-11-04" != listed["epoch_listed_date"]
    assert listed["flagship"] is True
    assert (listed["eci_ci_low"], listed["eci_ci_high"]) == (170.04, 177.22)
    assert (listed["organization"], listed["eci"]) == ("Anthropic", 173.4)
    assert other["release_date"] == "" and other["release_date_label"] == UNREGISTERED_LABEL
    assert other["flagship"] is False
    assert {row["lab_id"] for row in packet["rows"]} == {"ANTHROPIC"}


def test_index_scores_stay_out_of_the_percent_matrix(repository):
    ingest(repository, csv_bytes())
    matrix = DataProducts(structured_repository=repository).frontier_ai_capability_matrix()
    assert (matrix["coverage"]["numeric"], matrix["coverage"]["total"]) == (0, 99)
    assert matrix["candidate_observations"] == []


def test_committed_schedule_runs_the_registered_job():
    text = (Path(__file__).resolve().parents[1] / "config/data/schedules.yaml").read_text()
    assert "ats data ingest --source epoch_capabilities_index --dataset " + DATASET in text
