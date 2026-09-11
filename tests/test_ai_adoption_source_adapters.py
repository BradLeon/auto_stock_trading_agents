from datetime import datetime, timezone
import pytest

from ats.data.core.structured_models import (AdapterArtifact, AdapterBatch, FetchRequest,
                                             IngestionStatus, NativeRecord, ReleaseCandidate)
from ats.data.sources.census_btos import CensusBTOSAdapter, NEW_REGIME_START, _candidate, parse_btos_period
from ats.data.products.census_btos import snapshot as btos_snapshot
from ats.data.sources.rps_genai_adoption import RPSGenAIAdoptionAdapter, SERIES, parse_fred_csv
from ats.data.products.rps_genai_adoption import snapshot as rps_snapshot
from ats.data.sources.ons_bics_ai import _normalized_workbook_rows, parse_ons_rows
from ats.data.products.ons_bics_ai import snapshot as ons_snapshot
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.products.discovery import DataDiscovery
from ats.data.catalog.structured import StructuredCatalog
from ats.data.pipelines.structured.discovery import _failure_result
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.core.structured_models import DiscoveryResult, DiscoveryStatus


NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_btos_download_streams_and_hashes_complete_official_payload():
    payload = b'[{"PERIOD_ID":"99"}]'
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def raise_for_status(self): return None
        def iter_bytes(self): return iter((payload[:7], payload[7:]))
    class Client:
        def stream(self, *args, **kwargs): return Response()
    rows, digest, size = CensusBTOSAdapter._download_records(Client(), "https://official.test/data")
    import hashlib
    assert rows == [{"PERIOD_ID": "99"}]
    assert digest == hashlib.sha256(payload).hexdigest() and size == len(payload)


def test_btos_accepts_only_new_any_business_function_question_and_filters_geography():
    period = {"PERIOD_ID": "99", "COLLECTION_START": "17-NOV-25 12.00.00.000000 AM"}
    current = {"PERIOD_ID": "99", "QUESTION": "In the last two weeks, did this business use artificial intelligence in any of its business functions?", "OPTION_TEXT": "Current use"}
    future = {"PERIOD_ID": "99", "QUESTION": current["QUESTION"], "OPTION_TEXT": "Expected use in next six months"}
    candidate = _candidate(period, [current, future])
    assert candidate is not None and NEW_REGIME_START.isoformat() == "2025-11-17"
    records, _ = parse_btos_period(rows=[
        {"QUESTION": current["QUESTION"], "OPTION_TEXT": "Current use", "ANSWER": "Yes", "ESTIMATE_PERCENTAGE": "12.5", "STANDARD_ERROR": "1.2", "NAICS2": "54"},
        {"QUESTION": current["QUESTION"], "OPTION_TEXT": "Current use", "ANSWER": "Yes", "ESTIMATE_PERCENTAGE": "99", "STATE": "CA"},
    ], candidate=candidate, fetched_at=NOW)
    assert {record.entity_id for record in records} == {"BUSINESS_POP:US:NAICS:54"}
    assert {record.provider_field for record in records} == {"btos_response_share", "current_ai_yes_pct", "current_ai_yes_se"}


def test_btos_rejects_old_production_wording():
    period = {"PERIOD_ID": "99", "COLLECTION_START": "17-NOV-25 12.00.00.000000 AM"}
    old = {"PERIOD_ID": "99", "QUESTION": "Did this business use artificial intelligence in producing goods or services?", "OPTION_TEXT": "Current"}
    assert _candidate(period, [old, old]) is None


