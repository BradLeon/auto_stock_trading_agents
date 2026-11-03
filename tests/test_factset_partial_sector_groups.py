"""Sector comparison keeps the real readings of sector groups that published on their own.

The full eleven-sector view needs every sector group and the index core; until then the Information Technology
groups that passed their own review are still source-backed. The material must carry them, say which sectors are
absent, and never show an unpublished group."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from ats.data import factset
from ats.data.products import earnings_insight as ei

T = datetime(2026, 11, 3, tzinfo=timezone.utc)
V = "SP500:2026-10-30:research_article@x"


def _manifest(key, *, chart, period, observations, platform=True, report="2026-10-30", version=V, at=0):
    return {"partition_name": f"sector_group:{key}", "report_date": report, "version_id": version,
            "document_id": "doc", "known_at": f"2026-11-03T03:18:0{at}+00:00",
            "status": "platform" if platform else "shadow", "passed": platform,
            "quality": {"group": {"chart_id": chart, "period": period}}, "observation_ids": observations}


def _row(observation_id, metric, value, entity="GICS_45"):
    return {"entity_id": entity, "metric_id": metric, "period": "2026Q3", "period_basis": "target_quarter",
            "value": value, "unit": "percent", "known_at": T.isoformat(), "dimensions_json": "{}",
            "observation_id": observation_id}


def _products(manifests, rows):
    structured = SimpleNamespace(
        release_manifests=lambda **kw: manifests,
        observation=lambda oid: rows.get(oid),
        evidence_links=lambda **kw: [])
    return SimpleNamespace(structured=structured)


def test_published_groups_are_read_and_unpublished_groups_are_named_not_shown(monkeypatch):
    monkeypatch.setattr(ei, "_observation", lambda products, oid: _observation(oid, rows[oid]))
    rows = {"o1": _row("o1", "earnings.eps.yoy_growth", 67.5), "o2": _row("o2", "earnings.margin.x", 9.9)}
    manifests = [_manifest("a", chart="growth", period="2026Q3", observations=["o1"]),
                 _manifest("b", chart="net_profit_margin", period="2026Q3", observations=["o2"],
                           platform=False, at=1)]
    found = ei.load_published_sector_groups(_products(manifests, rows), as_of=T)
    assert found["report_date"] == "2026-10-30" and found["published_groups"] == 1
    assert list(found["sectors"]["GICS_45"]["2026Q3"]) == ["earnings.eps.yoy_growth"]
    assert found["withheld_groups"] == ["net_profit_margin/2026Q3"]


def test_nothing_published_yields_nothing():
    manifests = [_manifest("b", chart="g", period="p", observations=[], platform=False)]
    assert ei.load_published_sector_groups(_products(manifests, {}), as_of=T) == {}


def test_other_report_versions_are_not_mixed_in(monkeypatch):
    rows = {"o1": _row("o1", "m.new", 1.0), "o2": _row("o2", "m.old", 2.0)}
    monkeypatch.setattr(ei, "_observation", lambda products, oid: _observation(oid, rows[oid]))
    manifests = [_manifest("a", chart="g", period="p", observations=["o1"]),
                 _manifest("z", chart="g", period="q", observations=["o2"], report="2026-09-25", version="old")]
    found = ei.load_published_sector_groups(_products(manifests, rows), as_of=T)
    assert found["version_id"] == V and list(found["sectors"]["GICS_45"]["2026Q3"]) == ["m.new"]


def test_material_states_coverage_when_the_full_view_is_unavailable(monkeypatch):
    rows = {"o1": _row("o1", "earnings.eps.yoy_growth", 67.5)}
    monkeypatch.setattr(ei, "_observation", lambda products, oid: _observation(oid, rows[oid]))
    manifests = [_manifest("a", chart="growth", period="2026Q3", observations=["o1"])]
    monkeypatch.setenv("ATS_STRUCTURED_SECTOR_FACTSET_MODE", "platform")
    monkeypatch.setattr(factset, "_platform_snapshot", lambda products=None: ei.EarningsInsightSnapshot())
    material = factset.fetch_sector_material(products=_products(manifests, rows))
    assert material["state"] == "partial_sectors" and material["report_date"] == "2026-10-30"
    text = material["text"]
    assert "信息技术（GICS_45）" in text and "earnings.eps.yoy_growth=67.5" in text
    assert "1/11" in text and "通信服务" in text and "没有跨行业排名" in text
    assert "尚未发布" not in material["reason"] and "不可用" not in material["reason"]


def test_material_stays_unavailable_when_no_group_published(monkeypatch):
    monkeypatch.setenv("ATS_STRUCTURED_SECTOR_FACTSET_MODE", "platform")
    monkeypatch.setattr(factset, "_platform_snapshot", lambda products=None: ei.EarningsInsightSnapshot())
    material = factset.fetch_sector_material(products=_products([], {}))
    assert material["text"] == "" and "尚未发布" in material["reason"]


def _observation(observation_id, row):
    return ei.EarningsInsightObservation(
        observation_id=observation_id, entity_id=row["entity_id"], metric_id=row["metric_id"],
        period=row["period"], period_basis=row["period_basis"], value=row["value"], unit=row["unit"],
        known_at=T)
