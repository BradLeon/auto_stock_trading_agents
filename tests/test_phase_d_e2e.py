"""Phase D tasks 9.4/9.5 — end-to-end acceptance over the full funnel.

正向（9.4）：六类投影齐备 → 快照门放行 → 主理人（decide 路径）→ 风控 → Boss →
Trader，全程走真实 funnel（chief decision graph + hermetic broker），并断言
每笔成交订单的标的都能在落库快照里找到出处。

反向（9.5）：缺一类分析 → 运行判为不完整、缺口报告产出、不进决策周期、不下单
（broker 零成交、审计表零周期）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.decision.repository import DecisionAuditRepository
from ats.decision.snapshot import frozen_snapshot_complete
from ats.graph.chief_state import ChiefDecisionState
from ats.memory.store import TradingMemory
from ats.runtime.cli import run_decision_graph
from ats.schemas.decision import BossApproval

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)


def _expiry(hours: int = 48) -> str:
    return (NOW + timedelta(hours=hours)).isoformat(timespec="seconds")


def _env(role, payload, scope):
    return build_envelope(role=role, payload=payload, scope=scope,
                          as_of=NOW.isoformat(timespec="seconds"),
                          valid_until=_expiry(),
                          data_vintage_refs=["dataset@2026-09-23"])


def _seed(store, *, targets=("COHR",)):
    """Six categories complete: layer ×2, brief/event/technical per target,
    sector, macro (portfolio)."""
    rows = {}
    for key in ("L1_app", "L3_optics"):
        rows[f"layer:{key}"] = _env(
            "layer_analysis",
            {"layer": key, "status": "expanding", "summary": f"{key} orders up",
             "findings": ["book-to-bill 1.4"], "confidence": 0.8},
            ProjectionScope(kind="layer", id=key))
    for sym in targets:
        rows[f"brief:{sym}"] = _env(
            "information_brief",
            {"entity": sym, "headline": "上游订单走强", "summary": "指引上修",
             "relevance": "high", "sources": ["rss:test"],
             "fact_changes": ["上游订单 +12%"], "impact_candidates": ["demand"],
             "entities": [sym], "confidence": 0.7, "freshness": "today",
             "unverified": ["节奏待核验"]},
            ProjectionScope(kind="entity", id=sym))
        rows[f"fund:{sym}"] = _env(
            "fundamental_event_review",
            {"entity": sym, "event": "earnings", "period": "Q3 FY2026",
             "direction": 1, "magnitude": 0.05,
             "falsifiable_conditions": ["下季指引低于区间即证伪"], "confidence": 0.75},
            ProjectionScope(kind="entity", id=sym))
        rows[f"tech:{sym}"] = _env(
            "technical_review",
            {"entity": sym, "signal": "neutral", "summary": "区间震荡",
             "levels": {"support": 300.0}},
            ProjectionScope(kind="entity", id=sym))
    rows["sector:ai_hardware"] = _env(
        "sector_allocation",
        {"sector": "ai_hardware", "stance": "overweight", "target_weight": 0.4,
         "rationale": "L3/L5 扩张", "drivers": ["L3_optics: expanding"]},
        ProjectionScope(kind="sector", id="ai_hardware"))
    rows["macro:portfolio"] = _env(
        "macro_review",
        {"regime": "risk_on", "summary": "利率见顶", "indicators": ["10Y 4.0"]},
        ProjectionScope(kind="portfolio"))
    for env in rows.values():
        store.save_task_projection_envelope(env)
    return rows


@pytest.fixture
def e2e_env(tmp_path, monkeypatch):
    """Hermetic store + pinned plan inputs (same pins as the Group 7 fixture)."""
    store = TradingMemory(tmp_path / "phase-d-e2e.sqlite")
    monkeypatch.setattr("ats.memory.get_store", lambda: store)
    monkeypatch.setattr("ats.config.load_pead_global", lambda: {
        "targets": ["COHR"], "observe": [], "monitor": {},
        "sector_review": {"sectors": ["ai_hardware"]},
        "macro_review": {"name": "macro"}})
    monkeypatch.setattr(
        "ats.config.load_sector_config",
        lambda name="ai_hardware": SimpleNamespace(layers=[
            SimpleNamespace(key="L1_app"), SimpleNamespace(key="L3_optics")]))
    return store


def _state(**kw):
    base = dict(cycle_id="phase-d-e2e", as_of=NOW, source="chief", decide=True,
                use_llm=True, use_broker=True, dry_run=False, execute=True)
    base.update(kw)
    return ChiefDecisionState(**base)


def _chief_llm(monkeypatch, symbol: str = "COHR", notional: float = 10_000.0):
    """Deterministic chief synthesis: one actionable decision citing the snapshot."""
    from ats.agents.chief.decide import ChiefResult
    from ats.schemas.decision import TradeDecision

    def fake_from_context(context_text, *, cycle_id, as_of, use_llm=True):
        return ChiefResult(
            cycle_id=cycle_id, as_of=as_of, summary="六类投影齐备，买入 COHR",
            decisions=[TradeDecision(symbol=symbol, action="buy",
                                     notional_usd=notional, conviction=0.8,
                                     rationale="上游简报 + 事件评审 direction=+1")],
            context_text=context_text)

    monkeypatch.setattr("ats.agents.chief.decide.from_context", fake_from_context)


def _cap(monkeypatch, value: float):
    from ats.config import get_config

    rc = get_config().app.risk
    original = rc.max_single_order_usd
    rc.max_single_order_usd = value
    return original


# --- 9.4 正向：六类齐备，全链路走通且订单可追溯到快照条目 ------------------------ #

def test_complete_snapshot_runs_the_full_funnel_and_orders_trace_to_snapshot(
        e2e_env, broker, approve_all, monkeypatch):
    _seed(e2e_env)
    _chief_llm(monkeypatch)
    original = _cap(monkeypatch, 25_000.0)
    try:
        result = run_decision_graph(_state(), channel=approve_all)
    finally:
        from ats.config import get_config

        get_config().app.risk.max_single_order_usd = original

    # Trader executed
    assert [o.status for o in result["order_results"]] == ["filled"]
    assert broker.placed, "the order must reach the broker"

    # The cycle carries the real frozen snapshot, complete
    repo = DecisionAuditRepository(e2e_env)
    chain = repo.read_chain("phase-d-e2e")
    assert chain["cycle"]["status"] == "executed"
    snapshot = json.loads(chain["cycle"]["research_snapshot"])
    assert frozen_snapshot_complete(snapshot)
    assert snapshot["items"], "snapshot items must be persisted, not just a flag"

    # Traceability: every executed order's symbol is covered by an entity-scoped
    # snapshot entry (brief / fundamental / technical) — the order cites inputs
    # that were in the frozen snapshot, never something assembled after the fact.
    for order in result["order_results"]:
        symbol = order.symbol.upper()
        covering = [item for item in snapshot["items"]
                    if item.get("scope_kind") == "entity"
                    and str(item.get("scope_id", "")).upper() == symbol]
        assert covering, f"order {symbol} has no entity-scoped snapshot entry"
        roles = {item["agent_role"] for item in covering}
        assert {"information_brief", "fundamental_event_review",
                "technical_review"} <= roles
    # risk + approval + execution chain are all present in the audit trail
    assert [rv["verdict"] for rv in chain["reviews"]] == ["approved"]
    assert chain["approvals"] and chain["approvals"][0]["decision"] == "approved"


def test_every_snapshot_category_is_cited_in_the_decision_context(e2e_env):
    """The context the chief decided from lists all six categories from the
    projection read path (§7.2 contract, dual-read projection block)."""
    from ats.graph.chief import assemble_context

    _seed(e2e_env)
    state = _state()
    out = assemble_context(state)
    merged = {**state.model_dump(), **out}
    assert merged["context_text"]
    block = merged["context_text"].split("研究快照（六类投影）")[-1]
    for label in ("层级分析", "信息简报", "行业配置", "基本面", "宏观评审", "技术面评审"):
        assert label in block, label
    assert "direction=+1" in block and "非交易指令" in block


# --- 9.5 反向：缺一类分析 → 不完整、缺口报告、不进周期、不下单 ------------------- #

def test_missing_category_blocks_the_funnel_before_any_write(
        e2e_env, broker, approve_all, monkeypatch):
    _seed(e2e_env)
    _chief_llm(monkeypatch)
    # 缺技术面：快照门必须在任何写之前拦下整个周期
    e2e_env.conn.execute(
        "DELETE FROM task_projection_envelopes WHERE agent_role='technical_review'")
    e2e_env.conn.commit()

    result = run_decision_graph(_state(), channel=approve_all)

    # 运行判为不完整：缺口报告产出
    report = result.get("gap_report") or ""
    assert report, "the gap report must be produced"
    assert "技术面" in report
    # 不进入决策周期：审计表零周期、零修订
    repo = DecisionAuditRepository(e2e_env)
    assert repo.get_cycle("phase-d-e2e") is None
    revisions = repo.conn.execute(
        "SELECT COUNT(*) FROM decision_revisions WHERE cycle_id='phase-d-e2e'"
    ).fetchone()[0]
    assert revisions == 0
    # 不下单：broker 从未收到任何订单
    assert broker.placed == []
    # Boss 从未被请求审批（approval 链路在周期之前就被阻断）
    assert approve_all.requests == []