def test_btos_quality_gates_reject_invalid_percent_and_duplicate_cells():
    question = "In the last two weeks, did this business use Artificial Intelligence (AI) in any of its business functions?"
    candidate = ReleaseCandidate(identity="BTOS:99:x", period="99", methodology_fingerprint="x",
                                 metadata={"period_start": "2025-11-17", "period_end": "2025-11-30"})
    with pytest.raises(ValueError, match="percent_out_of_range"):
        parse_btos_period(rows=[{"QUESTION": question, "OPTION_TEXT": "AI current", "ANSWER": "Yes",
                                 "ESTIMATE_PERCENTAGE": "101"}], candidate=candidate, fetched_at=NOW)
    duplicate = {"QUESTION": question, "OPTION_TEXT": "AI current", "ANSWER": "Yes",
                 "ESTIMATE_PERCENTAGE": "10"}
    with pytest.raises(ValueError, match="duplicate_cell"):
        parse_btos_period(rows=[duplicate, duplicate], candidate=candidate, fetched_at=NOW)


def test_btos_quality_gates_answer_sum_se_and_suppression_semantics():
    question = "In the last two weeks, did this business use Artificial Intelligence (AI) in any of its business functions?"
    candidate = ReleaseCandidate(identity="BTOS:99:x", period="99", methodology_fingerprint="x",
                                 metadata={"period_start": "2025-11-17", "period_end": "2025-11-30"})
    bad_sum = [{"QUESTION": question, "OPTION_TEXT": "AI current", "ANSWER": answer,
                "ESTIMATE_PERCENTAGE": value} for answer, value in (("Yes", "10"), ("No", "80"), ("Do not know", "5"))]
    with pytest.raises(ValueError, match="answer_sum_failed"):
        parse_btos_period(rows=bad_sum, candidate=candidate, fetched_at=NOW)
    with pytest.raises(ValueError, match="standard_error_negative"):
        parse_btos_period(rows=[{"QUESTION": question, "OPTION_TEXT": "AI current", "ANSWER": "Yes",
                                 "ESTIMATE_PERCENTAGE": "10", "STANDARD_ERROR": "-1"}],
                          candidate=candidate, fetched_at=NOW)
    records, payload = parse_btos_period(rows=[
        {"QUESTION": question, "OPTION_TEXT": "AI current", "ANSWER": "Yes", "ESTIMATE_PERCENTAGE": None},
    ], candidate=candidate, fetched_at=NOW)
    assert records == [] and b'"ESTIMATE_PERCENTAGE": null' in payload


def test_btos_dataproduct_returns_strata_derivations_vintages_and_lineage():
    rows = []
    for period, value in (("97", 10.0), ("98", 12.0), ("99", 14.0), ("100", 16.0)):
        for metric, metric_value in (("ai.enterprise_adoption.current_use_share", value),
                                     ("ai.enterprise_adoption.expected_use_share", value + 2)):
            rows.append({"observation_id": f"{period}-{metric}", "artifact_id": f"a-{period}",
                         "source_id": "us_census_btos", "dataset_id": "ai_enterprise_adoption_us",
                         "entity_id": "BUSINESS_POP:US:ALL", "metric_id": metric, "period": period,
                         "value": metric_value, "dimensions_json": '{"stratum_type":"national"}'})
    rows.append({"observation_id": "industry", "artifact_id": "a-100", "source_id": "us_census_btos",
                 "dataset_id": "ai_enterprise_adoption_us", "entity_id": "BUSINESS_POP:US:NAICS:54",
                 "metric_id": "ai.enterprise_adoption.current_use_share", "period": "100", "value": 20.0,
                 "dimensions_json": '{"stratum_type":"naics2"}'})

    class Repo:
        def observations(self, **kwargs):
            selected = rows
            if kwargs.get("entity_id"):
                selected = [row for row in selected if row["entity_id"] == kwargs["entity_id"]]
            return list(selected)
        def source_health(self):
            return [{"source_id": "us_census_btos", "last_checked_at": NOW.isoformat(),
                     "latest_available_period": "2026-08-23"}]

    result = btos_snapshot(Repo(), period="100", include_vintages=True)
    assert result["derivations"]["period_change_pp"]["value"] == 2.0
    assert result["derivations"]["four_period_moving_average"]["value"] == 13.0
    assert result["derivations"]["current_minus_expected_pp"]["value"] == -2.0
    assert result["strata"]["industry"][0]["entity_id"].endswith("54")
    assert result["vintages"] and result["lineage"]["input_observation_ids"]
    assert "\u663e\u8457\u6027" in result["limitations"][-1]
    assert btos_snapshot(Repo())["period"] == "100"


