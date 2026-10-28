"""v1 (release only) vs v2 (with transcript): weights, sizing, and Chief gating.

The design rule these pin down: a transcript-less v1 is scored and stored but is NOT
offered to the Chief while we keep retrying for the transcript. That keeps
score_consumption's "the Chief acts once per earnings" invariant intact without ever
needing to un-consume anything — only the final score is ever actionable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from ats.agents.pead import score as score_agents
from ats.memory import get_store
from ats.schemas.pead import (
    ActualMetric,
    Actuals,
    PeadConfig,
    Scorecard,
    ScorecardDim,
    ScorecardLine,
)

NOW = datetime.now(timezone.utc)

DIMS = [
    ScorecardDim(key="revenue", label="营收", weight=0.40),
    ScorecardDim(key="margin", label="毛利率", weight=0.35),
    ScorecardDim(key="forward_guide", label="指引", weight=0.20),
    ScorecardDim(key="call_tone", label="口风", weight=0.05),
]


def _cfg(**kw) -> PeadConfig:
    return PeadConfig(symbol="GOOG", fiscal_label="Q2 2026", scorecard_dims=DIMS,
                      long_threshold=1.0, **kw)


def _actuals(keys=("revenue", "margin")) -> Actuals:
    return Actuals(symbol="GOOG", fiscal_label="Q2 2026", as_of=NOW,
                   metrics=[ActualMetric(dim_key=k, metric=k, actual="beat",
                                         vs_expected="超") for k in keys])


# --------------------------------------------------------------------------- #
# Weight renormalization (the v1 bias this prevents)
# --------------------------------------------------------------------------- #
def test_call_only_weights_are_renormalized_not_scored_zero(monkeypatch):
    """Without a transcript, guidance+tone (0.25 of the card) have no evidence.

    Scoring them 0 is not neutral — 0 means "in line" — so it would drag every v1 a
    quarter of the way toward 中性观望. They must be dropped and the rest rescaled.
    """
    monkeypatch.setattr(score_agents, "run_structured",
                        lambda *a, **k: _scores({"revenue": 2.0, "margin": 2.0,
                                                 "forward_guide": 0.0, "call_tone": 0.0}))
    v1 = score_agents.score(_cfg(), None, _actuals(), NOW, has_transcript=False)

    kept = {ln.dim_key: ln.weight for ln in v1.lines}
    assert kept["forward_guide"] == 0.0 and kept["call_tone"] == 0.0
    assert pytest.approx(kept["revenue"] + kept["margin"], abs=1e-6) == 1.0   # rescaled
    # Two dims at +2.0 with rescaled weights => the full +2.0, not 2*0.75 = 1.5.
    assert v1.total == pytest.approx(2.0, abs=1e-6)


def test_without_renormalization_the_same_run_would_miss_the_threshold(monkeypatch):
    """Same inputs scored as if the transcript were present -> dragged below the bar."""
    monkeypatch.setattr(score_agents, "run_structured",
                        lambda *a, **k: _scores({"revenue": 2.0, "margin": 2.0,
                                                 "forward_guide": 0.0, "call_tone": 0.0}))
    as_if_v2 = score_agents.score(_cfg(), None, _actuals(), NOW, has_transcript=True)
    assert as_if_v2.total == pytest.approx(1.5, abs=1e-6)      # 2*0.40 + 2*0.35


def test_v2_keeps_the_configured_weights(monkeypatch):
    monkeypatch.setattr(score_agents, "run_structured",
                        lambda *a, **k: _scores({d.key: 1.0 for d in DIMS}))
    v2 = score_agents.score(_cfg(), None, _actuals(tuple(d.key for d in DIMS)), NOW,
                            has_transcript=True)
    assert {ln.dim_key: ln.weight for ln in v2.lines} == {d.key: d.weight for d in DIMS}


def test_renormalization_backs_off_when_nothing_is_evidenced(monkeypatch):
    """If the release evidenced no dim at all, don't zero the whole card — leave the
    weights alone and let the upstream actuals guard decide."""
    monkeypatch.setattr(score_agents, "run_structured",
                        lambda *a, **k: _scores({d.key: 0.0 for d in DIMS}))
    sc = score_agents.score(_cfg(), None, _actuals(()), NOW, has_transcript=False)
    assert sum(ln.weight for ln in sc.lines) == pytest.approx(1.0, abs=1e-6)


def _scores(mapping: dict[str, float]):
    class V:
        items = [type("I", (), {"dim_key": k, "score": v, "note": ""})()
                 for k, v in mapping.items()]
    return V()


# --------------------------------------------------------------------------- #
# Event view — Phase D 5.7: no sizing, no actions, no portfolio reads
# --------------------------------------------------------------------------- #
def _card(total: float) -> Scorecard:
    return Scorecard(symbol="GOOG", fiscal_label="Q2 2026", as_of=NOW,
                     lines=[ScorecardLine(dim_key="revenue", label="营收", weight=1.0,
                                          score=total, weighted=total, note="")],
                     total=total, threshold=1.0, band="达到做多门槛 (≥+1.0)")


def test_the_sizing_tree_is_gone():
    """Phase D 5.7：基本面分析师只判断预期差，不做仓位。旧 sizing 树
    （decide / notional_hint / qty_hint / size_factor）必须保持不存在。"""
    for gone in ("decide", "size_factor", "notional_hint", "qty_hint"):
        assert not hasattr(score_agents, gone), gone
    import inspect

    src = inspect.getsource(score_agents)
    for gone in ("PortfolioSnapshot", "notional_hint", "size_factor", "qty_hint"):
        assert gone not in src, gone


def test_event_view_is_direction_magnitude_confidence_without_actions():
    """event_view 产出可证伪的预期差视图；direction 不是动作词，magnitude
    是基本面口径的差值读数，不携带任何仓位语义。"""
    up = score_agents.event_view(_cfg(), _card(1.5), None)
    assert up["direction"] == 1
    assert up["magnitude"] == 1.5
    assert 0.0 < up["confidence"] <= 1.0
    assert "预期差为正" in up["rationale"]

    neutral = score_agents.event_view(_cfg(), _card(0.3), None)
    assert neutral["direction"] == 0

    down = score_agents.event_view(_cfg(), _card(-1.2), None)
    assert down["direction"] == -1
    assert "不做空判断属于决策链" in down["rationale"]

    for view in (up, neutral, down):
        assert set(view) == {"direction", "magnitude", "confidence", "rationale"}


# --------------------------------------------------------------------------- #
# Chief gating: only a FINAL score is actionable
# --------------------------------------------------------------------------- #
def _seed_scored_dossier(label="Q2 2026"):
    from ats.schemas.pead import PeadDossier

    get_store().save_dossier(PeadDossier(
        symbol="GOOG", fiscal_label=label, phase="score", updated_at=NOW,
        scorecard=_card(1.5), decision_summary="达成门槛→小仓位做多 | 建议: buy GOOG $1,500"))


def test_v1_is_background_to_the_chief():
    from ats.agents.chief.assemble import _pead_block

    store = get_store()
    _seed_scored_dossier()
    store.record_score_run(symbol="GOOG", fiscal_label="Q2 2026", version=1,
                           earnings_date=date(2026, 7, 22), has_transcript=False)

    block = _pead_block()
    assert "仅背景（v1 缺电话会纪要" in block
    assert "新鲜可行动" not in block
    assert "无电话会纪要" in block                    # the evidence warning is shown
    assert "PEAD 分析师建议" not in block             # ...but not the actionable line


def test_v2_is_actionable_to_the_chief():
    from ats.agents.chief.assemble import ChiefContext, _pead_block

    store = get_store()
    _seed_scored_dossier()
    store.record_score_run(symbol="GOOG", fiscal_label="Q2 2026", version=1,
                           earnings_date=date(2026, 7, 22), has_transcript=False)
    store.record_score_run(symbol="GOOG", fiscal_label="Q2 2026", version=2,
                           earnings_date=date(2026, 7, 22), has_transcript=True,
                           transcript_source="tavily:investing.com")

    ctx = ChiefContext(as_of=NOW)
    block = _pead_block(ctx=ctx)
    assert "新鲜可行动" in block
    assert "PEAD 分析师建议" in block
    assert ("GOOG", "Q2 2026") in ctx.actionable_scores


def test_promoted_v1_becomes_actionable():
    """The transcript never arrived; the v1 is promoted so the quarter isn't lost."""
    from ats.agents.chief.assemble import _pead_block

    store = get_store()
    _seed_scored_dossier()
    store.record_score_run(symbol="GOOG", fiscal_label="Q2 2026", version=1,
                           earnings_date=date(2026, 7, 22), has_transcript=False)
    assert store.promote_score_run("GOOG", "Q2 2026") is True

    block = _pead_block()
    assert "新鲜可行动" in block
    assert "无电话会纪要" in block          # still flagged as thin evidence
    assert store.promote_score_run("GOOG", "Q2 2026") is False   # idempotent


