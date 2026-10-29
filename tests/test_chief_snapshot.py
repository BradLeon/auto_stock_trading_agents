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
from ats.decision.snapshot import frozen_snapshot_complete, frozen_snapshot_stale_reasons
from ats.graph.chief import assemble_context, persist_decision, route_after_assemble
from ats.graph.chief_state import ChiefDecisionState
from ats.memory.store import TradingMemory
from ats.schemas.decision import TradeDecision

pytestmark = pytest.mark.usefixtures("open_decision_read_gate")


NOW = datetime.now(timezone.utc) - timedelta(seconds=2)


def _expiry(hours: int = 48) -> str:
    return (NOW + timedelta(hours=hours)).isoformat(timespec="seconds")


def _env(role, payload, scope):
    return build_envelope(role=role, payload=payload, scope=scope,
                          as_of=NOW.isoformat(timespec="seconds"),
                          valid_until=_expiry(),
                          data_vintage_refs=["dataset@2026-09-23"])


def _seed(store, *, layers=("L1_app", "L3_optics"), targets=("COHR",),
          sectors=("ai_hardware",), fundamental="event", fund_run=("", "")):
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
        if fundamental == "routine":
            rows[f"fund:{sym}"] = build_envelope(
                role="fundamental_expectation_update",
                payload={"entity": sym, "metric": "gross_margin", "period": "Q3 FY2026",
                         "previous_value": 0.25, "new_value": 0.26, "driver": "例行读数"},
                scope=ProjectionScope(kind="entity", id=sym),
                as_of=NOW.isoformat(timespec="seconds"), valid_until=_expiry(),
                data_vintage_refs=["dataset@test"],
                workflow_run_id=fund_run[0], agent_run_id=fund_run[1])
        else:
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
    from phase_d_frozen_harness import producer_inputs

    from ats.workflow import decision_requirements as dr
    from ats.workflow.planning import TASK_ROLE
    plan = dr.restore(dr.standalone(_state(fundamental_mode=fundamental)))
    by_binding = {(e.agent_role, e.scope.key): e for e in rows.values()}
    selected = {}
    for task in plan.tasks:
        env = by_binding.get((TASK_ROLE[task.task_id], task.scope.key))
        if env is None:
            continue
        inputs = producer_inputs(plan, task, {k: selected[k] for k in task.dependencies})
        env = build_envelope(role=env.agent_role, payload=env.payload, scope=env.scope,
            as_of=env.as_of, valid_until=env.valid_until, input_refs=inputs,
            data_vintage_refs=env.data_vintage_refs,
            workflow_run_id=env.workflow_run_id, agent_run_id=env.agent_run_id)
        by_binding[(env.agent_role, env.scope.key)] = env
        selected[task.instance_key] = env
    for key, env in list(rows.items()):
        rows[key] = by_binding[(env.agent_role, env.scope.key)]
        store.save_task_projection_envelope(rows[key])
    return rows


@pytest.fixture
def chief_env(tmp_path, monkeypatch):
    store = TradingMemory(tmp_path / "chief.sqlite")
    monkeypatch.setattr("ats.memory.get_store", lambda: store)
    # Freeze the same real profile/configuration that the decision gate reads.
    import shutil

    import yaml

    from ats.config import REPO_ROOT, reset_config_cache
    root = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", root)
    path = root / "pead.yaml"
    cfg = yaml.safe_load(path.read_text()); cfg["targets"] = ["COHR"]
    path.write_text(yaml.safe_dump(cfg))
    path = root / "sectors/ai_hardware.yaml"
    cfg = yaml.safe_load(path.read_text()); cfg["layers"] = cfg["layers"][:2]
    for layer, key in zip(cfg["layers"], ["L1_app", "L3_optics"]):
        layer["key"] = key
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setenv("ATS_CONFIG_DIR", str(root)); reset_config_cache()
    monkeypatch.setattr("ats.graph.chief._now", lambda: NOW)
    # Pin the compatibility readers as well.
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
    base = dict(cycle_id="chief-g7", as_of=NOW, source="chief", decide=True, fundamental_mode="event",
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


# --- 7.4 the context is rendered from the projections only ------------------------- #

def test_context_is_rendered_from_the_projection_block_alone(chief_env):
    """With the legacy direct reads gone there is one read path: every frozen item is in the context
    and the old comparison (`dual_read_diffs`) no longer exists."""
    from ats.agents.chief import assemble

    _seed(chief_env)
    state = _state()
    out = assemble_context(state)
    assert not out.get("gap_report")
    assert "dual_read_diffs" not in out["context_stats"]
    assert not hasattr(assemble, "dual_read_diffs")
    text = out["context_text"]
    assert "研究快照（六类投影）" in text and "direction=" in text
    for item in out["research_snapshot"]["items"]:
        assert item["projection_id"] in text and item["content_hash"] in text


def test_a_category_with_no_fresh_projection_is_listed_as_missing_not_omitted():
    from ats.agents.chief import assemble

    detail = [{"task_id": "macro_review", "role": "macro_review", "scope": "portfolio:portfolio",
               "status": "missing", "reason": "missing", "as_of": "", "projection_id": "",
               "content_hash": "", "payload": None}]
    assert "宏观评审: **缺失**" in assemble.projection_context_block(detail)


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
        created_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="seconds"))
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


