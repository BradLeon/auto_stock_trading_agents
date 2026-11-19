"""2026-11-19 (user ruling B): one name without a fresh Technical reading no longer stops the whole Chief cycle.

11-13 and 11-17 GOOG's 08:30 price download failed, its reading was 25 hours old at 09:45, and chief-daily stopped
at `assemble_context`. Now the name is left out of the plan's Technical tasks, stated in the snapshot and the
Chief's context, and the risk review rejects every order on it this cycle. Every name missing still blocks.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.graph.chief import assemble_context
from ats.risk import checks as risk_checks
from ats.schemas.decision import TradeDecision
from ats.workflow import decision_requirements as dr
from ats.workflow.planning import TASK_ROLE
from ats.workflow.read_scope import ReadUnavailable
from ats.workflow.run_contracts import ContractError
from phase_d_frozen_harness import producer_inputs
from test_decision_risk_review import _buy, _pf, _pos, demo  # noqa: F401
from test_fundamental_per_target import (NOW, TARGETS, _others, _plan, _routine, _state,  # noqa: F401
                                         universe)
from ats.memory.store import TradingMemory

pytestmark = pytest.mark.usefixtures("open_decision_read_gate")


def _with_technical_age(rows, ages):
    out = []
    for env in rows:
        if env.agent_role == "technical_review" and env.scope.id in ages:
            env = build_envelope(role=env.agent_role, payload=env.payload, scope=env.scope,
                                 as_of=(NOW - ages[env.scope.id]).isoformat(timespec="seconds"),
                                 valid_until=env.valid_until, data_vintage_refs=env.data_vintage_refs)
        out.append(env)
    return out


def _publish(tmp_path, store, technical_ages):
    rows = _with_technical_age(_others(), technical_ages) + [_routine(sym) for sym in TARGETS]
    probe = TradingMemory(tmp_path / f"probe-{len(list(tmp_path.iterdir()))}.sqlite")
    for env in rows:
        probe.save_task_projection_envelope(env)
    plan = dr.restore(dr.standalone(_state(), probe))
    by_binding = {(e.agent_role, e.scope.key): e for e in rows}
    selected = {}
    for task in plan.tasks:
        env = by_binding.get((TASK_ROLE[task.task_id], task.scope.key))
        if env is None:
            continue
        env = build_envelope(role=env.agent_role, payload=env.payload, scope=env.scope,
            as_of=env.as_of, valid_until=env.valid_until,
            input_refs=producer_inputs(plan, task, {k: selected[k] for k in task.dependencies}),
            data_vintage_refs=env.data_vintage_refs)
        by_binding[(env.agent_role, env.scope.key)] = env
        selected[task.instance_key] = env
    for env in by_binding.values():
        store.save_task_projection_envelope(env)
    return plan


def test_a_stale_technical_reading_is_stated_and_does_not_block(universe):
    tmp_path, store = universe
    plan = _publish(tmp_path, store, {"LITE": timedelta(hours=25)})
    assert plan.task_inputs["technical_uncovered"] == ["LITE"]
    assert [t.scope.id for t in plan.tasks if t.task_id == "technical-review"] == ["COHR"]

    out = assemble_context(_state())
    assert not out.get("gap_report"), out.get("gap_report")
    assert "技术面缺失" in out["context_text"] and "LITE" in out["context_text"]
    frozen = json.loads(json.dumps(out["research_snapshot"]["requirements"]))
    assert dr.uncovered_technical(frozen) == {"LITE"}


def test_every_technical_reading_stale_still_blocks(universe):
    tmp_path, store = universe
    _publish(tmp_path, store, {sym: timedelta(hours=25) for sym in TARGETS})
    with pytest.raises(ReadUnavailable, match="technical-review:entity:.*freshness_unconfirmed"):
        assemble_context(_state())


def test_fresh_readings_change_nothing(universe):
    tmp_path, store = universe
    plan = _publish(tmp_path, store, {})
    assert "technical_uncovered" not in plan.task_inputs
    assert {t.scope.id for t in plan.tasks if t.task_id == "technical-review"} == set(TARGETS)


@pytest.mark.parametrize("uncovered", [["NVDA"], list(TARGETS)])
def test_a_plan_refuses_an_unknown_or_total_technical_gap(tmp_path, monkeypatch, uncovered):
    with pytest.raises(ContractError):
        _plan(tmp_path, monkeypatch, technical_uncovered=uncovered)


def test_no_order_in_either_direction_on_a_name_without_technical(demo):

    pf = _pf([_pos("MU", 0.10), _pos("TSM", 0.05)])
    orders = [_buy("MU", 20_000), TradeDecision(symbol="MU", action="trim", notional_usd=30_000),
              _buy("TSM", 20_000)]
    result = risk_checks.review_revision(orders, pf, sector="demo", uncovered_technical={"MU"})
    verdicts = {(v.symbol, v.action): v for v in result.order_verdicts}
    for blocked in (("MU", "buy"), ("MU", "trim")):
        assert verdicts[blocked].verdict == "rejected"
        assert "technical_uncovered" in " ".join(verdicts[blocked].reasons)
    assert "technical_uncovered" not in " ".join(verdicts[("TSM", "buy")].reasons)
    assert any(v.rule_id == "technical_uncovered" and v.entity == "MU" for v in result.violations)