def test_btos_revision_appends_vintage_and_as_of_replays_old_value(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "revision.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    request = FetchRequest(source_id="us_census_btos", dataset_id="ai_enterprise_adoption_us")

    class Adapter:
        def __init__(self, value, at):
            self.value, self.at = value, at
        def fetch(self, request):
            record = NativeRecord(entity_id="BUSINESS_POP:US:ALL", provider_field="current_ai_yes_pct",
                                  period="100", value=self.value, unit="percent",
                                  period_basis="survey_reference_window", published_at=self.at,
                                  dimensions={"stratum_type": "national"}, slice_key="btos:100",
                                  raw={"revision_value": self.value})
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=IngestionStatus.SUCCEEDED, fetched_at=self.at, records=[record],
                                artifacts=[AdapterArtifact(artifact_key="btos:100",
                                                           payload=f"value={self.value}",
                                                           source_version=f"revision:{self.value}")])

    first_at = datetime(2026, 9, 8, tzinfo=timezone.utc)
    second_at = datetime(2026, 9, 9, tzinfo=timezone.utc)
    pipeline = IngestionPipeline(repository)
    assert pipeline.run(Adapter(20.0, first_at), request)["accepted"] == 1
    assert pipeline.run(Adapter(20.0, first_at), request)["status"] == "no_change"
    assert pipeline.run(Adapter(21.0, second_at), request)["accepted"] == 1
    vintages = btos_snapshot(repository, period="100", include_vintages=True)["vintages"]
    assert len(vintages) == 2
    old = btos_snapshot(repository, period="100", as_of=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))
    assert old["current_use"]["value"] == 20.0
    assert btos_snapshot(repository, period="100")["current_use"]["value"] == 21.0


def test_rps_csv_is_quarterly_work_only_and_preserves_missing_values():
    payload = b"observation_date,RPSGENAIUSAGESHAREWORK\n2026-04-01,45.2\n2026-07-01,.\n"
    records = parse_fred_csv("RPSGENAIUSAGESHAREWORK", payload, fetched_at=NOW, slice_key="x")
    assert len(records) == 1
    assert records[0].period == "2026-Q2"
    assert records[0].dimensions["technology_scope"] == "self_reported_GenAI_work_use"


def test_rps_rejects_all_purpose_range_frequency_duplicate_and_logic_drift():
    with pytest.raises(ValueError, match="out_of_scope"):
        parse_fred_csv("ALL_PURPOSE_DAILY", b"observation_date,ALL_PURPOSE_DAILY\n2026-04-01,1\n",
                       fetched_at=NOW, slice_key="x")
    with pytest.raises(ValueError, match="out_of_range"):
        parse_fred_csv(SERIES[0], f"observation_date,{SERIES[0]}\n2026-04-01,101\n".encode(),
                       fetched_at=NOW, slice_key="x")
    with pytest.raises(ValueError, match="frequency_drift"):
        parse_fred_csv(SERIES[0], f"observation_date,{SERIES[0]}\n2026-02-01,10\n".encode(),
                       fetched_at=NOW, slice_key="x")
    with pytest.raises(ValueError, match="duplicate_period"):
        parse_fred_csv(SERIES[0], f"observation_date,{SERIES[0]}\n2026-04-01,10\n2026-04-01,11\n".encode(),
                       fetched_at=NOW, slice_key="x")
    bad = [NativeRecord(entity_id="x", provider_field=field, period="2026-Q2", value=value,
                        unit="percent") for field, value in zip(SERIES[:3], (20, 21, 22))]
    assert RPSGenAIAdoptionAdapter._logic_failures(bad)[0].startswith("rps_persistence_order_failed")