# --- per-task config lineage (switch-trade-path-and-retire-r5, 3.2 finding) ---------- #
# Production research runs one workflow per task family, so a projection records only the
# configuration its own task reads. The decision gate must accept that shape and still
# refuse a projection whose OWN configuration changed since it was produced.

def test_projections_from_single_workflow_runs_pass_the_decision_gate(chief_env):
    rows = _seed(chief_env)
    tech = rows["tech:COHR"]
    configs = {ref.split("@")[0] for ref in tech.input_refs if ref.startswith("config:")}
    assert configs == {"config:config/pead.yaml", "config:config/technical.yaml"}
    out = assemble_context(_state())
    assert out["context_text"] and not out.get("gap_report")


def test_a_change_to_a_tasks_own_config_still_refuses_its_projection(chief_env, tmp_path):
    from ats.workflow.cutover_routing import RouteUnavailable

    _seed(chief_env)
    path = tmp_path / "config" / "technical.yaml"
    path.write_text(path.read_text() + "\n# changed after the projection was produced\n")
    with pytest.raises(RouteUnavailable, match="lineage differs: .*technical-review"):
        assemble_context(_state())


def test_a_change_to_another_tasks_config_does_not_refuse_technical(chief_env, tmp_path):
    from ats.workflow.cutover_routing import RouteUnavailable

    _seed(chief_env)
    path = tmp_path / "config" / "macro.yaml"
    path.write_text(path.read_text() + "\n# changed after the projections were produced\n")
    with pytest.raises(RouteUnavailable) as refused:
        assemble_context(_state())
    # Only Macro, whose own config changed, is refused.
    assert "macro-review" in str(refused.value) and "technical-review" not in str(refused.value)


def _routine_attempt(store, *, key, projection_id):
    store.conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_runs (agent_run_id TEXT PRIMARY KEY, run_id TEXT, "
        "task_instance_key TEXT, task_id TEXT, scope_json TEXT, status TEXT, projection_refs_json TEXT)")
    store.conn.execute(
        "INSERT INTO agent_runs VALUES ('agent-run-1','wf-routine',?, 'fundamental-routine',?,"
        "'succeeded',?)", (key, json.dumps(ProjectionScope(kind="entity", id="COHR").model_dump(mode="json")),
                           json.dumps([projection_id])))
    store.conn.commit()


def _routine_key(scope_key):
    from ats.workflow import decision_requirements as dr

    plan = dr.restore(dr.standalone(_state(fundamental_mode="routine")))
    task = next(t for t in plan.tasks if t.task_id == "fundamental-routine" and t.scope.key == scope_key)
    # The producing Routine workflow froze a different config set, so its key ends differently.
    return task.instance_key.rpartition(":")[0] + ":cad636aba1cc"


def test_a_routine_attempt_from_its_own_workflow_is_accepted(chief_env):
    rows = _seed(chief_env, fundamental="routine", fund_run=("wf-routine", "agent-run-1"))
    _routine_attempt(chief_env, key=_routine_key("entity:COHR"),
                     projection_id=rows["fund:COHR"].projection_id)
    out = assemble_context(_state(fundamental_mode="routine"))
    assert out["context_text"] and not out.get("gap_report")


def test_a_routine_attempt_for_another_scope_is_still_refused(chief_env):
    from ats.workflow.cutover_routing import RouteUnavailable

    rows = _seed(chief_env, fundamental="routine", fund_run=("wf-routine", "agent-run-1"))
    key = _routine_key("entity:COHR").replace("entity:COHR", "entity:NVDA")
    _routine_attempt(chief_env, key=key, projection_id=rows["fund:COHR"].projection_id)
    with pytest.raises(RouteUnavailable, match="attempt not terminal or mismatched"):
        assemble_context(_state(fundamental_mode="routine"))
