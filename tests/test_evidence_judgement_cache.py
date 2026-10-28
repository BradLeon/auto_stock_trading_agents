"""The evidence adjudicator's answers are cached by (model config, prompt implementation, prompt text).

The same claim and clusters were adjudicated by the Layer review and then twice by the Sector review with
byte-identical prompts (26 requests became 78 per weekly run).
"""
from __future__ import annotations

import pytest

from ats.agents.evidence import adjudicator
from ats.agents.evidence.outputs import (AdjudicationView, ClusterJudgementView, CrossSectionView,
                                         EntityReadingView)
from ats.schemas.chain import ClaimDef, EvidenceCluster


@pytest.fixture(autouse=True)
def cache_on(monkeypatch):
    monkeypatch.delenv("ATS_NO_JUDGEMENT_CACHE", raising=False)
    # A private table per test: the memory store used by tests may be shared.
    from ats.memory import get_store

    get_store().conn.execute("DROP TABLE IF EXISTS llm_judgement_cache")


def _claim(statement="supply stays tight"):
    return ClaimDef(id="tight", kind="common", statement=statement)


def _clusters(n=3, text="HBM sold out"):
    return [EvidenceCluster(key=f"S{i}|S{i}|capacity|up", speaker=f"S{i}", entity=f"S{i}", concept="capacity",
                            direction="up", rows=[{"evidence_span": f"{text} {i}", "period": "2026Q3"}])
            for i in range(n)]


class Model:
    """Counts requests; `partial` answers only the first cluster (the flake the retry exists for)."""

    def __init__(self, partial=False):
        self.calls, self.partial = 0, partial

    def __call__(self, role, schema, context, **kw):
        self.calls += 1
        keys = [line.split(" id=", 1)[1].strip() for line in context.splitlines() if " id=" in line]
        if schema is AdjudicationView:
            keys = keys[:1] if self.partial else keys
            return AdjudicationView(judgements=[ClusterJudgementView(cluster_key=k, polarity="support", reason="r")
                                                for k in keys])
        return CrossSectionView(readings=[EntityReadingView(entity="A", standing="strong", reason="r",
                                                            key_clusters=keys[:1]),
                                          EntityReadingView(entity="B", standing="weak", reason="r")])


def _install(monkeypatch, model):
    monkeypatch.setattr(adjudicator, "run_structured", model)


def test_an_identical_prompt_is_answered_from_the_cache(monkeypatch):
    model = Model()
    _install(monkeypatch, model)
    first = adjudicator.judge(_claim(), _clusters())
    second = adjudicator.judge(_claim(), _clusters())
    third = adjudicator.judge(_claim(), _clusters())
    assert model.calls == 1
    assert [j.polarity for j in first] == [j.polarity for j in second] == [j.polarity for j in third] == ["support"] * 3


def test_a_cached_judgement_is_rebound_to_the_current_clusters(monkeypatch):
    model = Model()
    _install(monkeypatch, model)
    adjudicator.judge(_claim(), _clusters())
    clusters = _clusters()
    clusters[0].stance = "customer"           # not part of the prompt, but part of the judgement we hand back
    out = adjudicator.judge(_claim(), clusters)
    assert model.calls == 1 and out[0].cluster_key == clusters[0].key and out[0].stance == "customer"


def test_changed_evidence_or_claim_is_a_miss(monkeypatch):
    model = Model()
    _install(monkeypatch, model)
    adjudicator.judge(_claim(), _clusters())
    adjudicator.judge(_claim(), _clusters(text="HBM now in surplus"))
    adjudicator.judge(_claim("pricing power holds"), _clusters())
    assert model.calls == 3


def test_a_changed_model_configuration_is_a_miss(monkeypatch):
    model = Model()
    _install(monkeypatch, model)
    adjudicator.judge(_claim(), _clusters())
    from ats.config import get_config

    role = get_config().app.llm.routing["evidence_adjudicator"]
    monkeypatch.setattr(role, "max_tokens", (role.max_tokens or 8192) + 1)
    adjudicator.judge(_claim(), _clusters())
    assert model.calls == 2


def test_a_short_answer_is_never_frozen_into_the_cache(monkeypatch):
    model = Model(partial=True)
    _install(monkeypatch, model)
    adjudicator.judge(_claim(), _clusters())            # 1 of 3 judged, retried once, still short
    first_calls = model.calls
    model.partial = False
    adjudicator.judge(_claim(), _clusters())            # not served from the short answer
    assert first_calls == 2 and model.calls == first_calls + 1


def test_the_environment_switch_forces_fresh_calls(monkeypatch):
    model = Model()
    _install(monkeypatch, model)
    adjudicator.judge(_claim(), _clusters())
    monkeypatch.setenv("ATS_NO_JUDGEMENT_CACHE", "1")
    adjudicator.judge(_claim(), _clusters())
    assert model.calls == 2


def test_cross_section_readings_are_cached_when_every_entity_was_read(monkeypatch):
    model = Model()
    _install(monkeypatch, model)
    claim = ClaimDef(id="share", kind="relative", statement="share holds", entities=["A", "B"])
    by_entity = {"A": _clusters(1), "B": _clusters(1, text="other")}
    first = adjudicator.judge_cross_section(claim, by_entity)
    second = adjudicator.judge_cross_section(claim, by_entity)
    assert model.calls == 1
    assert [(r.entity, r.standing) for r in first] == [(r.entity, r.standing) for r in second] == [("A", "strong"), ("B", "weak")]


def test_an_incomplete_cross_section_is_not_cached(monkeypatch):
    class OneEntity(Model):
        def __call__(self, role, schema, context, **kw):
            self.calls += 1
            return CrossSectionView(readings=[EntityReadingView(entity="A", standing="strong", reason="r")])

    model = OneEntity()
    _install(monkeypatch, model)
    claim = ClaimDef(id="share", kind="relative", statement="share holds", entities=["A", "B"])
    by_entity = {"A": _clusters(1), "B": _clusters(1, text="other")}
    adjudicator.judge_cross_section(claim, by_entity)
    adjudicator.judge_cross_section(claim, by_entity)
    assert model.calls == 2


def test_a_broken_cache_never_changes_the_answer(monkeypatch):
    model = Model()
    _install(monkeypatch, model)
    monkeypatch.setattr("ats.memory.get_store", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    out = adjudicator.judge(_claim(), _clusters())
    assert model.calls == 1 and [j.polarity for j in out] == ["support"] * 3
