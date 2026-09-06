from datetime import datetime, timezone
import json
from pathlib import Path

from ats.data.catalog import DataCatalog, StructuredCatalog
from ats.data.adapters.structured.registry import validate_source_registration
from ats.data.products import DataProducts
from ats.data.sources.anthropic_economic_index import (
    HF_API, AnthropicEconomicIndexAdapter, discover_releases, parse_monthly_csv,
    parse_research_snapshot_csv, parse_taxonomy_csvs,
)
from ats.data.structured import (
    AdapterArtifact, AdapterBatch, EntityRelationInput, FetchRequest, IngestionPipeline,
    IngestionStatus, NativeRecord, ReferenceEntityInput, SQLiteStructuredRepository,
)


FIXTURE = Path(__file__).parent / "fixtures" / "anthropic_economic_index"


class _Response:
    def __init__(self, content: bytes):
        self.content = content
        self.headers = {"content-length": str(len(content))}

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield self.content[:9]
        yield self.content[9:]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class _Client:
    def _response(self, url):
        if url == HF_API:
            return _Response((FIXTURE / "metadata.json").read_bytes())
        for filename in (
            "aei_claude_ai_2026_06_26.csv", "aei_1p_api_2026_06_26.csv",
            "onet_task_statements.csv", "soc_structure.csv", "job_exposure.csv",
            "task_penetration.csv",
        ):
            if url.endswith(filename):
                return _Response((FIXTURE / filename).read_bytes())
        if url.endswith("aei_claude_ai_2026-06-26.csv"):
            return _Response((FIXTURE / "aei_claude_ai_2026_06_26.csv").read_bytes())
        if url.endswith("aei_1p_api_2026-06-26.csv"):
            return _Response((FIXTURE / "aei_1p_api_2026_06_26.csv").read_bytes())
        raise AssertionError(url)

    def get(self, url, **_):
        return self._response(url)

    def stream(self, _method, url, **_):
        return self._response(url)


class _StaticAdapter:
    def __init__(self, batch):
        self.batch = batch

    def fetch(self, _request):
        return self.batch


class _FailOneProductClient(_Client):
    def stream(self, method, url, **kwargs):
        if url.endswith("aei_1p_api_2026-06-26.csv"):
            raise RuntimeError("503 upstream temporarily unavailable")
        return super().stream(method, url, **kwargs)


class _UnmappedTaskClient(_Client):
    def _response(self, url):
        if url.endswith("onet_task_statements.csv"):
            content = (FIXTURE / "onet_task_statements.csv").read_text().replace(",15-2031.00,Core", ",,Core")
            return _Response(content.encode())
        return super()._response(url)


