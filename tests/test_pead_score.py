"""PEAD scorecard weighting + decision tree (deterministic, no network).

Includes the COHR Q3FY26 replay: feeding the doc's per-dimension scores through
the weighting must reproduce ~+0.96 and the "below special +1.5 bar → no entry".
"""

from datetime import datetime, timezone

from ats.agents.pead import score as score_mod
from ats.agents.pead.outputs import ScoreItemView, ScoresView
from ats.config import load_pead_config
from ats.schemas.pead import Actuals, Scorecard, ScorecardLine
from ats.schemas.portfolio import Position, PortfolioSnapshot

NOW = datetime.now(timezone.utc)

# Per-dimension scores recorded in 已完成-基本面分析-COHR-Q3FY2026.md §4.
COHR_DOC_SCORES = {
    "gross_margin": 0.5, "forward_guide": 1.25, "revenue": 0.5, "datacom": 1.5,
    "eps": 0.25, "inp_capacity": 1.5, "t16": 0.5, "ocs": 1.5, "bookings": 1.5, "call_tone": 1.5,
}


def test_cohr_replay_scorecard_total_and_band(monkeypatch):
    cfg = load_pead_config("COHR")
    view = ScoresView(items=[ScoreItemView(dim_key=k, score=v, note="") for k, v in COHR_DOC_SCORES.items()])
    monkeypatch.setattr(score_mod, "run_structured", lambda *a, **k: view)

    sc = score_mod.score(cfg, None, Actuals(symbol="COHR", as_of=NOW), NOW)
    # Doc reports +0.96 (their intermediate rounding); exact weighting = +0.9875.
    assert 0.90 <= sc.total <= 1.00
    assert sc.threshold == 1.5
    assert "未达门槛" in sc.band      # below the COHR special +1.5 bar


def test_cohr_replay_decision_is_no_entry():
    cfg = load_pead_config("COHR")
    sc = Scorecard(symbol="COHR", as_of=NOW, total=0.96, threshold=1.5,
                   lines=[], band="温和正面但未达门槛")
    decisions, band, _ = score_mod.decide(cfg, sc, run_up_vs_sector=-12.0,
                                           portfolio=None, net_liquidation=100000)
    assert decisions == []             # no entry — matches the doc's 决策


def test_decision_long_when_clears_bar_and_runup_ok():
    cfg = load_pead_config("COHR")
    sc = Scorecard(symbol="COHR", as_of=NOW, total=1.8, threshold=1.5, lines=[])
    decisions, band, _ = score_mod.decide(cfg, sc, run_up_vs_sector=2.0,
                                           portfolio=None, net_liquidation=100000)
    assert len(decisions) == 1 and decisions[0].action == "buy"


def test_decision_observe_when_runup_overheated():
    cfg = load_pead_config("COHR")
    sc = Scorecard(symbol="COHR", as_of=NOW, total=1.8, threshold=1.5, lines=[])
    decisions, band, _ = score_mod.decide(cfg, sc, run_up_vs_sector=9.0,
                                           portfolio=None, net_liquidation=100000)
    assert decisions == [] and "抢跑" in band


def test_decision_trims_when_holding_and_below_bar():
    cfg = load_pead_config("COHR")
    sc = Scorecard(symbol="COHR", as_of=NOW, total=0.9, threshold=1.5, lines=[])
    pf = PortfolioSnapshot(as_of=NOW, net_liquidation=100000, positions=[
        Position(symbol="COHR", qty=100, avg_cost=300, market_price=326, market_value=32600)])
    decisions, band, _ = score_mod.decide(cfg, sc, run_up_vs_sector=-12.0,
                                           portfolio=pf, net_liquidation=100000)
    assert len(decisions) == 1 and decisions[0].action == "trim"
    assert decisions[0].qty == 30      # 30% of 100


def test_band_thresholds():
    assert "达到做多门槛" in score_mod._band(1.6, 1.5)
    assert "未达门槛" in score_mod._band(0.9, 1.5)
    assert "中性观望" in score_mod._band(0.0, 1.5)
    assert score_mod._band(-1.0, 1.5) == "负面"
