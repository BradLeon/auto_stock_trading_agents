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


def test_actuals_view_coerces_currency_formatted_numbers():
    # Regression: gemini returned reported_eps='€7.35', reported_revenue='€9.7 billion'
    # (currency + scale words). Hard float-parsing failed the whole extraction and
    # zeroed the scorecard. ActualsView must coerce instead of raising.
    from ats.agents.pead.outputs import ActualsView

    a = ActualsView(reported_eps="€7.35", reported_revenue="€9.7 billion")
    assert a.reported_eps == 7.35
    assert a.reported_revenue == 9.7e9

    b = ActualsView(reported_eps="$1,234M", reported_revenue="n/a")
    assert b.reported_eps == 1.234e9
    assert b.reported_revenue is None      # unparseable -> None, not a crash


def test_llm_list_views_coerce_stringified_json_arrays():
    # Regression: gemini-flash serialized `rows`/`items`/`metrics` as a JSON *string*
    # ('[{...}, {...}]') instead of a real array. list-type validation dropped every
    # element (NVDA prep landed 0 expectation rows). Coerce the string back to a list.
    import json

    from ats.agents.pead.outputs import (
        ActualsView,
        ExpectationsView,
        ScoresView,
        SignalChainView,
    )

    rows = json.dumps([{"dim_key": "datacenter_rev", "metric": "DC营收", "neutral": "$85B"},
                       {"dim_key": "forward_guide", "metric": "次季指引"}])
    ev = ExpectationsView(rows=rows)
    assert len(ev.rows) == 2 and ev.rows[0].dim_key == "datacenter_rev"

    sc = SignalChainView(items=json.dumps([{"symbol": "TSM", "signal": "CoWoS 产能"}]), summary="x")
    assert len(sc.items) == 1 and sc.items[0].symbol == "TSM"

    assert len(ScoresView(items=json.dumps([{"dim_key": "a", "score": 1.0}])).items) == 1
    assert len(ActualsView(metrics=json.dumps([{"dim_key": "a", "actual": "x"}])).metrics) == 1

    # Real lists and empties still pass through untouched.
    assert len(ExpectationsView(rows=[{"dim_key": "z"}]).rows) == 1
    assert ExpectationsView(rows="").rows == []


def test_narrative_focus_ranking_coerces_stringified_array():
    # Regression: sonnet serialized focus_ranking as a JSON-array *string*, so the
    # report rendered all 7 focuses as one giant list item. Coerce back to a list.
    import json

    from ats.agents.pead.outputs import NarrativeView

    # clean JSON array as a string
    nv = NarrativeView(narrative="t", focus_ranking=json.dumps(["数据中心营收", "毛利率路径", "供给约束"]))
    assert nv.focus_ranking == ["数据中心营收", "毛利率路径", "供给约束"]

    # pseudo-JSON with UNESCAPED inner ASCII quotes (the real NVDA failure) — json.loads
    # fails, so the tolerant '","' splitter must recover the items.
    blob = '["次季指引措辞强度 —— 看 "demand visibility" 出现频率", "中国区 —— "合规路径拓宽" 信号", "OpEx 杠杆"]'
    nv2 = NarrativeView(narrative="t", focus_ranking=blob)
    assert len(nv2.focus_ranking) == 3
    assert nv2.focus_ranking[2] == "OpEx 杠杆"

    # a one-element list whose sole member is the pseudo-JSON blob (how it reached the store)
    nv3 = NarrativeView(narrative="t", focus_ranking=[blob])
    assert len(nv3.focus_ranking) == 3

    # genuine lists and empties untouched
    assert NarrativeView(narrative="t", focus_ranking=["a", "b"]).focus_ranking == ["a", "b"]
    assert NarrativeView(narrative="t", focus_ranking=[]).focus_ranking == []