class _RevisedClaudeClient(_Client):
    def _response(self, url):
        if url.endswith("aei_claude_ai_2026-06-26.csv"):
            return _Response((FIXTURE / "aei_claude_ai_2026_06_26.csv").read_bytes().replace(b",0.34,", b",0.36,"))
        return super()._response(url)


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(tmp_path / "aei.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


def test_release_discovery_and_global_filtering_are_commit_pinned(tmp_path):
    catalog = DataCatalog.load()
    assert catalog.validate().valid
    structured_catalog = StructuredCatalog.load()
    registration = validate_source_registration("anthropic_economic_index", catalog=structured_catalog)
    assert registration["valid"]
    assert next(item for item in structured_catalog.datasets() if item.id == "ai_work_adoption").primary_sources == ["anthropic_economic_index"]
    assert next(metric for provider, field, metric in structured_catalog.provider_mappings()
                if provider == "anthropic_economic_index" and field == "pct") == "ai.work_adoption.usage_share"
    metadata = json.loads((FIXTURE / "metadata.json").read_text())
    releases = discover_releases(metadata)
    assert releases == [{"release": "2026_06_26", "directory": "release_2026_06_26", "commit": "caa39af",
                         "claude_path": "release_2026_06_26/data/aei_claude_ai_2026-06-26.csv",
                         "api_path": "release_2026_06_26/data/aei_1p_api_2026-06-26.csv"}]
    records, payload, counts = parse_monthly_csv(
        FIXTURE / "aei_claude_ai_2026_06_26.csv", source_product="claude_ai",
        release="2026_06_26", commit="caa39af", published_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
        slice_key="claude_ai:2026_06_26")
    assert counts["non_global_rows"] == 1
    assert {record.period for record in records} == {"2026-04", "2026-05"}
    assert all(record.dimensions["source_product"] == "claude_ai" for record in records)
    assert all(record.entity_id != "SOC:X" for record in records)
    assert b'"geo_id":"GLOBAL"' in payload


def test_runtime_factory_accepts_transport_only_local_file_overrides(monkeypatch, tmp_path):
    from ats.data.adapters.structured.registry import runtime_spec

    claude = tmp_path / "claude.csv"
    api = tmp_path / "api.csv"
    monkeypatch.setenv("ATS_AEI_CLAUDE_AI_FILE", str(claude))
    monkeypatch.setenv("ATS_AEI_1P_API_FILE", str(api))
    adapter = runtime_spec("anthropic_economic_index").factory()

    assert adapter.local_file_overrides == {
        "aei_claude_ai_2026-06-26.csv": claude,
        "aei_1p_api_2026-06-26.csv": api,
    }


def test_release_discovery_skips_incomplete_release_and_repeated_content_is_no_change(tmp_path):
    metadata = json.loads((FIXTURE / "metadata.json").read_text())
    incomplete = {**metadata, "siblings": [item for item in metadata["siblings"]
                                                   if "aei_1p_api" not in item["rfilename"]]}
    assert discover_releases(incomplete) == []

    repo = _repo(tmp_path)
    adapter = AnthropicEconomicIndexAdapter(
        client=_Client(), clock=lambda: datetime(2026, 6, 27, tzinfo=timezone.utc))
    request = FetchRequest(source_id="anthropic_economic_index", dataset_id="ai_work_adoption",
                           periods=["2026-05"])
    assert IngestionPipeline(repo).run(adapter, request)["status"] == "succeeded"
    repeated = IngestionPipeline(repo).run(adapter, request)
    assert repeated["status"] == "no_change"
    assert repeated["unchanged"] > 0


def test_parser_schema_drift_and_unknown_metric_are_explicit(tmp_path):
    valid = (FIXTURE / "aei_claude_ai_2026_06_26.csv").read_text()
    missing_column = tmp_path / "missing-column.csv"
    missing_column.write_text(valid.replace(",node_external_id", "", 1))
    try:
        parse_monthly_csv(missing_column, source_product="claude_ai", release="2026_06_26",
                          commit="caa39af", published_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
                          slice_key="test")
    except ValueError as exc:
        assert "monthly_schema_missing" in str(exc)
    else:
        raise AssertionError("schema drift must be rejected")

    unknown_metric = tmp_path / "unknown-metric.csv"
    unknown_metric.write_text(valid.replace(",pct,0.34,", ",future_metric,0.34,", 1))
    records, _, counts = parse_monthly_csv(
        unknown_metric, source_product="claude_ai", release="2026_06_26", commit="caa39af",
        published_at=datetime(2026, 6, 26, tzinfo=timezone.utc), slice_key="test")
    assert counts["excluded_metrics"] == 1
    assert all(record.provider_field != "future_metric" for record in records)


def test_one_product_failure_is_partial_and_does_not_contaminate_other_product(tmp_path):
    repo = _repo(tmp_path)
    adapter = AnthropicEconomicIndexAdapter(
        client=_FailOneProductClient(), clock=lambda: datetime(2026, 6, 27, tzinfo=timezone.utc))
    result = IngestionPipeline(repo).run(adapter, FetchRequest(
        source_id="anthropic_economic_index", dataset_id="ai_work_adoption", periods=["2026-05"]))
    assert result["status"] == "partial"
    products = {json.loads(row["dimensions_json"])["source_product"] for row in repo.observations(
        dataset_id="ai_work_adoption", accepted_only=True)
        if row["period_basis"] == "calendar_month"}
    assert products == {"claude_ai"}


def test_taxonomy_stable_ids_task_type_and_exposure_granularity_survive_name_changes(tmp_path):
    task_path = tmp_path / "tasks.csv"
    soc_path = tmp_path / "soc.csv"
    task_path.write_text(
        "Task ID,Task,O*NET-SOC Code,Task Type\n"
        "7382,Renamed task display text,15-2031.00,Core\n"
        "9999,Unmapped task display text,,Supplemental\n")
    soc_path.write_text("SOC Code,Title\n15-0000,Renamed major group\n15-2031.00,Renamed occupation\n")
    entities, relations, _, _ = parse_taxonomy_csvs(
        task_path, soc_path, source_version="taxonomy-v2", slice_key_tasks="tasks", slice_key_soc="soc")
    task = next(entity for entity in entities if entity.entity_id == "ONET_TASK:7382")
    unmapped = next(entity for entity in entities if entity.entity_id == "ONET_TASK:9999")
    assert task.canonical_name == "Renamed task display text"
    assert task.metadata["task_type"] == "Core"
    assert unmapped.metadata["occupation_mapping_status"] == "unmapped"
    assert ("SOC:15-2031.00", "ONET_TASK:7382") == next(
        (relation.parent_entity_id, relation.child_entity_id) for relation in relations
        if relation.child_entity_id == "ONET_TASK:7382")

    exposure_path = tmp_path / "exposure.csv"
    exposure_path.write_text("SOC Code,observed_exposure\n15-2031,0.42\n")
    exposure, _ = parse_research_snapshot_csv(
        exposure_path, metric_field="observed_exposure", entity_prefix="SOC", release_date="2026_06_26",
        published_at=datetime(2026, 6, 26, tzinfo=timezone.utc), slice_key="exposure")
    assert exposure[0].entity_id == "SOC:15-2031"  # do not coerce 6-digit SOC to O*NET-SOC .XX


def test_official_wide_soc_schema_and_text_only_penetration_remain_governed(tmp_path):
    soc_path = tmp_path / "wide-soc.csv"
    soc_path.write_text(
        "Major Group,Minor Group,Broad Occupation,Detailed Occupation,Detailed O*NET-SOC,"
        "SOC or O*NET-SOC 2019 Title,soc_major_group\n"
        "11-0000,,,,,Management Occupations,11\n"
        ",,,11-1011,11-1011.03,Chief Sustainability Officers,11\n")
    task_path = tmp_path / "tasks.csv"
    task_path.write_text(
        "O*NET-SOC Code,Title,Task ID,Task,Task Type\n"
        "11-1011.00,Chief Executives,8823,Direct financial activities,Core\n")
    entities, relations, _, _ = parse_taxonomy_csvs(
        task_path, soc_path, source_version="official-schema", slice_key_tasks="tasks",
        slice_key_soc="soc")
    entity_ids = {entity.entity_id for entity in entities}
    assert {"SOC:11-0000", "SOC:11-1011", "SOC:11-1011.03", "SOC:11-1011.00"} <= entity_ids
    assert any(relation.parent_entity_id == "SOC:11-1011.00"
               and relation.child_entity_id == "ONET_TASK:8823" for relation in relations)

    penetration_path = tmp_path / "penetration.csv"
    penetration_path.write_text("task,penetration\nDirect financial activities,0.25\n")
    penetration, _ = parse_research_snapshot_csv(
        penetration_path, metric_field="penetration", entity_prefix="RESEARCH_TASK",
        release_date="2026_06_26", published_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
        slice_key="penetration")
    assert penetration[0].entity_id.startswith("RESEARCH_TASK:")
    assert penetration[0].dimensions["stable_onet_mapping"] == "unavailable"
    assert penetration[0].raw["source_native_identifier"] == "Direct financial activities"


def test_unmapped_task_is_retained_and_reported_as_warning(tmp_path):
    repo = _repo(tmp_path)
    result = IngestionPipeline(repo).run(AnthropicEconomicIndexAdapter(
        client=_UnmappedTaskClient(), clock=lambda: datetime(2026, 6, 27, tzinfo=timezone.utc)), FetchRequest(
            source_id="anthropic_economic_index", dataset_id="ai_work_adoption", periods=["2026-05"]))
    assert any(warning.startswith("unmapped_task_ids:claude_ai:7382") for warning in result["warnings"])
    assert any(warning.startswith("task_relation_coverage_below_threshold:claude_ai")
               for warning in result["warnings"])
    assert repo.observations(entity_id="ONET_TASK:7382", accepted_only=True)
    assert not repo.entity_relations(dataset_id="ai_work_adoption", source_id="anthropic_economic_index",
                                     child_entity_id="ONET_TASK:7382", relation_type="has_task")


def test_revision_appends_vintage_and_health_exposes_slice_state(tmp_path):
    repo = _repo(tmp_path)
    request = FetchRequest(source_id="anthropic_economic_index", dataset_id="ai_work_adoption", periods=["2026-05"])
    first_known = datetime(2026, 6, 27, tzinfo=timezone.utc)
    IngestionPipeline(repo).run(AnthropicEconomicIndexAdapter(client=_Client(), clock=lambda: first_known), request)
    revised_known = datetime(2026, 7, 1, tzinfo=timezone.utc)
    revision = IngestionPipeline(repo).run(AnthropicEconomicIndexAdapter(
        client=_RevisedClaudeClient(), clock=lambda: revised_known), request)
    assert revision["status"] == "succeeded"
    old = repo.observations(dataset_id="ai_work_adoption", source_id="anthropic_economic_index",
                            entity_id="SOC:15-2031.00", metric_id="ai.work_adoption.usage_share",
                            as_of=first_known, latest_only=True, accepted_only=True)
    current = repo.observations(dataset_id="ai_work_adoption", source_id="anthropic_economic_index",
                                entity_id="SOC:15-2031.00", metric_id="ai.work_adoption.usage_share",
                                as_of=revised_known, latest_only=True, accepted_only=True)
    assert {row["value"] for row in old} == {0.34, 0.44}
    assert {row["value"] for row in current} == {0.36, 0.44}
    health = next(row for row in repo.source_health() if row["source_id"] == "anthropic_economic_index")
    assert health["last_checked_at"] and health["latest_upstream_commit"] == "caa39af"
    assert health["latest_ingested_release"] == "2026_06_26"
    assert health["source_products"]["claude_ai"]["latest_available_period"] == "2026-05"


def test_product_isolated_series_cross_section_and_recomputable_derivations(tmp_path):
    repo = _repo(tmp_path)
    IngestionPipeline(repo).run(AnthropicEconomicIndexAdapter(
        client=_Client(), clock=lambda: datetime(2026, 6, 27, tzinfo=timezone.utc)), FetchRequest(
            source_id="anthropic_economic_index", dataset_id="ai_work_adoption", periods=["2026-04", "2026-05"]))
    products = DataProducts(structured_repository=repo)
    series = products.ai_work_adoption_series(entity_id="SOC:15-2031.00",
                                               metric_id="ai.work_adoption.usage_share",
                                               source_product="claude_ai")
    frame = products.ai_work_adoption_series(entity_id="SOC:15-2031.00",
                                              metric_id="ai.work_adoption.usage_share",
                                              source_product="claude_ai", as_frame=True)
    section = products.ai_work_adoption_cross_section(metric_id="ai.work_adoption.usage_share",
                                                       source_product="claude_ai", period="2026-05")
    snapshot = products.ai_work_adoption_snapshot(source_product="claude_ai", period="2026-05")
    sql = repo.open_read_only()
    sql_value = sql.execute("SELECT value FROM structured_observations o JOIN structured_series s "
                            "ON s.series_id=o.series_id WHERE s.entity_id='SOC:15-2031.00' "
                            "AND s.metric_id='ai.work_adoption.usage_share' AND o.period='2026-05'").fetchone()[0]
    sql.close()
    assert [row["value"] for row in series["rows"]] == list(frame["value"])
    assert section["rows"][0]["entity_id"] == "SOC:15-2031.00"
    assert sql_value == snapshot["jobs"][0]["metrics"]["ai.work_adoption.usage_share"] == 0.34
    assert snapshot["jobs"][0]["usage_share_yoy_pp"]["status"] == "insufficient_history"
    assert section["rows"][0]["value"] == 0.34  # level 0 is not constructed from level-1 aggregates
    try:
        products.ai_work_adoption_series(entity_id="SOC:15-2031.00", metric_id="ai.work_adoption.usage_share",
                                          source_product="all")
    except ValueError:
        pass
    else:
        raise AssertionError("cross-product default comparison must be rejected")


def test_2026_06_26_isolated_backfill_acceptance(tmp_path):
    repo = _repo(tmp_path)
    request = FetchRequest(source_id="anthropic_economic_index", dataset_id="ai_work_adoption",
                           periods=["2026-04", "2026-05"])
    result = IngestionPipeline(repo).run(AnthropicEconomicIndexAdapter(
        client=_Client(), clock=lambda: datetime(2026, 6, 27, tzinfo=timezone.utc)), request)
    assert result["status"] == "succeeded"
    rows = repo.observations(dataset_id="ai_work_adoption", accepted_only=True)
    monthly = [row for row in rows if row["period_basis"] == "calendar_month"]
    assert {row["period"] for row in monthly} == {"2026-04", "2026-05"}
    assert {json.loads(row["dimensions_json"])["source_product"] for row in monthly} == {
        "claude_ai", "1p_api"}
    assert all(json.loads(row["raw_payload"])["provider_row"]["geo_id"] == "GLOBAL" for row in monthly)
    assert {row["period_basis"] for row in rows if row["metric_id"] in {
        "ai.work_adoption.observed_exposure", "ai.work_adoption.task_penetration"}} == {"research_snapshot"}
    artifacts = repo.artifacts_for(source_id="anthropic_economic_index", dataset_id="ai_work_adoption")
    product_artifacts = {json.loads(row["metadata_json"]).get("source_product"): row["artifact_id"]
                         for row in artifacts if json.loads(row["metadata_json"]).get("source_product")}
    assert set(product_artifacts) == {"claude_ai", "1p_api"}
    products = DataProducts(structured_repository=repo)
    latest = products.ai_work_adoption_snapshot(source_product="claude_ai")
    assert latest["period"] == "2026-05"
    assert products.ai_work_adoption_snapshot(source_product="claude_ai", period="2026-04")["status"] == "ok"


def test_adapter_and_pipeline_keep_product_artifact_lineage_and_taxonomy(tmp_path):
    repo = _repo(tmp_path)
    adapter = AnthropicEconomicIndexAdapter(
        client=_Client(), clock=lambda: datetime(2026, 6, 27, tzinfo=timezone.utc))
    result = IngestionPipeline(repo).run(adapter, FetchRequest(
        source_id="anthropic_economic_index", dataset_id="ai_work_adoption", periods=["2026-05"]))

    assert result["status"] == "succeeded"
    rows = repo.observations(dataset_id="ai_work_adoption", accepted_only=True)
    by_product = {json.loads(row["dimensions_json"])["source_product"]: row["artifact_id"]
                  for row in rows if row["metric_id"] == "ai.work_adoption.usage_share"
                  and row["entity_id"] == "SOC:15-2031.00"}
    assert len(by_product) == 2 and by_product["claude_ai"] != by_product["1p_api"]
    relations = repo.entity_relations(dataset_id="ai_work_adoption", source_id="anthropic_economic_index")
    assert {(row["parent_entity_id"], row["child_entity_id"], row["relation_type"]) for row in relations} == {
        ("SOC:15-0000", "SOC:15-2031.00", "contains_occupation"),
        ("SOC:15-2031.00", "ONET_TASK:7382", "has_task"),
    }
    assert repo.observations(metric_id="ai.work_adoption.observed_exposure")[0]["period_basis"] == "research_snapshot"


def test_snapshot_and_job_profile_do_not_mix_products_or_relabel_usage(tmp_path):
    repo = _repo(tmp_path)
    adapter = AnthropicEconomicIndexAdapter(
        client=_Client(), clock=lambda: datetime(2026, 6, 27, tzinfo=timezone.utc))
    IngestionPipeline(repo).run(adapter, FetchRequest(
        source_id="anthropic_economic_index", dataset_id="ai_work_adoption", periods=["2026-04", "2026-05"]))
    products = DataProducts(structured_repository=repo)

    snapshot = products.ai_work_adoption_snapshot(source_product="claude_ai", period="2026-05")
    profile = products.ai_job_profile("15-2031.00", source_product="1p_api", period="2026-05")
    assert snapshot["jobs"][0]["metrics"]["ai.work_adoption.usage_share"] == 0.34
    assert profile["metrics"]["ai.work_adoption.usage_share"] == 0.44
    assert profile["task_structure"]["observed_task_coverage"] == 1.0
    assert snapshot["jobs"][0]["usage_share_change_pp"]["value"] == 0.04
    assert snapshot["jobs"][0]["automation_share_change_pp"]["value"] == 2.0
    assert snapshot["jobs"][0]["job_share_of_major_group"]["value"] == 0.085
    assert "not worker adoption" in snapshot["semantic_boundary"]["usage_share"]
    assert profile["observed_exposure"]["period_basis"] == "research_snapshot"


def test_multi_artifact_requires_exact_slice_key_and_taxonomy_is_as_of(tmp_path):
    repo = _repo(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    batch = AdapterBatch(
        source_id="anthropic_economic_index", dataset_id="ai_work_adoption",
        status=IngestionStatus.SUCCEEDED, fetched_at=now,
        artifacts=[
            AdapterArtifact(artifact_key="left", payload=b"left", source_url="https://example.test/left"),
            AdapterArtifact(artifact_key="right", payload=b"right", source_url="https://example.test/right"),
        ],
        entities=[
            ReferenceEntityInput(entity_id="SOC:15-0000", kind="soc_major_group", canonical_name="Group"),
            ReferenceEntityInput(entity_id="SOC:15-2031.00", kind="soc_occupation", canonical_name="Job"),
        ],
        relations=[EntityRelationInput(
            parent_entity_id="SOC:15-0000", child_entity_id="SOC:15-2031.00",
            relation_type="contains_occupation", source_version="v1", slice_key="left")],
        records=[NativeRecord(
            entity_id="SOC:15-2031.00", provider_field="pct", period="2026-05", value=1,
            unit="percent", period_basis="calendar_month", slice_key="missing",
            dimensions={"source_product": "claude_ai", "classification": "soc_occupation", "hierarchy_level": 0})],
    )
    result = IngestionPipeline(repo).run(_StaticAdapter(batch), FetchRequest(
        source_id="anthropic_economic_index", dataset_id="ai_work_adoption"))
    assert result["quarantined"] == 1
    assert repo.observations(dataset_id="ai_work_adoption") == []
    relation = repo.entity_relations(dataset_id="ai_work_adoption", source_id="anthropic_economic_index")[0]
    repo.save_entity_relation(dataset_id="ai_work_adoption", source_id="anthropic_economic_index",
                              parent_entity_id="SOC:15-0000", child_entity_id="SOC:15-2031.00",
                              relation_type="contains_occupation", source_version="v2",
                              known_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                              artifact_id=relation["artifact_id"], active=False)
    assert repo.entity_relations(dataset_id="ai_work_adoption", source_id="anthropic_economic_index",
                                 as_of=now) == [relation]
    assert repo.entity_relations(dataset_id="ai_work_adoption", source_id="anthropic_economic_index",
                                 as_of=datetime(2026, 7, 2, tzinfo=timezone.utc)) == []


def test_monthly_validation_rejects_bad_collaboration_sum_and_snapshot_replays_relations(tmp_path):
    repo = _repo(tmp_path)
    adapter = AnthropicEconomicIndexAdapter(
        client=_Client(), clock=lambda: datetime(2026, 6, 27, tzinfo=timezone.utc))
    IngestionPipeline(repo).run(adapter, FetchRequest(
        source_id="anthropic_economic_index", dataset_id="ai_work_adoption", periods=["2026-05"]))
    products = DataProducts(structured_repository=repo)
    snapshot = products.ai_work_adoption_snapshot(
        source_product="claude_ai", period="2026-05", snapshot_consumer="evidence_observer")
    replay = products.replay_snapshot(snapshot["manifest"]["snapshot_id"])
    assert replay["rows"] and replay["relations"] and replay["artifacts"]
    content = (FIXTURE / "aei_claude_ai_2026_06_26.csv").read_text()
    corrupted = tmp_path / "corrupted.csv"
    corrupted.write_text(content.replace("52.00", "52.01", 1).replace("48.00", "40.00", 1))
    records, _, _ = parse_monthly_csv(
        corrupted, source_product="claude_ai", release="2026_06_26", commit="caa39af",
        published_at=datetime(2026, 6, 26, tzinfo=timezone.utc), slice_key="test")
    from ats.data.sources.anthropic_economic_index import validate_monthly_slice

    assert any(reason.startswith("automation_augmentation_sum_invalid")
               for reason in validate_monthly_slice(records, mapped_task_ids={"7382"}))


def test_l1_observer_consumes_governed_products_and_persists_manifest(tmp_path):
    from ats.agents.evidence import observe_work_adoption

    repo = _repo(tmp_path)
    IngestionPipeline(repo).run(AnthropicEconomicIndexAdapter(
        client=_Client(), clock=lambda: datetime(2026, 6, 27, tzinfo=timezone.utc)), FetchRequest(
            source_id="anthropic_economic_index", dataset_id="ai_work_adoption",
            periods=["2026-04", "2026-05"]))
    packet = observe_work_adoption(
        source_product="claude_ai", period="2026-05", occupation="15-2031.00",
        products=DataProducts(structured_repository=repo), top_n=1)

    assert packet["status"] == "ok"
    assert packet["source_access"] == "data_products_only"
    assert packet["job_profile"]["occupation"]["entity_id"] == "SOC:15-2031.00"
    assert packet["manifest"]["snapshot_id"]
    assert DataProducts(structured_repository=repo).replay_snapshot(
        packet["manifest"]["snapshot_id"])["rows"]
    assert "不是该职业从业者采用率" in packet["facts"][0]["statement"]
