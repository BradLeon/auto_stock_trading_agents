"""Consumer-level acceptance for Chain's governed regional inputs."""

from __future__ import annotations

from datetime import datetime, timezone

from ats.chain import sources
from ats.schemas.chain import SourceDef
from ats.structured import ArtifactDescriptor, ObservationInput, SeriesIdentity, SQLiteStructuredRepository


NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


class _NoChangePipeline:
    """The consumer reads accepted data; source acquisition is tested separately."""

    def __init__(self, _repository):
        pass

    def run(self, _adapter, _request):
        return {"status": "no_change"}


class _UnavailablePipeline:
    """A refresh gap must not hide an already accepted monthly vintage."""

    def __init__(self, _repository):
        pass

    def run(self, _adapter, _request):
        return {"status": "unreachable"}


def _seed(repo, *, source, dataset, entity, metric, unit, start):
    for index in range(14):
        year = 2025 + (start + index - 1) // 12
        month = (start + index - 1) % 12 + 1
        period = f"{year}-{month:02d}"
        value = float(100 + index)
        artifact = repo.put_artifact(
            {"period": period, "value": value},
            ArtifactDescriptor(source_id=source, dataset_id=dataset, fetched_at=NOW,
                               query_scope={"fixture": "chain_regional"}),
        )
        repo.save_observation(ObservationInput(
            series=SeriesIdentity(source_id=source, dataset_id=dataset, entity_id=entity,
                                  metric_id=metric, unit=unit, period_basis="month"),
            period=period, value=value, published_at=NOW, known_at=NOW, fetched_at=NOW,
            artifact_id=artifact.id,
        ))


def test_chain_regional_reads_platform_levels_and_derivations(monkeypatch, tmp_path) -> None:
    database = tmp_path / "regional.sqlite"
    seed = SQLiteStructuredRepository(database, artifact_root=tmp_path / "artifacts")
    seed.bootstrap_catalog()
    _seed(seed, source="tw_mof_exports", dataset="regional_tw_exports", entity="TW_IC_EXPORT",
          metric="regional.tw_ic_exports.value", unit="USD M", start=6)
    _seed(seed, source="kr_ecos_exports", dataset="regional_kr_exports", entity="KR_SEMI_EXPORT",
          metric="regional.kr_semiconductor_exports.index", unit="2020=100", start=6)
    seed.close()

    def platform_repository():
        repository = SQLiteStructuredRepository(database, artifact_root=tmp_path / "artifacts")
        repository.bootstrap_catalog()
        return repository

    monkeypatch.setattr("ats.data.runtime.get_platform_structured_repository", platform_repository)
    monkeypatch.setattr("ats.structured.IngestionPipeline", _NoChangePipeline)
    monkeypatch.setenv("ATS_STRUCTURED_CHAIN_REGIONAL_MODE", "platform")

    definitions = (
        SourceDef(id="tw_ic_exports", label="台湾 IC 出口", adapter="tw_mof",
                  entity="TW_IC_EXPORT", stance="regulator", observation_type="regulatory",
                  cadence="monthly", concepts=["supply_tightness"], direction_from=["yoy", "mom"]),
        SourceDef(id="kr_semi_exports", label="韩国半导体出口", adapter="kr_ecos",
                  entity="KR_SEMI_EXPORT", stance="regulator", observation_type="regulatory",
                  cadence="monthly", concepts=["supply_tightness"], direction_from=["yoy", "mom"]),
    )
    for definition in definitions:
        points = sources.fetch(definition, lookback_months=2)
        assert [point.period for point in points] == ["2026-06", "2026-07"]
        assert all(point.yoy is not None and point.mom is not None for point in points)
        assert points[-1].yoy == (113 / 101 - 1)
        assert points[-1].mom == (113 / 112 - 1)


def test_chain_regional_uses_accepted_platform_vintage_when_refresh_is_unreachable(
        monkeypatch, tmp_path) -> None:
    database = tmp_path / "regional.sqlite"
    seed = SQLiteStructuredRepository(database, artifact_root=tmp_path / "artifacts")
    seed.bootstrap_catalog()
    _seed(seed, source="tw_mof_exports", dataset="regional_tw_exports", entity="TW_IC_EXPORT",
          metric="regional.tw_ic_exports.value", unit="USD M", start=6)
    seed.close()

    def platform_repository():
        repository = SQLiteStructuredRepository(database, artifact_root=tmp_path / "artifacts")
        repository.bootstrap_catalog()
        return repository

    monkeypatch.setattr("ats.data.runtime.get_platform_structured_repository", platform_repository)
    monkeypatch.setattr("ats.structured.IngestionPipeline", _UnavailablePipeline)
    monkeypatch.setenv("ATS_STRUCTURED_CHAIN_REGIONAL_MODE", "platform")
    definition = SourceDef(
        id="tw_ic_exports", label="台湾 IC 出口", adapter="tw_mof", entity="TW_IC_EXPORT",
        stance="regulator", observation_type="regulatory", cadence="monthly",
        concepts=["supply_tightness"], direction_from=["yoy", "mom"])

    points = sources.fetch(definition, lookback_months=2)

    assert [point.period for point in points] == ["2026-06", "2026-07"]
