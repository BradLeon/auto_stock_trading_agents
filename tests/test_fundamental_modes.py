"""Phase D task group 5: 基本面例行/事件双模式，剥离内部风控。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)


def _store():
    from ats.memory.store import TradingMemory

    return TradingMemory(":memory:")


def _es(symbol="COHR", fiscal="Q3 FY2026", expectations=None):
    from ats.schemas.pead import Expectation, ExpectationSet

    if expectations is None:
        expectations = [
            Expectation(dim_key="gross_margin", metric="毛利率", conservative="20",
                        neutral="25", optimistic="30", source="baseline"),
            Expectation(dim_key="forward_guide", metric="指引", conservative="flat",
                        neutral="+5%", optimistic="+10%", source="baseline"),
        ]
    return ExpectationSet(symbol=symbol, fiscal_label=fiscal, as_of=NOW,
                          narrative="基线叙事", expectations=expectations)


def _seed_baseline_dossier(store, symbol="COHR", fiscal="Q3 FY2026"):
    from ats.schemas.pead import PeadDossier

    store.save_dossier(PeadDossier(symbol=symbol, fiscal_label=fiscal, phase="prep",
                                   updated_at=NOW, expectation_set=_es(symbol, fiscal)))


# --- 5.1 双模式触发契约与显式入口 ------------------------------------------------ #

def test_trigger_contract_keeps_modes_apart():
    from ats.agents.fundamental import entry

    routine = entry.routine_request("cohr", trigger="information_brief_update")
    event = entry.event_request("cohr", trigger="earnings_release",
                                fiscal_label="Q3 FY2026", cutoff=NOW.isoformat())
    assert routine.mode == "routine" and event.mode == "event"
    assert routine.symbol == "COHR" and event.fiscal_label == "Q3 FY2026"

    with pytest.raises(ValueError):
        entry.routine_request("COHR", trigger="earnings_release")       # 事件触发 ≠ 例行
    with pytest.raises(ValueError):
        entry.event_request("COHR", trigger="information_brief_update",
                            fiscal_label="Q3 FY2026")                   # 简报触发 ≠ 事件
    with pytest.raises(ValueError):
        entry.event_request("COHR", trigger="earnings_release",
                            fiscal_label="")                            # 事件必须带报告期
    with pytest.raises(ValueError):
        entry.build_run_request("shadow", "COHR", trigger="earnings_release")


def test_run_pass_dispatch_and_routine_no_brief_noop(monkeypatch):
    from ats.agents.fundamental import entry

    store = _store()
    monkeypatch.setattr("ats.memory.get_store", lambda: store)
    req = entry.routine_request("COHR", trigger="information_brief_update")
    out = entry.run_fundamental_pass(req)
    assert out["mode"] == "routine" and out["published"] == []
    assert "no information brief" in out["note"]


# --- 5.2 例行模式：四类归类与预期更新投影 ---------------------------------------- #

def test_routine_classifies_four_ways_and_publishes_updates(monkeypatch):
    from ats.agents.fundamental import routine

    store = _store()
    _seed_baseline_dossier(store)
    monkeypatch.setattr("ats.memory.get_store", lambda: store)

    changes = [
        "gross_margin 毛利率实际读数 25.5%",          # 与中性 25 偏差 2% → 确认
        "gross_margin 毛利率大跌至 12",               # 偏差 52% → 否定
        "new_plant 宣布新建马来西亚工厂",              # 基线未跟踪 → 新增
        "forward_guide 指引节奏仍待管理层确认",        # 无数值 → 待验证
    ]
    baseline = routine.load_baseline(store, "COHR")
    labels = routine.classify_changes(changes, baseline, use_llm=False)
    assert labels == [routine.CONFIRM, routine.REFUTE, routine.NEW, routine.PENDING]

    drafts = routine.build_expectation_updates(changes, labels, baseline)
    ids = [routine.publish_expectation_update(store, d) for d in drafts]
    assert len(ids) == 4 and len(set(ids)) == 4

    rows = store.task_projection_envelopes(
        agent_role="fundamental_expectation_update", scope_kind="entity",
        scope_id="COHR", limit=50)
    assert len(rows) == 4
    drivers = " || ".join(r["payload"]["driver"] for r in rows)
    for label in routine.CLASSIFICATIONS:
        assert f"[{label}]" in drivers          # 四类归类随投影留痕
    refute = next(r for r in rows if "[否定]" in r["payload"]["driver"])
    assert refute["payload"]["previous_value"] == 25.0
    assert refute["payload"]["new_value"] == 12.0
    # 输出不含交易动作（词表拦截在发布路径）。
    from ats.agent.task_projection import EnvelopeValidationError

    with pytest.raises(EnvelopeValidationError):
        routine.publish_expectation_update(store, {
            "entity": "COHR", "metric": "x", "period": "p",
            "previous_value": 1.0, "new_value": 1.0,
            "driver": "[新增] 建议买入 NVDA"})


def test_routine_pass_runs_end_to_end(monkeypatch):
    from ats.agent.task_projection import ProjectionScope, build_envelope
    from ats.agents.fundamental import entry

    store = _store()
    _seed_baseline_dossier(store)
    payload = {
        "entity": "COHR", "headline": "供应链读数", "summary": "毛利率读数与基线一致",
        "relevance": "high", "sources": ["rss:test"],
        "fact_changes": ["gross_margin 毛利率实际读数 25.5%"],
        "impact_candidates": ["margin"], "entities": ["COHR"],
        "confidence": 0.6, "freshness": "published now", "unverified": ["节奏待核验"],
    }
    env = build_envelope(role="information_brief", payload=payload,
                         scope=ProjectionScope(kind="entity", id="COHR"),
                         as_of=NOW.isoformat())
    store.save_task_projection_envelope(env)
    monkeypatch.setattr("ats.memory.get_store", lambda: store)

    out = entry.run_fundamental_pass(
        entry.routine_request("COHR", trigger="information_brief_update", use_llm=False))
    assert out["published"] and out["summary"].get("确认") == 1


# --- 5.3 事件模式：cutoff 冻结基线、期间不符排除 --------------------------------- #

def test_event_freeze_is_immutable_after_cutoff():
    from ats.agents.fundamental import event

    store = _store()
    _seed_baseline_dossier(store)
    frozen = event.freeze_baseline(store, symbol="COHR", fiscal_label="Q3 FY2026",
                                   cutoff=NOW.isoformat())
    assert frozen["narrative"] == "基线叙事" and frozen["expectations"]

    # cutoff 之后 prep 重写 dossier / 简报更新到达，都不改写冻结记录。
    _seed_baseline_dossier(store, fiscal="Q3 FY2026")
    again = event.load_frozen_baseline(store, symbol="COHR", fiscal_label="Q3 FY2026")
    assert again == frozen

    event.record_exclusion(store, symbol="COHR", fiscal_label="Q3 FY2026",
                           item={"kind": "wrong_period", "detail": "transcript 属上一季度"})
    assert event.read_exclusions(store, symbol="COHR", fiscal_label="Q3 FY2026") != []
    # 排除留痕独立成键：冻结基线仍未被触碰。
    assert event.load_frozen_baseline(store, symbol="COHR", fiscal_label="Q3 FY2026") == frozen


# --- 5.4 三类差异分列、方向分歧保留 ---------------------------------------------- #

def test_tri_diffs_keep_disagreement():
    from ats.schemas.pead import ActualMetric, Actuals, MarketSetup

    from ats.agents.fundamental.event import compute_tri_diffs

    frozen = {"expectations": [
        {"dim_key": "gross_margin", "metric": "毛利率", "neutral": "25"}], "narrative": ""}
    actuals = Actuals(symbol="COHR", fiscal_label="Q3 FY2026", as_of=NOW,
                      metrics=[ActualMetric(dim_key="gross_margin", metric="毛利率",
                                            actual="30", vs_expected="超")],
                      reported_eps=1.10)
    es = _es()
    es.consensus_eps = 1.00
    market = MarketSetup(symbol="COHR", as_of=NOW, expected_move_pct=8.0)

    tri = compute_tri_diffs(frozen_baseline=frozen, expectation_set=es,
                            actuals=actuals, market_setup=market)
    # 基线口径：30 vs 25 → 正
    assert tri["vs_baseline"] and tri["directions"]["vs_baseline"] == 1
    # Consensus 口径：1.10 vs 1.00 → 正
    assert tri["vs_consensus"]["eps"]["delta_pct"] == pytest.approx(0.1)
    assert tri["directions"]["vs_consensus"] == 1
    # 市场隐含：惊喜 10% 超出 EM 8% → 没定价 → 正；若 EM 12% 则已在价内 → 0
    assert tri["directions"]["vs_market"] == 1
    tri2 = compute_tri_diffs(frozen_baseline=frozen, expectation_set=es,
                             actuals=actuals,
                             market_setup=MarketSetup(symbol="COHR", as_of=NOW,
                                                      expected_move_pct=12.0))
    assert tri2["directions"]["vs_market"] == 0
    # 方向不一致时保留分歧、不取平均。
    frozen_neg = {"expectations": [
        {"dim_key": "gross_margin", "metric": "毛利率", "neutral": "35"}]}
    tri3 = compute_tri_diffs(frozen_baseline=frozen_neg, expectation_set=es,
                             actuals=actuals, market_setup=market)
    assert tri3["directions"]["vs_baseline"] == -1
    assert tri3["directions"]["vs_consensus"] == 1
    assert tri3["directions_agree"] is False
    assert "未取平均" in tri3["note"]


# --- 5.5 迟到材料产出新版本、早期版本保留 ---------------------------------------- #

def test_late_transcript_creates_new_version_keeping_the_old():
    from ats.agents.fundamental import event
    from ats.schemas.pead import Scorecard

    store = _store()
    view1 = {"direction": 0, "magnitude": 0.2, "confidence": 0.3,
             "rationale": "仅财报稿，方向中性"}
    sc = Scorecard(symbol="COHR", fiscal_label="Q3 FY2026", as_of=NOW,
                   lines=[], total=0.2, threshold=1.5, band="中性观望")
    p1 = event.build_event_review_payload(symbol="COHR", period="Q3 FY2026",
                                          event="earnings", scorecard=sc,
                                          event_view=view1, tri_diffs={}, actuals=None)
    id1 = event.publish_event_review(store, p1, as_of="2026-09-24T08:00:00+00:00")

    view2 = {"direction": 1, "magnitude": 1.8, "confidence": 0.8,
             "rationale": "电话会迟到后上调：指引超预期"}
    p2 = event.build_event_review_payload(symbol="COHR", period="Q3 FY2026",
                                          event="earnings", scorecard=sc,
                                          event_view=view2, tri_diffs={}, actuals=None)
    id2 = event.publish_event_review(store, p2, as_of="2026-09-26T08:00:00+00:00",
                                     supersedes_projection_id=id1)

    versions = event.event_reviews_for(store, symbol="COHR", period="Q3 FY2026")
    assert {v["projection_id"] for v in versions} == {id1, id2}   # 两条都在
    assert id2 != id1
    old = next(v for v in versions if v["projection_id"] == id1)
    assert old["payload"]["direction"] == 0                        # 早期版本未覆盖


# --- 5.9 双模式投影各自保留 ------------------------------------------------------ #

def test_dual_mode_projections_coexist(monkeypatch):
    from ats.agents.fundamental import event, routine

    store = _store()
    _seed_baseline_dossier(store)
    monkeypatch.setattr("ats.memory.get_store", lambda: store)

    baseline = routine.load_baseline(store, "COHR")
    draft = routine.build_expectation_updates(
        ["gross_margin 毛利率实际读数 25.5%"], [routine.CONFIRM], baseline)[0]
    upd_id = routine.publish_expectation_update(store, draft)
    sc = event.build_event_review_payload(
        symbol="COHR", period="Q3 FY2026", event="earnings",
        scorecard=None, event_view={"direction": 1, "magnitude": 1.6,
                                    "confidence": 0.8, "rationale": "超预期"},
        tri_diffs={}, actuals=None)
    rev_id = event.publish_event_review(store, sc)

    assert upd_id != rev_id
    updates = store.task_projection_envelopes(agent_role="fundamental_expectation_update")
    reviews = store.task_projection_envelopes(agent_role="fundamental_event_review")
    assert len(updates) == 1 and len(reviews) == 1     # 互不覆盖
    assert updates[0]["schema_name"] == "FundamentalExpectationUpdate"
    assert reviews[0]["schema_name"] == "FundamentalEventReview"


# --- 5.10 跨标的信号只取中性事实 -------------------------------------------------- #

def test_peer_report_returns_neutral_facts_only(monkeypatch):
    from ats.graph import pead
    from ats.schemas.fundamentals import (FinancialStatements, FundamentalData,
                                          StatementMetric)

    store = _store()
    # 上游同业有已打分的 dossier：评审结论/分档存在，但 _peer_report 不再读它们。
    from ats.schemas.pead import Actuals, PeadDossier, Scorecard

    store.save_dossier(PeadDossier(
        symbol="TSMC", fiscal_label="Q3 FY2026", phase="score", updated_at=NOW,
        actuals=Actuals(symbol="TSMC", fiscal_label="Q3 FY2026", as_of=NOW,
                        guidance="上调资本开支指引区间", transcript_signals=[]),
        scorecard=Scorecard(symbol="TSMC", fiscal_label="Q3 FY2026", as_of=NOW,
                            lines=[], total=2.0, threshold=1.5, band="达到做多门槛"),
        decision_summary="达成门槛→做多"))
    monkeypatch.setattr("ats.memory.get_store", lambda: store)

    fd = FundamentalData(symbol="TSMC", as_of=NOW, statements=FinancialStatements(
        period="2026-06-30", lines=[
            StatementMetric(label="Revenue", value=30000, yoy=12.0, unit="$M"),
            StatementMetric(label="Diluted EPS", value=2.10, yoy=25.0, unit="$"),
        ]))
    monkeypatch.setattr("ats.data.fundamentals.fetch", lambda *a, **k: fd)

    row = pead._peer_report("TSMC")
    assert row["reported"] is True
    assert row["peer_reported_eps"] == 2.10            # 已报实际值仍取得到
    assert row["peer_reported_revenue"] == 30000
    assert "peer_band" not in row and "peer_decision" not in row \
        and "peer_guidance" not in row                 # 评审结论/分档/自由文本被丢弃


def test_peer_line_neutral_rendering():
    from ats.agents.pead.prep import _peer_line

    row = {"symbol": "TSMC", "role": "upstream", "price_chg_pct": 3.1,
           "earnings_date": "2026-09-18", "reported": True, "peer_fiscal": "2026-06-30",
           "peer_reported_eps": 2.10, "peer_reported_eps_yoy": 25.0,
           "peer_brief_facts": ["TSMC 上调 CoWoS 产能指引区间"]}
    line = _peer_line(row)
    assert "已报 EPS" in line and "简报事实" in line
    assert "band" not in line and "结论" not in line


# --- 5.11 守卫：基本面不读其他标的的基本面投影 ------------------------------------ #

def test_guard_flags_unscoped_fundamental_projection_read(tmp_path):
    from ats.workflow.architecture_guards import scan_module

    pkg = tmp_path / "src" / "ats" / "agents" / "fundamental"
    pkg.mkdir(parents=True)
    (pkg / "evil.py").write_text(
        "def peek(store):\n"
        "    return store.task_projection_envelopes("
        'agent_role="fundamental_event_review")\n',
        encoding="utf-8")
    found = scan_module(pkg / "evil.py", root=tmp_path)
    kinds = {v.kind for v in found}
    assert "unscoped_projection_read" in kinds


def test_guard_blocks_cross_ticker_fundamental_read():
    from ats.workflow.architecture_guards import CrossScopeReadError, assert_fundamental_scope

    assert_fundamental_scope("fundamental_event_review", "COHR", own_symbol="COHR")
    with pytest.raises(CrossScopeReadError):
        assert_fundamental_scope("fundamental_event_review", "TSMC", own_symbol="COHR")
    # 非基本面家族投影不受此约束（information_brief 是被允许的输入）。
    assert_fundamental_scope("information_brief", "TSMC", own_symbol="COHR")


# --- 5.6/5.7 事件评审不触碰风控与 sizing ------------------------------------------ #

def test_event_view_has_no_sizing_and_no_risk_calls():
    from ats.agents.pead.score import event_view
    from ats.schemas.pead import Scorecard

    cfg = __import__("ats.config", fromlist=["load_pead_config"]).load_pead_config("COHR")
    sc = Scorecard(symbol="COHR", fiscal_label="Q3 FY2026", as_of=NOW,
                   lines=[], total=2.0, threshold=cfg.long_threshold, band="x")
    view = event_view(cfg, sc, run_up_vs_sector=2.0)
    assert view["direction"] == 1
    assert {"action", "qty", "qty_hint", "notional", "notional_hint", "weight"}.isdisjoint(view)
    assert 0.0 < view["confidence"] <= 1.0

    source = open("src/ats/agents/pead/score.py", encoding="utf-8").read()
    assert "PortfolioSnapshot" not in source
    assert "def decide(" not in source and "_held_qty" not in source

    graph_source = open("src/ats/graph/pead.py", encoding="utf-8").read()
    for banned in ("risk_agent", "risk_validator", "pre_trade", "review_guardrails",
                   "IBKRBroker", "TradeDecision", "PeadRecommendation"):
        assert banned not in graph_source, banned


# --- 5.8 注入开关被移除（而非保留为可开启选项）------------------------------------ #

def test_inject_prep_switch_is_gone():
    from ats.config import load_pead_global

    g = load_pead_global()
    assert "inject_prep" not in g.get("sector_review", {})
    assert "inject_prep" not in g.get("macro_review", {})
    assert "inject_monitor" not in g.get("sector_review", {})
    graph_source = open("src/ats/graph/pead.py", encoding="utf-8").read()
    # 开关不得再被读取（注释里的历史说明不算使用）。
    assert '["inject_prep"]' not in graph_source
    assert 'get("inject_prep"' not in graph_source
    assert "prep_block" not in graph_source
