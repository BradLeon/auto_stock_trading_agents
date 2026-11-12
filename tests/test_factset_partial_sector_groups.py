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
    assert "2026Q3 earnings.eps.yoy_growth" in text and "信息技术 67.5" in text
    assert "（1/11）" in text and "通信服务" in text and "n 小于 11 的行不得据此断言相对强弱" in text
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


def test_eleven_sectors_are_ranked_per_metric_and_unpublished_groups_are_listed_with_their_scope(monkeypatch):
    names = ["GICS_10", "GICS_15", "GICS_20", "GICS_25", "GICS_30", "GICS_35",
             "GICS_40", "GICS_45", "GICS_50", "GICS_55", "GICS_60"]
    rows = {f"o{i}": _row(f"o{i}", "earnings.eps.yoy_growth", float(i), entity=e) for i, e in enumerate(names)}
    monkeypatch.setattr(ei, "_observation", lambda products, oid: _observation(oid, rows[oid]))
    manifests = [_manifest("a", chart="growth", period="2026Q3", observations=list(rows))]
    manifests[0]["quality"]["group"]["scope_id"] = "all_sectors"
    shadow = _manifest("b", chart="eps_guidance", period="2026Q4", observations=[], platform=False, at=1)
    shadow["quality"]["group"]["scope_id"] = "all_sectors"
    monkeypatch.setenv("ATS_STRUCTURED_SECTOR_FACTSET_MODE", "platform")
    monkeypatch.setattr(factset, "_platform_snapshot", lambda products=None: ei.EarningsInsightSnapshot())
    material = factset.fetch_sector_material(products=_products(manifests + [shadow], rows))
    text = material["text"]
    assert "（11/11）" in text and "没有数据的行业" not in text
    assert text.index("房地产 10") < text.index("能源 0")                      # best first
    assert "all_sectors:eps_guidance/2026Q4" in text
    assert "只有 n=11 的指标可做跨行业排名" in material["reason"]


def test_a_later_attempt_that_did_not_pass_withdraws_the_earlier_publication(monkeypatch):
    # 2026-11-03: a re-read caught a dropped decimal point in a cell that had already published. The package
    # differs (so its partition key differs) but it is the same chart, period and scope: the newer attempt decides.
    rows = {"old": _row("old", "consensus.target.upside", 74.0, entity="GICS_30"),
            "keep": _row("keep", "earnings.eps.yoy_growth", 5.1, entity="GICS_30")}
    monkeypatch.setattr(ei, "_observation", lambda products, oid: _observation(oid, rows[oid]))
    ratings = {"scope_id": "all_sectors", "chart_id": "target_ratings", "period": "2026-10-30"}
    earlier = _manifest("first", chart="target_ratings", period="2026-10-30", observations=["old"], at=0)
    earlier["quality"]["group"] = dict(ratings)
    later = _manifest("second", chart="target_ratings", period="2026-10-30", observations=[], platform=False, at=5)
    later["quality"]["group"] = dict(ratings)
    growth = _manifest("g", chart="growth", period="2026Q3", observations=["keep"], at=1)
    growth["quality"]["group"]["scope_id"] = "all_sectors"
    found = ei.load_published_sector_groups(_products([later, growth, earlier], rows), as_of=T)   # newest first
    assert found["withheld_groups"] == ["all_sectors:target_ratings/2026-10-30"]
    assert found["published_groups"] == 1
    assert found["observation_ids"] == ["keep"] and "consensus.target.upside" not in str(found["sectors"])
