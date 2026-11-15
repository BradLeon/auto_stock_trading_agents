"""Weekly calibration of event-component ratings (event-impact-routing 3.3, 2026-11-15)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ats.agents.information import calibration as cal

NOW = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
DAY = NOW - timedelta(days=3)       # 11-11


class Store:
    def __init__(self, rows):
        self.rows = rows

    def task_projection_envelopes(self, *, agent_role=None, **_):
        return [r for r in self.rows if r["agent_role"] == agent_role]


def _brief(comps, as_of=DAY, entity="NVDA"):
    return {"agent_role": "information_brief", "projection_id": f"b-{entity}", "scope_id": entity,
            "as_of": as_of.isoformat(), "payload": {"event_components": comps}}


def _comp(cid, materiality, entities=("NVDA",), direction="negative"):
    return {"component_id": cid, "cluster_key": cid, "content": cid, "scope": "entity", "entities": list(entities),
            "materiality": materiality, "direction": direction}


def _series(start_price, moves):
    days = [date(2026, 11, d) for d in (10, 11, 12, 13, 14)]
    out, p = [], start_price
    for d, m in zip(days, [0, *moves]):
        p *= 1 + m
        out.append((d, p))
    return out


def test_disagreements_and_cross_scope_claims_are_counted():
    rows = [
        _brief([_comp("c1", "major"), _comp("c2", "minor")]),
        {"agent_role": "fundamental_material_review", "scope_id": "NVDA", "as_of": NOW.isoformat(),
         "payload": {"component_refs": ["b-NVDA#c1"], "materiality_review": "agree：调查属实"}},
        {"agent_role": "layer_analysis", "scope_id": "L5_chip_design", "as_of": NOW.isoformat(),
         "payload": {"off_thesis_shocks": [{"component_refs": ["b-NVDA#c2"], "materiality_review": "偏低：应为中等"}],
                     "cross_scope_claims": [{"component_ref": "b-NVDA#c1", "from_entity": "NVDA",
                                             "transmission": "采购推迟", "materiality_review": "agree"}]}},
    ]
    report = cal.build(Store(rows), now=NOW, history=lambda syms: {})
    assert len(report["components"]) == 2 and len(report["reviews"]) == 3
    assert [d["review"] for d in report["disagreements"]] == ["偏低：应为中等"]
    assert len(report["cross_scope"]) == 1


def test_abnormal_return_is_against_the_layer_basket_and_grouped_by_materiality():
    comps = [{**_comp("c1", "major"), "as_of": DAY}, {**_comp("c2", "minor", entities=("AMD",)), "as_of": DAY}]
    # Sessions 11-10..11-14; the event is on 11-11 and NVDA drops 10% the next session.
    history = {"NVDA": _series(100, [0.0, -0.10, 0.0, 0.0]), "AMD": _series(100, [0.0, 0.0, 0.0, 0.0]),
               "AVGO": _series(100, [0.0, 0.0, 0.0, 0.0]), "MRVL": _series(100, [0.0, 0.0, 0.0, 0.0])}
    layers = {"L5_chip_design": {"NVDA", "AMD", "AVGO", "MRVL"}}
    rows = cal.abnormal_returns(comps, layers=layers, history=lambda syms: history)
    nvda = next(r for r in rows if r["entity"] == "NVDA")
    assert round(nvda["ar1"], 4) == -0.10 and nvda["layer"] == "L5_chip_design"
    summary = {s["materiality"]: s for s in cal.summarize(rows)}
    assert summary["major"]["mean_abs_ar1"] > summary["minor"]["mean_abs_ar1"]


def test_no_components_writes_nothing(tmp_path):
    assert cal.write(Store([]), output_dir=tmp_path, now=NOW, history=lambda s: {}) is None
    assert not list(tmp_path.glob("事件评级校准-*.md"))


def test_report_renders_and_states_a_market_data_gap(tmp_path):
    def broken(symbols):
        raise ConnectionError("yahoo down")

    path = cal.write(Store([_brief([_comp("c1", "critical")])]), output_dir=tmp_path, now=NOW, history=broken)
    text = path.read_text(encoding="utf-8")
    assert "事件评级校准" in text and "行情未取到" in text and "极重大" in text


def test_the_publisher_skips_without_a_new_sector_review():
    from ats.workflow import report_publishers

    out = report_publishers.event_calibration_report([{"task_id": "sector-review", "status": "succeeded",
                                                       "reused": True}], store_path=None)
    assert out["status"] == "skipped" and out["reason"] == "no_new_sector_review"