def test_rps_dataproduct_derives_proxies_changes_yoy_and_alignment():
    metric_map = {
        "adoption": "ai.worker_adoption.work_use_share",
        "last_week": "ai.worker_adoption.last_week_work_use_share",
        "daily": "ai.worker_adoption.daily_work_use_share",
        "assisted_hours": "ai.worker_adoption.assisted_work_hours",
        "time_saved": "ai.worker_adoption.time_saved_hours",
    }
    base = {"adoption": 40, "last_week": 32, "daily": 12, "assisted_hours": 5, "time_saved": 2}
    rows = []
    for offset, period in enumerate(("2025-Q2", "2026-Q1", "2026-Q2")):
        for name, metric in metric_map.items():
            rows.append({"observation_id": f"{period}-{name}", "artifact_id": f"a-{name}",
                         "source_id": "rps_genai_adoption", "dataset_id": "ai_worker_adoption_us",
                         "entity_id": "WORKER_POP:US:EMPLOYED_18_64", "metric_id": metric,
                         "period": period, "value": base[name] + offset,
                         "dimensions_json": '{"notes":"official work-only note"}'})
    class Repo:
        def observations(self, **kwargs): return list(rows)
        def source_health(self): return [{"source_id": "rps_genai_adoption",
                                          "last_checked_at": NOW.isoformat(),
                                          "latest_available_period": "2026-Q2"}]
    result = rps_snapshot(Repo())
    assert result["period"] == "2026-Q2" and result["series_alignment"]["aligned"]
    assert result["derivations"]["weekly_persistence_proxy"]["value"] == pytest.approx(34 / 42)
    assert result["derivations"]["quarter_change_pp"]["adoption"]["value"] == 1
    assert result["derivations"]["year_over_year_change_pp"]["adoption"]["value"] == 2
    assert result["derivations"]["weekly_persistence_proxy"]["label"] == "persistence_proxy_not_cohort_retention"


def test_rps_fetch_isolates_single_series_failure():
    import hashlib
    payloads = {series_id: f"observation_date,{series_id}\n2026-04-01,10\n".encode()
                for series_id in SERIES[:2]}
    candidates = [ReleaseCandidate(identity=f"FRED:{series_id}", period="2026-Q2",
                                   methodology_fingerprint="approved", metadata={
                                       "series_id": series_id,
                                       "content_hash": (hashlib.sha256(payloads[series_id]).hexdigest()
                                                        if index == 0 else "changed-after-discovery")})
                  for index, series_id in enumerate(SERIES[:2])]
    class Adapter(RPSGenAIAdoptionAdapter):
        def _bytes(self, client, series_id): return payloads[series_id]
    batch = Adapter(client=object(), clock=lambda: NOW).fetch(FetchRequest(
        source_id="rps_genai_adoption", dataset_id="ai_worker_adoption_us",
        query_scope={"discovery_candidates": [item.model_dump(mode="json") for item in candidates]}))
    assert batch.status.value == "partial" and len(batch.artifacts) == 1
    assert len(batch.failures) == 1 and batch.failures[0].slice_key.endswith(SERIES[1])


