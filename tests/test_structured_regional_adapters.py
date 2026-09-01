"""Official TW/KR levels, shared derivations and failure-safe ingestion fixtures."""

from datetime import datetime, timezone
import json
import re

import pytest

from ats.data.sources import kr_ecos, tw_mof
from ats.chain import sources as chain_sources
from ats.schemas.chain import SeriesPoint, SourceDef
from ats.data.structured import (
    FetchRequest,
    IngestionPipeline,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
MONTHS = [f"2025-{month:02d}" for month in range(1, 13)] + ["2026-01", "2026-02"]


class _Response:
    def __init__(self, *, body=None, content=b""):
        self.body = body
        self.content = content

    def json(self):
        return self.body


class _TaiwanClient:
    def __init__(self, csv_body: bytes, *, error=None):
        self.csv_body = csv_body
        self.error = error

    def get(self, url, **kwargs):
        if self.error:
            raise self.error
        if url == tw_mof.DATASET:
            return _Response(body={"result": {"distribution": [{
                "resourceDownloadUrl": "https://example.test/tw.csv",
                "resourceModified": "2026-08-20T00:00:00+08:00",
            }]}})
        return _Response(content=self.csv_body)


class _KoreaClient:
    def __init__(self, rows, *, error=None):
        self.rows = rows
        self.error = error

    def get(self, url, **kwargs):
        if self.error:
            raise self.error
        match = re.search(r"/kr/(\d+)/(\d+)/403Y001/", url)
        assert match
        start, end = int(match.group(1)), int(match.group(2))
        bounds = re.search(r"/M/(\d{6})/(\d{6})/", url)
        assert bounds
        eligible = [row for row in self.rows
                    if bounds.group(1) <= row["TIME"] <= bounds.group(2)]
        page = eligible[start - 1:end]
        return _Response(body={"StatisticSearch": {
            "list_total_count": len(eligible), "row": page}})


def _tw_csv(values=None):
    values = values or list(range(100, 100 + len(MONTHS)))
    lines = ["期間,電子零組件(百萬美元),總計"]
    for period, value in zip(MONTHS, values):
        year, month = period.split("-")
        lines.append(f"{int(year) - 1911}年{int(month)}月,{value},999")
    lines.append("115年 (1~2月),999,999")
    return ("\ufeff" + "\n".join(lines)).encode("utf-8")


def _kr_rows(values=None):
    values = values or list(range(200, 200 + len(MONTHS)))
    return [{
        "TIME": period.replace("-", ""), "DATA_VALUE": str(value),
        "UNIT_NAME": "2020=100", "STAT_CODE": "403Y001", "ITEM_CODE1": "3091AA",
    } for period, value in zip(MONTHS, values)]


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


def _tw_request():
    return FetchRequest(
        source_id="tw_mof_exports", dataset_id="regional_tw_exports",
        entities=["TW_IC_EXPORT"], query_scope={"lookback_months": 14})


def _kr_request():
    return FetchRequest(
        source_id="kr_ecos_exports", dataset_id="regional_kr_exports",
        entities=["KR_SEMI_EXPORT"], periods=MONTHS, query_scope={
            "lookback_months": 14, "stat": "403Y001", "item": "3091AA"})


def test_taiwan_adapter_returns_official_levels_coverage_and_raw_csv(tmp_path):
    repo = _repo(tmp_path)
    adapter = tw_mof.TaiwanMOFAdapter(
        client=_TaiwanClient(_tw_csv()), clock=lambda: NOW)
    batch = adapter.fetch(_tw_request())
    result = IngestionPipeline(repo).run(adapter, _tw_request())

    assert batch.provider_metadata["coverage"] == {
        "first_period": "2025-01", "last_period": "2026-02", "period_count": 14,
        "publication_time_status": "not_supplied_by_dataset_endpoint"}
    assert batch.records[0].published_at is None
    assert batch.records[-1].value == 113 and batch.records[-1].currency == "USD"
    assert result["status"] == "succeeded" and result["accepted"] == 14
    artifact = repo.lineage(repo.observations()[0]["observation_id"])["artifact"]
    assert artifact["media_type"] == "text/csv"
    assert repo.artifacts.read(artifact["relative_path"]).startswith(b"\xef\xbb\xbf")


def test_korea_adapter_pages_query_slice_and_keeps_native_index_unit(tmp_path):
    repo = _repo(tmp_path)
    adapter = kr_ecos.KoreaECOSAdapter(
        client=_KoreaClient(_kr_rows()), api_key="fixture-key", clock=lambda: NOW)
    batch = adapter.fetch(_kr_request())
    result = IngestionPipeline(repo).run(adapter, _kr_request())

    assert len(batch.records) == 14
    assert batch.records[0].unit == "2020=100" and batch.records[0].currency == ""
    assert batch.provider_metadata["coverage"]["reported_total_count"] == 14
    assert batch.artifacts[0].source_url.startswith(f"{kr_ecos.BASE}/<redacted>/")
    assert "fixture-key" not in batch.artifacts[0].source_url
    assert result["status"] == "succeeded" and result["accepted"] == 14
    artifact = repo.lineage(repo.observations()[0]["observation_id"])["artifact"]
    raw = json.loads(repo.artifacts.read(artifact["relative_path"]))
    assert len(raw["pages"]) == 2


def test_korea_sample_key_chunks_time_range_to_respect_ten_row_cap():
    adapter = kr_ecos.KoreaECOSAdapter(
        client=_KoreaClient(_kr_rows()), api_key="sample", clock=lambda: NOW)

    batch = adapter.fetch(_kr_request())

    assert [record.period for record in batch.records] == MONTHS
    assert len(batch.artifacts[0].payload["pages"]) == 2


@pytest.mark.parametrize("kind", ["tw", "kr"])
def test_backfill_is_continuous_idempotent_and_revision_appends(tmp_path, kind):
    repo = _repo(tmp_path)
    if kind == "tw":
        request = _tw_request()
        first_adapter = tw_mof.TaiwanMOFAdapter(
            client=_TaiwanClient(_tw_csv()), clock=lambda: NOW)
        revised_values = list(range(100, 114))
        revised_values[-1] = 999
        revised_adapter = tw_mof.TaiwanMOFAdapter(
            client=_TaiwanClient(_tw_csv(revised_values)), clock=lambda: NOW)
    else:
        request = _kr_request()
        first_adapter = kr_ecos.KoreaECOSAdapter(
            client=_KoreaClient(_kr_rows()), api_key="fixture", clock=lambda: NOW)
        revised_values = list(range(200, 214))
        revised_values[-1] = 999
        revised_adapter = kr_ecos.KoreaECOSAdapter(
            client=_KoreaClient(_kr_rows(revised_values)), api_key="fixture", clock=lambda: NOW)
    pipeline = IngestionPipeline(repo)

    first = pipeline.run(first_adapter, request)
    repeated = pipeline.run(first_adapter, request)
    revised = pipeline.run(revised_adapter, request)
    rows = repo.observations(latest_only=False)

    assert first["accepted"] == 14
    assert repeated["status"] == "no_change" and repeated["unchanged"] == 14
    assert revised["accepted"] == 1 and revised["unchanged"] == 13
    assert sorted({row["period"] for row in rows}) == MONTHS
    assert [row["value"] for row in rows if row["period"] == "2026-02"][-1] == 999


def test_legacy_output_uses_shared_yoy_and_mom_derivations():
    tw_records = tw_mof.parse_csv(_tw_csv())
    kr_records = kr_ecos.parse_rows(_kr_rows())

    tw_points = tw_mof._legacy_points(tw_records, 2)
    kr_points = kr_ecos._legacy_points(kr_records, 2)

    assert tw_points[0].period == "2026-01"
    assert tw_points[0].yoy == pytest.approx(112 / 100 - 1)
    assert tw_points[0].mom == pytest.approx(112 / 111 - 1)
    assert kr_points[-1].yoy == pytest.approx(213 / 201 - 1)
    assert kr_points[-1].mom == pytest.approx(213 / 212 - 1)


@pytest.mark.parametrize("adapter,request_factory", [
    (lambda: tw_mof.TaiwanMOFAdapter(
        client=_TaiwanClient(b"", error=TimeoutError("offline")), clock=lambda: NOW),
     _tw_request),
    (lambda: kr_ecos.KoreaECOSAdapter(
        client=_KoreaClient([], error=TimeoutError("offline")),
        api_key="fixture", clock=lambda: NOW), _kr_request),
])
def test_network_failure_is_unreachable_and_never_creates_zero(
        adapter, request_factory, tmp_path):
    repo = _repo(tmp_path)
    result = IngestionPipeline(repo).run(adapter(), request_factory())

    assert result["status"] == "unreachable"
    assert repo.observations() == []
    assert repo.conn.execute("SELECT count(*) FROM structured_artifacts").fetchone()[0] == 0


def test_schema_change_is_parse_failure_not_partial_zero(tmp_path):
    repo = _repo(tmp_path)
    bad_csv = "期間,完全不同欄位\n115年1月,123".encode()
    adapter = tw_mof.TaiwanMOFAdapter(
        client=_TaiwanClient(bad_csv), clock=lambda: NOW)

    result = IngestionPipeline(repo).run(adapter, _tw_request())

    assert result["status"] == "parse_failed"
    assert repo.observations() == []


def test_chain_regional_feature_flag_switch_and_rollback(monkeypatch):
    source = SourceDef(
        id="tw_ic_exports", label="TW", adapter="tw_mof", entity="TW_IC_EXPORT",
        stance="regulator", observation_type="regulatory", cadence="monthly",
        concepts=["packaging_throughput"], direction_from=["yoy", "mom"])
    legacy = [SeriesPoint(period="2026-01", value=100, unit="百万美元", yoy=0.1)]
    platform = [SeriesPoint(period="2026-01", value=101, unit="百万美元", yoy=0.2)]
    monkeypatch.setattr(tw_mof, "fetch", lambda **_: legacy)
    monkeypatch.setattr(chain_sources, "_platform_fetch", lambda *_, **__: platform)

    monkeypatch.setenv("ATS_STRUCTURED_CHAIN_REGIONAL_MODE", "platform")
    assert chain_sources.fetch(source) == platform

    # Rollback drill: changing only this consumer flag restores the unchanged contract.
    monkeypatch.setenv("ATS_STRUCTURED_CHAIN_REGIONAL_MODE", "legacy")
    assert chain_sources.fetch(source) == legacy
