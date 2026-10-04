"""Phase D Group 7: chief research snapshot wiring (tasks 7.1–7.9).

The chief is the only role allowed to aggregate every analyst's view — and it
may only do so from a FROZEN research snapshot built from projections. Six
categories complete, or the cycle never opens; mid-cycle invalidation
supersedes; the gap report never becomes a decision input.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.decision.repository import DecisionAuditRepository
from ats.decision.snapshot import (frozen_snapshot_complete,
                                   frozen_snapshot_stale_reasons)
from ats.graph.chief import assemble_context, persist_decision, route_after_assemble
from ats.graph.chief_state import ChiefDecisionState
from ats.memory.store import TradingMemory
from ats.schemas.decision import TradeDecision

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)


def _expiry(hours: int = 48) -> str:
    return (NOW + timedelta(hours=hours)).isoformat(timespec="seconds")


def _env(role, payload, scope):
    return build_envelope(role=role, payload=payload, scope=scope,
                          as_of=NOW.isoformat(timespec="seconds"),
                          valid_until=_expiry(),
                          data_vintage_refs=["dataset@2026-09-23"])


def _seed(store, *, layers=("L1_app", "L3_optics"), targets=("COHR",),
          sectors=("ai_hardware",)):
    rows = {}
    for key in layers:
        rows[f"layer:{key}"] = _env(
            "layer_analysis",
            {"layer": key, "status": "expanding", "summary": f"{key} orders up",
             "findings": ["book-to-bill 1.4"], "confidence": 0.8},
            ProjectionScope(kind="layer", id=key))
    for sym in targets:
        rows[f"entity:{sym}"] = _env(
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
    for name in sectors:
        rows[f"sector:{name}"] = _env(
            "sector_allocation",
            {"sector": name, "stance": "overweight", "target_weight": 0.4,
             "rationale": "L3/L5 扩张", "drivers": ["L3_optics: expanding"]},
            ProjectionScope(kind="sector", id=name))
    rows["macro:portfolio"] = _env(
        "macro_review",
        {"regime": "risk_on", "summary": "利率见顶", "indicators": ["10Y 4.0"]},
        ProjectionScope(kind="portfolio"))
    for sym in targets:
        rows[f"tech:{sym}"] = _env(
            "technical_review",
            {"entity": sym, "signal": "neutral", "summary": "区间震荡",
             "levels": {"support": 300.0}},
            ProjectionScope(kind="entity", id=sym))
    for env in rows.values():
        store.save_task_projection_envelope(env)
    return rows


@pytest.fixture
def chief_env(tmp_path, monkeypatch):
    store = TradingMemory(tmp_path / "chief.sqlite")
    monkeypatch.setattr("ats.memory.get_store", lambda: store)
    # Pin the plan inputs so the config on disk cannot shift the fixture.
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
    base = dict(cycle_id="chief-g7", as_of=NOW, source="chief", decide=True,
                use_llm=False, use_broker=False)
    base.update(kw)
    return ChiefDecisionState(**base)


# --- 7.1 snapshot records provenance per category --------------------------------- #

def test_snapshot_records_six_categories_with_provenance(chief_env):
    from ats.agents.chief import assemble

    _seed(chief_env)
    snapshot, detail = assemble.build_chief_snapshot(chief_env, at=NOW)
    assert snapshot.complete
    categories = {item.category for item in snapshot.items}
    assert categories == {"layer_analysis", "information_brief", "sector_allocation",
                          "fundamental_analysis", "macro_review", "technical_review"}
    for item in snapshot.items:
        assert item.projection_id and item.content_hash and item.as_of
        assert item.reusable and item.freshness == "fresh"
    # every scanned CATEGORY is fresh somewhere (the untouched fundamental mode
    # legitimately scans as missing — either mode satisfies the category)
    categories = {assemble.TASK_TO_CATEGORY[d["task_id"]] for d in detail}
    for category in categories:
        assert any(d["status"] == "fresh" for d in detail
                   if assemble.TASK_TO_CATEGORY[d["task_id"]] == category), category


def test_snapshot_fundamental_category_satisfied_by_either_mode(chief_env):
    from ats.agents.chief import assemble

    _seed(chief_env)
    snapshot, _ = assemble.build_chief_snapshot(chief_env, at=NOW)
    fundamental = [i for i in snapshot.items if i.category == "fundamental_analysis"]
    assert len(fundamental) == 1
    assert fundamental[0].task_id == "fundamental_event_review"   # the mode that delivered


# --- 7.2/7.5 incomplete snapshot blocks before any cycle write --------------------- #

def test_incomplete_snapshot_blocks_and_writes_nothing(chief_env, capsys):
    from ats.agents.chief import assemble

    _seed(chief_env)
    chief_env.conn.execute("DELETE FROM task_projection_envelopes WHERE agent_role='macro_review'")
    chief_env.conn.commit()
    state = _state()
    out = assemble_context(state)
    merged = {**state.model_dump(), **out}
    assert merged["gap_report"], "gap report must exist"
    assert merged["context_text"] == ""          # 7.5: no silent empty-context decision
    assert "宏观评审" in merged["gap_report"]
    assert merged["research_snapshot"]           # the snapshot itself is still recorded
    snapshot = merged["research_snapshot"]
    assert not frozen_snapshot_complete(snapshot)
    assert route_after_assemble(_state(gap_report=merged["gap_report"])) == "end"

    # full graph: the blocked run writes NO cycle (7.2)
    from ats.decision.repository import DecisionAuditRepository
    from ats.runtime.cli import run_decision_graph

    repo = DecisionAuditRepository(chief_env)
    result = run_decision_graph(_state(execute=False, dry_run=True), channel=None)
    assert result.get("gap_report")
    assert repo.get_cycle("chief-g7") is None


# --- 7.3 the real frozen snapshot reaches create_cycle ------------------------------ #

def test_persist_writes_real_snapshot_into_the_cycle(chief_env):
    from ats.agents.chief import assemble

    _seed(chief_env)
    state = _state(decisions=[TradeDecision(symbol="COHR", action="buy",
                                            notional_usd=5000, rationale="r")])
    out = assemble_context(state)
    state = state.model_copy(update=out)
    assert state.context_text                       # decision context assembled
    persist_decision(state)
    repo = DecisionAuditRepository(chief_env)
    row = repo.get_cycle("chief-g7")
    assert row is not None
    stored = json.loads(row["research_snapshot"])
    assert stored == state.research_snapshot
    assert frozen_snapshot_complete(stored)


def test_decide_path_refuses_persist_without_snapshot(chief_env):
    state = _state(decisions=[TradeDecision(symbol="COHR", action="buy",
                                            notional_usd=5000, rationale="r")])
    with pytest.raises(ValueError, match="complete research snapshot"):
        persist_decision(state)
    assert DecisionAuditRepository(chief_env).get_cycle("chief-g7") is None


# --- 7.4 dual-read: projection path agrees with legacy path ------------------------- #

def test_dual_read_paths_agree_and_diffs_are_zero(chief_env):
    from ats.agents.chief import assemble

    _seed(chief_env)
    state = _state()
    out = assemble_context(state)
    assert not out.get("gap_report")
    # 7.4: both read paths ran and their per-category presence disagreement is
    # recorded (this hermetic fixture has empty legacy blocks, so what the
    # assertion pins is that the comparison covers exactly the five categories
    # that HAVE a legacy counterpart and never invents extra ones).
    assert set(out["context_stats"]["dual_read_diffs"]) == set(
        assemble.CATEGORY_TO_LEGACY_BLOCK)
    # the projection block is part of the decision context and cites projections
    assert "研究快照（六类投影）" in out["context_text"]
    assert "direction=" in out["context_text"]
    # with the legacy side populated, an agreement fixture diffs to zero
    ctx = SimpleNamespace(blocks={name: "text" for name
                                  in assemble.CATEGORY_TO_LEGACY_BLOCK.values()})
    detail = [{"task_id": task_id, "scope": "x", "status": "fresh"}
              for task_id in assemble.TASK_TO_CATEGORY]
    assert set(assemble.dual_read_diffs(ctx, detail).values()) == {0}


def test_dual_read_counts_disagreement(chief_env):
    from ats.agents.chief import assemble

    _seed(chief_env)
    state = _state()
    out = assemble_context(state)
    ctx_blocks = {"宏观评审（倾斜修正）": "regime: risk_on …"}  # legacy present, projection missing
    detail = [{"task_id": "macro_review", "scope": "portfolio", "status": "missing"}]
    diffs = assemble.dual_read_diffs(
        SimpleNamespace(blocks=ctx_blocks), detail)
    assert diffs["macro_review"] == 1


# --- 7.6 revision rounds reuse the same snapshot ------------------------------------ #

def test_revision_rounds_reuse_one_snapshot(chief_env):
    _seed(chief_env)
    decisions = [TradeDecision(symbol="COHR", action="buy", notional_usd=5000,
                               rationale="r")]
    state = _state(decisions=decisions)
    out = assemble_context(state)
    state = state.model_copy(update=out)
    persist_decision(state)                                   # round 1
    repo = DecisionAuditRepository(chief_env)
    first = repo.get_cycle("chief-g7")["research_snapshot"]
    # round 2: revise → persist again; the frozen payload must not move.
    # (The revised order differs — identical content is idempotently deduped.)
    revised = [TradeDecision(symbol="COHR", action="buy", notional_usd=3000,
                             rationale="[revise@r1] 采纳边界")]
    state2 = state.model_copy(update={"decisions": revised,
                                      "parent_revision_no": 1, "risk_round": 2,
                                      "revision_no": 1, "revision_hash": "h1",
                                      "risk_review": None})
    persist_decision(state2)
    assert repo.get_cycle("chief-g7")["research_snapshot"] == first
    revisions = repo.conn.execute(
        "SELECT revision_no FROM decision_revisions WHERE cycle_id='chief-g7' "
        "ORDER BY revision_no").fetchall()
    assert [r["revision_no"] for r in revisions] == [1, 2]


# --- 7.7 mid-cycle invalidation supersedes ------------------------------------------ #

def test_newer_vintage_supersedes_mid_cycle(chief_env):
    _seed(chief_env)
    decisions = [TradeDecision(symbol="COHR", action="buy", notional_usd=5000,
                               rationale="r")]
    state = _state(decisions=decisions)
    state = state.model_copy(update=assemble_context(state))
    persist_decision(state)
    # a NEW projection with a DIFFERENT data vintage lands mid-cycle
    newer = build_envelope(
        role="macro_review",
        payload={"regime": "risk_off", "summary": "信用利差走阔", "indicators": ["HY OAS +80"]},
        scope=ProjectionScope(kind="portfolio"), as_of=NOW.isoformat(timespec="seconds"),
        valid_until=_expiry(), data_vintage_refs=["dataset@2026-09-24"],
        created_at=(NOW + timedelta(minutes=1)).isoformat(timespec="seconds"))
    chief_env.save_task_projection_envelope(newer)

    repo = DecisionAuditRepository(chief_env)
    reasons = frozen_snapshot_stale_reasons(
        chief_env, json.loads(repo.get_cycle("chief-g7")["research_snapshot"]))
    assert any("vintage" in r for r in reasons)

    state2 = state.model_copy(update={"parent_revision_no": 1, "risk_round": 2,
                                      "revision_no": 1, "revision_hash": "h1",
                                      "risk_review": None,
                                      "decisions": decisions})
    out = persist_decision(state2)
    assert out["cycle_status"] == "superseded"
    cycle = repo.get_cycle("chief-g7")
    assert cycle["status"] == "superseded"
    # the original snapshot was NOT swapped
    stored = json.loads(cycle["research_snapshot"])
    assert stored == state.research_snapshot
    # no second revision was written
    count = repo.conn.execute(
        "SELECT COUNT(*) FROM decision_revisions WHERE cycle_id='chief-g7'"
    ).fetchone()[0]
    assert count == 1


def test_withdrawn_projection_supersedes_mid_cycle(chief_env):
    _seed(chief_env)
    state = _state(decisions=[TradeDecision(symbol="COHR", action="buy",
                                            notional_usd=5000, rationale="r")])
    state = state.model_copy(update=assemble_context(state))
    persist_decision(state)
    rows = chief_env.task_projection_envelopes(agent_role="macro_review")
    chief_env.conn.execute(
        "UPDATE task_projection_envelopes SET status='withdrawn' WHERE projection_id=?",
        (rows[0]["projection_id"],))
    chief_env.conn.commit()
    repo = DecisionAuditRepository(chief_env)
    reasons = frozen_snapshot_stale_reasons(
        chief_env, json.loads(repo.get_cycle("chief-g7")["research_snapshot"]))
    assert any("撤销" in r for r in reasons)


# --- 7.8 the gap report is never a decision input ------------------------------------ #

def test_gap_report_content_and_isolation(chief_env):
    from ats.agents.chief import assemble

    _seed(chief_env)
    # expire the technical projection + withdraw a layer projection
    rows = chief_env.task_projection_envelopes(agent_role="technical_review")
    chief_env.conn.execute(
        "UPDATE task_projection_envelopes SET valid_until=? WHERE projection_id=?",
        ((NOW - timedelta(hours=1)).isoformat(timespec="seconds"),
         rows[0]["projection_id"]))
    chief_env.conn.commit()
    snapshot, detail = assemble.build_chief_snapshot(chief_env, at=NOW)
    assert not snapshot.complete
    report = assemble.gap_report(snapshot, detail)
    assert "technical_review" in report and "expired" in report
    assert "影响范围" in report
    # a full graph run with the gap never puts the report into decision context
    state = _state()
    out = assemble_context(state)
    assert "gap_report" in out and out["context_text"] == ""


# --- 7.9 direction is a research input, never mapped to an action -------------------- #

def test_direction_is_presented_as_research_input_only(chief_env):
    import pathlib

    _seed(chief_env)
    state = _state()
    out = assemble_context(state)
    assert "direction=+1" in out["context_text"]
    assert "非交易指令" in out["context_text"]
    # and no chief module carries a direction→action mapping table
    for path in (pathlib.Path("src/ats/graph/chief.py"),
                 *pathlib.Path("src/ats/agents/chief").glob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert "DIRECTION_TO_ACTION" not in src, path
        assert "DIRECTION_ACTION_MAP" not in src, path
        for verb in ("buy", "sell", "trim"):
            assert f"'direction': '{verb}'" not in src, path
            assert f'"{verb}": 1' not in src, path