def test_rps_single_series_revision_is_vintaged_and_replayable(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "rps-revision.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    request = FetchRequest(source_id="rps_genai_adoption", dataset_id="ai_worker_adoption_us")
    class Adapter:
        def __init__(self, value, at): self.value, self.at = value, at
        def fetch(self, request):
            record = NativeRecord(entity_id="WORKER_POP:US:EMPLOYED_18_64",
                                  provider_field=SERIES[0], period="2026-Q2", value=self.value,
                                  unit="percent", period_basis="calendar_quarter", published_at=self.at,
                                  dimensions={"notes": "official work-only note"}, slice_key="fred:adoption",
                                  raw={"revision_value": self.value})
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=IngestionStatus.SUCCEEDED, fetched_at=self.at, records=[record],
                                artifacts=[AdapterArtifact(artifact_key="fred:adoption", payload=str(self.value),
                                                           source_version=f"revision:{self.value}")])
    first = datetime(2026, 9, 8, tzinfo=timezone.utc)
    second = datetime(2026, 9, 9, tzinfo=timezone.utc)
    pipeline = IngestionPipeline(repository)
    assert pipeline.run(Adapter(40, first), request)["accepted"] == 1
    assert pipeline.run(Adapter(40, first), request)["status"] == "no_change"
    assert pipeline.run(Adapter(41, second), request)["accepted"] == 1
    assert len(rps_snapshot(repository, include_vintages=True)["vintages"]) == 2
    old = rps_snapshot(repository, as_of=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))
    assert old["latest"]["adoption"]["value"] == 40
    assert rps_snapshot(repository)["latest"]["adoption"]["value"] == 41


def test_ons_wide_workbook_parser_preserves_universe_suppression_and_headlines():
    import io
    import openpyxl
    book = openpyxl.Workbook()
    current = book.active
    current.title = "AI Current Usage TS (WTD)"
    current.append(["Question: Which of the following artificial intelligence technologies, if any, does your business currently use?"])
    current.append(["As a percentage of businesses not permanently stopped trading, broken down by industry and size band, weighted by count, UK"])
    current.append([]); current.append([]); current.append([])
    current.append(["Dates", "Wave", "Industry/Size Band", "Text generation using Large Language Models", "Not sure",
                    "Business does not currently use artificial intelligence technologies"])
    current.append(["15 June 2026 to 28 June 2026", "Wave 159", "10 - 49", 0.20, 0.10, 0.60])
    current.append(["15 June 2026 to 28 June 2026", "Wave 159", "Manufacturing", "[c]", 0.10, 0.70])
    extent = book.create_sheet("AI Extent Use TS (WTD)")
    extent.append(["Question: To what extent does your business use artificial intelligence technologies in its business operations?"])
    extent.append(["As a percentage of businesses using some form of artificial intelligence"])
    extent.append([]); extent.append([]); extent.append([])
    extent.append(["Dates", "Wave", "Industry/Size Band", "Used extensively", "Used on a limited basis",
                   "Used only for artificial intelligence technology testing or pilots"])
    extent.append(["15 June 2026 to 28 June 2026", "Wave 159", "10 - 49", 0.10, 0.60, 0.30])
    stream = io.BytesIO(); book.save(stream)
    rows = _normalized_workbook_rows(stream.getvalue(), target_wave="159")
    assert any(row["answer_text"].startswith("Use at least") and row["estimate"] == 30 for row in rows)
    assert any(row.get("suppression_code") == "[c]" for row in rows)
    candidate = ReleaseCandidate(identity="ONS:159:x", period="wave-159", methodology_fingerprint="regime",
                                 metadata={"wave": "159", "questionnaire": "https://ons.test/questions"})
    records, _, unknown = parse_ons_rows(rows=rows, candidate=candidate, fetched_at=NOW)
    assert not unknown
    assert {record.provider_field for record in records} >= {"ai_use_pct", "extensive_use_pct",
                                                             "limited_use_pct", "pilot_use_pct",
                                                             "ons_response_share"}
    assert any(record.entity_id == "BUSINESS_POP:UK:EMP:10 - 49" for record in records)
    assert all(record.dimensions["denominator_scope"] for record in records)


