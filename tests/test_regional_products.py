from datetime import datetime, timezone

from ats.data import regional
from ats.data.products import DataProducts, RegionalPoint, RegionalProducts, RegionalSnapshot
from ats.structured import ArtifactDescriptor, ObservationInput, SeriesIdentity, SQLiteStructuredRepository


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


class _Store:
    def projection_lineage(self, _identifier):
        return None


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(tmp_path / "data.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog()
    return repo


def _save(repo, *, source, dataset, entity, metric, period, value, unit):
    artifact = repo.put_artifact(
        {"period": period, "value": value},
        ArtifactDescriptor(source_id=source, dataset_id=dataset, fetched_at=NOW,
                           query_scope={"period": period}))
    return repo.save_observation(ObservationInput(
        series=SeriesIdentity(source_id=source, dataset_id=dataset, entity_id=entity,
                              metric_id=metric, unit=unit, period_basis="month"),
        period=period, value=value, published_at=NOW, known_at=NOW, fetched_at=NOW,
        artifact_id=artifact.id)).id


def test_regional_product_selects_latest_and_shared_derivations(tmp_path):
    repo = _repo(tmp_path)
    months = [f"2025-{month:02d}" for month in range(1, 13)] + ["2026-01", "2026-02"]
    for index, period in enumerate(months):
        _save(repo, source="tw_mof_exports", dataset="regional_tw_exports", entity="TW_IC_EXPORT",
              metric="regional.tw_ic_exports.value", period=period, value=100 + index, unit="USD M")
        _save(repo, source="kr_ecos_exports", dataset="regional_kr_exports", entity="KR_SEMI_EXPORT",
              metric="regional.kr_semiconductor_exports.index", period=period, value=200 + index,
              unit="2020=100")

    snapshot = RegionalProducts(DataProducts(store=_Store(), structured_repository=repo)).snapshot()
    by_id = {point.id: point for point in snapshot.points}

    assert by_id["tw_ic_exports"].period == "2026-02"
    assert by_id["tw_ic_exports"].value == 113
    assert by_id["tw_ic_exports"].mom == (113 / 112 - 1)
    assert by_id["tw_ic_exports"].yoy == (113 / 101 - 1)
    assert by_id["tw_ic_exports"].observation_id
    assert by_id["kr_semi_exports"].unit == "2020=100"


def _snapshot(value=100.0, *, source_ids=None) -> RegionalSnapshot:
    points = (
        RegionalPoint(id="tw_ic_exports", label="台湾 IC 出口", period="2026-07", value=value,
                      unit="USD M", yoy=0.1, mom=0.02, known_at="2026-08-20T00:00:00+00:00",
                      source_id="tw_mof_exports", dataset_id="regional_tw_exports",
                      observation_id="obs-tw"),
        RegionalPoint(id="kr_semi_exports", label="韩国半导体出口指数", period="2026-07",
                      value=value + 100, unit="2020=100", yoy=0.1, mom=0.02,
                      known_at="2026-08-20T00:00:00+00:00", source_id="kr_ecos_exports",
                      dataset_id="regional_kr_exports", observation_id="obs-kr"),
    )
    if source_ids is not None:
        points = tuple(point for point in points if point.source_id in source_ids)
    return RegionalSnapshot(points=points, as_of=NOW)


def test_regional_shadow_returns_legacy_and_records_equivalent_snapshot(monkeypatch):
    legacy = _snapshot()
    records = []
    monkeypatch.setattr(regional, "_legacy_snapshot", lambda **kwargs: _snapshot(**kwargs))
    monkeypatch.setattr(regional, "_platform_snapshot", lambda **kwargs: _snapshot(**kwargs))
    monkeypatch.setattr(regional, "_record", lambda **kwargs: records.append(kwargs))
    monkeypatch.setenv("ATS_STRUCTURED_SECTOR_AGENT_TW_MOF_EXPORTS_MODE", "shadow")

    result = regional.fetch(consumer="sector_agent")
    assert result.points == legacy.points
    assert {record["source_id"] for record in records} == {"tw_mof_exports", "kr_ecos_exports"}
    assert all(record["matched"] is True for record in records)


def test_regional_shadow_falls_back_to_legacy_when_platform_fails(monkeypatch):
    legacy = _snapshot()
    monkeypatch.setattr(regional, "_legacy_snapshot", lambda **kwargs: _snapshot(**kwargs))
    monkeypatch.setattr(regional, "_platform_snapshot",
                        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    monkeypatch.setenv("ATS_STRUCTURED_MACRO_AGENT_TW_MOF_EXPORTS_MODE", "shadow")

    assert regional.fetch(consumer="macro_agent").points == legacy.points


def test_regional_source_specific_mode_does_not_switch_the_other_source(monkeypatch):
    monkeypatch.setattr(regional, "_legacy_snapshot", lambda **kwargs: _snapshot(100, **kwargs))
    monkeypatch.setattr(regional, "_platform_snapshot", lambda **kwargs: _snapshot(200, **kwargs))
    monkeypatch.setenv("ATS_STRUCTURED_SECTOR_AGENT_TW_MOF_EXPORTS_MODE", "platform")
    monkeypatch.setenv("ATS_STRUCTURED_SECTOR_AGENT_KR_ECOS_EXPORTS_MODE", "legacy")

    result = regional.fetch(consumer="sector_agent")
    values = {point.source_id: point.value for point in result.points}

    assert values == {"tw_mof_exports": 200, "kr_ecos_exports": 200}


def test_sector_and_macro_contexts_render_regional_block():
    from ats.agents.macro.assemble import MacroContext
    from ats.agents.sector.assemble import SectorContext
    from ats.config import load_macro_config
    from ats.schemas.sector import SectorConfig

    regional_block = _snapshot().render()
    sector_cfg = SectorConfig(name="test", label="test", output_dir="", layers=[])
    assert "台湾 IC 出口" in SectorContext(cfg=sector_cfg, regional_block=regional_block).as_context()
    assert "台湾 IC 出口" in MacroContext(
        cfg=load_macro_config("macro"), regional_block=regional_block).as_context()
