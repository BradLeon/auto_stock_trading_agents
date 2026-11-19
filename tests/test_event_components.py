"""Rated event components on information briefs (event-impact-routing 2.1–2.3, 2026-11-13)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.agents.information import event_components as ec

NOW = datetime(2026, 11, 12, 13, 0, tzinfo=timezone.utc)


def _payload():
    facts = ["来源信息（WSJ）：英伟达董事会调查 CEO 黄仁勋", "来源信息（WSJ）：国会议员借此推动更严格的 AI 芯片出口法案",
             "来源信息（IBD）：英伟达股价周二下跌 5.5%"]
    evidence = [{"fact_index": i, "document_ref": f"DOWJONES:doc{i}@v1", "locator": {"start": 10, "end": 90}}
                for i in range(3)]
    return {"entity": "NVDA", "headline": "NVDA 信息材料更新", "summary": "；".join(facts), "relevance": "high",
            "sources": ["ibkr_news"], "fact_changes": facts, "source_evidence": evidence, "confidence": 0.8,
            "unverified": ["来源陈述尚需独立核验"]}


class Store:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def task_projection_envelopes(self, **_):
        return self.rows


def _view(**kw):
    base = dict(fact_indices=[0], cluster_key="NVDA CEO probe", content="董事会调查 CEO", scope="entity",
                materiality="major", rationale="治理：创始人 CEO 被调查；范围：本公司")
    return ec.RatedComponentView(**{**base, **kw})


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(ec, "settings", lambda path=None: {"enabled": True, "novelty_window_days": 7, "max_facts": 40})


def test_switched_off_makes_no_request_and_leaves_the_brief_alone(monkeypatch):
    monkeypatch.setattr(ec, "settings", lambda path=None: {"enabled": False, "novelty_window_days": 7, "max_facts": 40})
    payload = _payload()
    out, refs = ec.attach(Store(), payload, symbol="NVDA", now=NOW, rate=lambda ctx: pytest.fail("rated while off"))
    assert out is payload and refs == [] and "event_components" not in out


def test_the_repo_switch_is_off_by_default():
    assert ec.settings()["enabled"] is False


def test_components_are_rated_with_citations_taken_from_the_brief(on):
    batch = ec.ComponentBatchView(components=[
        _view(),
        _view(fact_indices=[1], cluster_key="us-ai-chip-export-bill", content="国会推动芯片出口法案",
              scope="cross_layer", layers=["L5_chip_design", "L7_foundry_pkg"], entities=["NVDA", "AMD", "TSM"],
              role="contagion")])
    out, refs = ec.attach(Store(), _payload(), symbol="NVDA", now=NOW, rate=lambda ctx: batch)
    comps = {c["cluster_key"]: c for c in out["event_components"]}
    assert comps["nvda-ceo-probe"]["citations"] == ["DOWJONES:doc0@v1#10-90"]
    assert comps["nvda-ceo-probe"]["novelty"] == "first" and comps["nvda-ceo-probe"]["entities"] == ["NVDA"]
    assert comps["us-ai-chip-export-bill"]["scope"] == "cross_layer"
    assert refs and refs[0].startswith("event-components:")


def test_a_major_component_without_a_traceable_citation_is_downgraded(on):
    payload = _payload()
    payload["source_evidence"] = [{"fact_index": 0, "document_ref": "DOWJONES:doc0@v1", "locator": {}}]
    out, _ = ec.attach(Store(), payload, symbol="NVDA", now=NOW,
                       rate=lambda ctx: ec.ComponentBatchView(components=[_view()]))
    comp = out["event_components"][0]
    assert comp["materiality"] == "moderate" and comp["downgraded_from"] == "major" and comp["downgrade_reason"]


def test_unknown_layers_or_tickers_are_refused_and_disclosed(on):
    batch = ec.ComponentBatchView(components=[
        _view(scope="layer", layers=["L9_space"], cluster_key="x"),
        _view(scope="entity", entities=["ZZZZ"], cluster_key="y"),
        _view(scope="cross_layer", layers=["L5_chip_design"], cluster_key="z")])
    out, _ = ec.attach(Store(), _payload(), symbol="NVDA", now=NOW, rate=lambda ctx: batch)
    assert "event_components" not in out
    notes = " ".join(out["unverified"])
    assert "L9_space" in notes and "ZZZZ" in notes and "至少涉及两层" in notes


def test_advice_in_a_component_is_refused(on):
    out, _ = ec.attach(Store(), _payload(), symbol="NVDA", now=NOW,
                       rate=lambda ctx: ec.ComponentBatchView(components=[_view(content="建议减持英伟达")]))
    assert "event_components" not in out and any("交易建议" in n for n in out["unverified"])


def test_novelty_follows_the_cluster_ledger_and_only_the_model_calls_escalation(on):
    seen = [{"as_of": (NOW - timedelta(days=2)).isoformat(),
             "payload": {"event_components": [{"cluster_key": "nvda-ceo-probe", "materiality": "major",
                                                "content": "调查"}]}}]
    out, _ = ec.attach(Store(seen), _payload(), symbol="NVDA", now=NOW,
                       rate=lambda ctx: ec.ComponentBatchView(components=[_view()]))
    assert out["event_components"][0]["novelty"] == "follow_up"
    out, _ = ec.attach(Store(seen), _payload(), symbol="NVDA", now=NOW,
                       rate=lambda ctx: ec.ComponentBatchView(components=[_view(escalation=True)]))
    assert out["event_components"][0]["novelty"] == "escalation"
    old = [{**seen[0], "as_of": (NOW - timedelta(days=9)).isoformat()}]
    out, _ = ec.attach(Store(old), _payload(), symbol="NVDA", now=NOW,
                       rate=lambda ctx: ec.ComponentBatchView(components=[_view(escalation=True)]))
    assert out["event_components"][0]["novelty"] == "first"


def test_the_rater_sees_recent_clusters_so_it_can_reuse_their_keys(on):
    seen = [{"as_of": (NOW - timedelta(days=1)).isoformat(),
             "payload": {"event_components": [{"cluster_key": "us-ai-chip-export-bill", "materiality": "major",
                                                "content": "出口法案"}]}}]
    captured = {}

    def rate(ctx):
        captured["ctx"] = ctx
        return ec.ComponentBatchView()

    ec.attach(Store(seen), _payload(), symbol="NVDA", now=NOW, rate=rate)
    assert "us-ai-chip-export-bill" in captured["ctx"] and "L5_chip_design" in captured["ctx"]


def test_a_failed_rating_publishes_the_brief_without_components(on):
    def boom(ctx):
        raise TimeoutError("relay")

    out, refs = ec.attach(Store(), _payload(), symbol="NVDA", now=NOW, rate=boom)
    assert "event_components" not in out and refs == []
    assert any("事件成分评级未完成" in n for n in out["unverified"])


def test_components_survive_the_brief_schema():
    from ats.agent import task_projection as tp

    batch = ec.ComponentBatchView(components=[_view()])
    comps, _ = ec.validate_components("NVDA", batch.components, _payload(), layers=ec.chain_scope(), recent={})
    brief = tp.validate_payload("information_brief", {**_payload(), "event_components": comps})
    assert brief.event_components[0].cluster_key == "nvda-ceo-probe"


def _config_with_switch(tmp_path, enabled):
    import shutil
    from pathlib import Path

    import yaml

    root = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", root)
    path = root / "workflow" / "event_impact.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["components"]["enabled"] = enabled
    path.write_text(yaml.safe_dump(doc))
    return root


def test_light_briefs_cover_chain_observe_names_only_when_switched_on(tmp_path):
    from ats.agent.task_projection import ProjectionScope
    from ats.workflow import planning

    daily = ProjectionScope(kind="portfolio", id="pead-targets")
    off = _config_with_switch(tmp_path / "off", False)
    assert [s.id for s in planning._scopes("information-brief", daily, off)] == planning._targets(off)
    on = _config_with_switch(tmp_path / "on", True)
    extra = [s.id for s in planning._scopes("information-brief", daily, on)][len(planning._targets(on)):]
    assert set(extra) == {"META", "ORCL", "005930.KS", "SPCX", "INTC", "AMD", "AMAT", "CRWV", "NBIS"}
    # No Fundamental follows a light brief, and decision plans (sector scope) are unchanged.
    assert len(planning._scopes("fundamental-routine", daily, on)) == len(planning._targets(on))
    sector = ProjectionScope(kind="sector", id="ai_hardware")
    assert len(planning._scopes("information-brief", sector, on)) == len(planning._targets(on))


def test_two_components_of_one_cluster_and_scope_get_distinct_ids():
    batch = ec.ComponentBatchView(components=[_view(fact_indices=[0]), _view(fact_indices=[2], content="市场担忧 CEO 去留")])
    comps, _ = ec.validate_components("NVDA", batch.components, _payload(), layers=ec.chain_scope(), recent={})
    assert len({c["component_id"] for c in comps}) == 2


def test_a_major_positive_event_keeps_its_direction(on):
    """2026-11-15 benchmark: a long-term contract covering a third of revenue is material although it is good news."""
    view = _view(content="美光签下覆盖至 2030 年逾 35% 营收的长约", materiality="major", direction="positive",
                 cluster_key="mu-long-term-contracts")
    out, _ = ec.attach(Store(), _payload(), symbol="NVDA", now=NOW,
                       rate=lambda ctx: ec.ComponentBatchView(components=[view]))
    comp = out["event_components"][0]
    assert comp["materiality"] == "major" and comp["direction"] == "positive"