def test_consumed_score_stays_background():
    """score_consumption still governs — this is the invariant we did not touch."""
    from ats.agents.chief.assemble import _pead_block

    store = get_store()
    _seed_scored_dossier()
    store.record_score_run(symbol="GOOG", fiscal_label="Q2 2026", version=1,
                           earnings_date=date(2026, 7, 22), has_transcript=True)
    store.mark_score_consumed("GOOG", "Q2 2026", "chief-20260722-200500")

    block = _pead_block()
    assert "score 已消费" in block
    assert "新鲜可行动" not in block


def test_score_age_comes_from_the_run_not_the_monitor_bump():
    """The daily monitor rewrites the dossier and bumps updated_at, so a scored
    dossier looks 0 days old forever. Freshness must come from the score run."""
    from ats.agents.chief.assemble import FRESH_SCORE_DAYS, _pead_block
    from ats.schemas.pead import PeadDossier

    store = get_store()
    old = NOW - timedelta(days=FRESH_SCORE_DAYS + 3)
    store.save_dossier(PeadDossier(symbol="GOOG", fiscal_label="Q2 2026", phase="score",
                                   updated_at=NOW,          # monitor touched it today
                                   scorecard=_card(1.5),
                                   decision_summary="buy GOOG"))
    store.conn.execute(
        "INSERT OR REPLACE INTO pead_score_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("GOOG", "Q2 2026", 1, old.isoformat(), "2026-07-22", 1, "x", "amc",
         None, 1.5, "band", "buy GOOG", 1))
    store.conn.commit()

    assert "新鲜可行动" not in _pead_block()