def test_ons_dataproduct_keeps_uk_snapshot_and_conditional_denominators_separate():
    rows = [{"observation_id": "o1", "artifact_id": "a1", "source_id": "ons_bics_ai",
             "dataset_id": "ai_enterprise_adoption_uk", "entity_id": "BUSINESS_POP:UK:ALL",
             "metric_id": "ai.uk_enterprise_adoption.extensive_use_share", "period": "wave-159",
             "value": 10.0, "dimensions_json": '{"question_regime":"r1","denominator_scope":"AI-using businesses","questionnaire_url":"https://ons.test/q"}'}]
    class Repo:
        def observations(self, **kwargs): return list(rows)
        def source_health(self): return [{"source_id": "ons_bics_ai", "last_checked_at": NOW.isoformat(),
                                          "latest_available_period": "wave-159"}]
    result = ons_snapshot(Repo())
    assert result["geography_role"] == "UK_supplement"
    assert result["trend_status"]["extensive"] == "insufficient_history"
    assert result["quality"]["conditional_denominators_explicit"]
    assert result["questionnaire_references"] == ["https://ons.test/q"]


def test_discovery_candidate_identity_has_one_atomic_claim(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "claims.sqlite", artifact_root=tmp_path / "artifacts")
    assert repository.claim_discovery_candidate(source_id="us_census_btos", candidate_identity="BTOS:99:abc", owner_id="scheduled")
    assert not repository.claim_discovery_candidate(source_id="us_census_btos", candidate_identity="BTOS:99:abc", owner_id="manual")
    # A changed upstream content identity for the same business period is a
    # revision, not a duplicate; it remains eligible for a new vintage.
    assert repository.claim_discovery_candidate(source_id="us_census_btos", candidate_identity="BTOS:99:def", owner_id="revision")


def test_catalog_exposes_source_check_health_without_an_ingestion_run(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "health.sqlite", artifact_root=tmp_path / "artifacts")
    catalog = StructuredCatalog.load()
    repository.bootstrap_catalog(catalog)
    repository.save_source_check(source_id="rps_genai_adoption", dataset_id="ai_worker_adoption_us",
                                 status="no_change", latest_upstream_identity="FRED:abc",
                                 latest_ingested_identity="FRED:old", latest_available_period="2026-Q2",
                                 diagnostics={"reason": "unchanged"}, at=NOW)
    view = DataDiscovery(repository, catalog=catalog).catalog_view()
    row = next(item for item in view["sources"] if item["source_id"] == "rps_genai_adoption")
    assert row["last_check_status"] == "no_change"
    assert row["latest_available_period"] == "2026-Q2"


def test_discovery_transport_and_schema_failures_have_distinct_statuses():
    assert _failure_result(source_id="x", dataset_id="y", exc=TimeoutError("slow")).status.value == "unreachable"
    assert _failure_result(source_id="x", dataset_id="y", exc=ValueError("bad schema")).status.value == "validation_failed"


@pytest.mark.parametrize("status", [
    DiscoveryStatus.NOT_YET_PUBLISHED, DiscoveryStatus.NO_CHANGE,
    DiscoveryStatus.QUESTION_NOT_FIELDED, DiscoveryStatus.METHODOLOGY_BREAK,
    DiscoveryStatus.VALIDATION_FAILED, DiscoveryStatus.NEW_RELEASE,
])
def test_discovery_state_machine_statuses_are_persisted_without_ingestion(tmp_path, status):
    repository = SQLiteStructuredRepository(tmp_path / "states.sqlite", artifact_root=tmp_path / "artifacts")
    result = DiscoveryResult(source_id="ons_bics_ai", dataset_id="ai_enterprise_adoption_uk",
                             checked_at=NOW, status=status, latest_upstream_identity="wave:1",
                             diagnostics={"fixture": status.value})
    repository.save_source_check(source_id=result.source_id, dataset_id=result.dataset_id,
                                 status=result.status.value,
                                 latest_upstream_identity=result.latest_upstream_identity,
                                 diagnostics=result.diagnostics, at=result.checked_at)
    row = repository.source_checks(source_id="ons_bics_ai", limit=1)[0]
    assert row["status"] == status.value
    assert row["latest_upstream_identity"] == "wave:1"
    assert repository.observations(source_id="ons_bics_ai") == []
