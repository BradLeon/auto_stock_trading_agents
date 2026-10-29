"""tasks 2.7 — the trade path's two scheduled workflows (design D5).

`clerk-daily` and `chief-daily` start switched off: nothing registers, nothing is expected, nothing is
skipped-and-logged. Switched on, they run on NYSE sessions only, under a claim that a restart cannot run twice.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from test_phase_d_e2e import _an_active_trade_route, _chief_llm, _seed, _state, e2e_env  # noqa: F401

from ats.config import REPO_ROOT
from ats.decision.repository import DecisionAuditRepository
from ats.runtime import dispatcher_daemon
from ats.workflow import consumer_cutover as cc
from ats.workflow import cutover_wiring as cw
from ats.workflow import schedule_runtime as rt
from ats.workflow import trade_schedules as ts
from ats.workflow.isolation import isolated_run
from ats.workflow.run_contracts import TriggerContext

ET = ZoneInfo("America/New_York")
OK = {"status": "ok", "problems": [], "warnings": []}
SCOPE = {"kind": "portfolio", "id": "portfolio"}


def _tick(workflow, when):
    return TriggerContext(kind="schedule", workflow_id=workflow, schedule_id=workflow, scheduled_for=when.isoformat())


@pytest.fixture
def world(tmp_path):
    """An isolated schedule authority and route store; the two trade routes are on target and healthy."""
    with isolated_run("trade-schedules", root=tmp_path / "iso") as environment:
        path = environment.path_for("ATS_CUTOVER_DB")
        cw.bootstrap_wired(path=path)
        for consumer in ("clerk", "chief"):
            cc.switch(consumer, actor="op", approval="chat", stop_conditions="health fails", health=OK, path=path,
                      observe_until=(datetime.now(UTC) + timedelta(hours=72)).isoformat())
        yield path


def _resume(workflow):
    return rt.owner_switch(workflow, to_owner="dispatcher", actor="op", reason="drill",
                           approval="user approved", health_check=lambda consumer: OK)


@pytest.fixture
def config(tmp_path):
    """A copy of the real config with both trade workflows switched on."""
    root = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", root)
    path = root / "workflow" / "trade_schedules.yaml"
    body = yaml.safe_load(path.read_text())
    for entry in body["workflows"].values():
        entry["enabled"] = True
    path.write_text(yaml.safe_dump(body))
    return root


# --- switched off: the production schedule does not change ------------------------------------------ #
def test_both_workflows_ship_switched_off_with_the_agreed_times():
    schedules = ts.load_trade_schedules()
    assert {k: v["enabled"] for k, v in schedules.items()} == {"clerk-daily": False, "chief-daily": False}
    assert schedules["chief-daily"]["cron"] == {"day_of_week": "mon-fri", "hour": 10, "minute": 30,
                                               "timezone": "America/New_York"}
    assert schedules["clerk-daily"]["cron"] == {"day_of_week": "mon-fri", "hour": 16, "minute": 30,
                                               "timezone": "America/New_York"}


def test_switched_off_means_not_registered_and_not_expected(world):
    assert ts.enabled_entries() == {} and ts.trade_schedule_entries() == {}
    expected = rt.expected_triggers(datetime(2026, 11, 2, 0, tzinfo=UTC), datetime(2026, 11, 6, 23, tzinfo=UTC))
    assert not {row["workflow"] for row in expected["expected"]} & {"clerk-daily", "chief-daily"}
    assert not [w for w in expected["wake_expected"] if w["job_id"].endswith(("clerk-daily", "chief-daily"))]


def test_switched_on_but_paused_is_still_not_registered(world, config):
    assert set(ts.enabled_entries(config)) == {"clerk-daily", "chief-daily"}
    assert ts.trade_schedule_entries(config) == {}                 # no owner row yet: never resumed


# --- switched on ---------------------------------------------------------------------------------------- #
def test_the_oracle_expects_trading_days_only(world, config):
    """Thanksgiving 2026-11-26 has no session: no tick is expected for either workflow."""
    start, end = datetime(2026, 11, 25, 0, tzinfo=ET), datetime(2026, 11, 27, 23, tzinfo=ET)
    expected = rt.expected_triggers(start, end, config_dir=config)
    planned = {w["planned"] for w in expected["wake_expected"] if w["job_id"] == "phase_e:chief-daily"}
    days = {datetime.fromisoformat(p).astimezone(ET).date().isoformat() for p in planned}
    assert days == {"2026-11-25", "2026-11-27"}
    clerk_days = {datetime.fromisoformat(w["planned"]).astimezone(ET).date().isoformat()
                  for w in expected["wake_expected"] if w["job_id"] == "phase_e:clerk-daily"}
    assert clerk_days == {"2026-11-25", "2026-11-27"}
    assert {"clerk-daily", "chief-daily"} <= {row["workflow"] for row in expected["expected"]}


def test_a_closed_market_runs_nothing(world, monkeypatch):
    calls = []
    monkeypatch.setitem(ts._RUNNERS, "chief-daily", lambda entry: calls.append("chief") or {})
    thanksgiving = datetime(2026, 11, 26, 10, 30, tzinfo=ET)
    out = ts.run_trade_workflow("chief-daily", {"scope": ts.ProjectionScope(kind="portfolio", id="portfolio")},
                                _tick("chief-daily", thanksgiving))
    assert out == {"status": "skipped", "reason": "market_closed"} and calls == []


def test_a_replayed_tick_runs_once_and_returns_the_first_result(world, monkeypatch):
    _resume("chief-daily")
    calls = []
    monkeypatch.setitem(ts._RUNNERS, "chief-daily",
                        lambda entry: calls.append(1) or {"cycle_id": "chief-x", "outcome": "pending_approval"})
    entry = {"scope": ts.ProjectionScope(kind="portfolio", id="portfolio")}
    tick = _tick("chief-daily", datetime(2026, 11, 25, 10, 30, tzinfo=ET))
    first = ts.run_trade_workflow("chief-daily", entry, tick)
    again = ts.run_trade_workflow("chief-daily", entry, tick)            # a restart replaying the same planned instant
    assert calls == [1] and first["outcome"] == "pending_approval"
    assert again.get("schedule_skipped") and again["result"]["cycle_id"] == "chief-x"


def test_a_chief_failure_leaves_the_clerks_tick_alone(world, monkeypatch):
    _resume("chief-daily")
    _resume("clerk-daily")

    def boom(entry):
        raise RuntimeError("model down")

    monkeypatch.setitem(ts._RUNNERS, "chief-daily", boom)
    monkeypatch.setitem(ts._RUNNERS, "clerk-daily", lambda entry: {"status": "complete", "run_id": "r1"})
    entry = {"scope": ts.ProjectionScope(kind="portfolio", id="portfolio")}
    with pytest.raises(RuntimeError):
        ts.run_trade_workflow("chief-daily", entry, _tick("chief-daily", datetime(2026, 11, 25, 10, 30, tzinfo=ET)))
    out = ts.run_trade_workflow("clerk-daily", entry, _tick("clerk-daily", datetime(2026, 11, 25, 16, 30, tzinfo=ET)))
    assert out == {"status": "complete", "run_id": "r1"}


# --- owner: the paused / resumed words ------------------------------------------------------------------- #
def test_resume_needs_the_approval_text_and_the_consumers_target_route(world):
    with pytest.raises(rt.ScheduleAuthorityError, match="approval"):
        rt.owner_switch("chief-daily", to_owner="dispatcher", actor="op", reason="r", approval="")
    entry = _resume("chief-daily")
    assert entry["to_owner"] == "dispatcher" and entry["health"]["consumer"] == "chief"
    assert rt.current_owner("chief-daily", SCOPE) == "dispatcher"
    paused = rt.owner_switch("chief-daily", to_owner="legacy", actor="op", reason="pause")
    assert paused["action"] == "rollback" and rt.current_owner("chief-daily", SCOPE) == "legacy"


def test_resume_is_refused_while_the_consumer_is_not_on_the_target_route(tmp_path):
    with isolated_run("trade-schedules-off-route", root=tmp_path / "iso") as environment:
        path = environment.path_for("ATS_CUTOVER_DB")
        cw.bootstrap_wired(path=path)
        cc.switch("clerk", actor="op", approval="chat", stop_conditions="health fails", health=OK, path=path,
                  observe_until=(datetime.now(UTC) + timedelta(hours=72)).isoformat())
        with pytest.raises(rt.ScheduleAuthorityError, match="not on the target route"):
            _resume("chief-daily")                          # `chief` was never switched
        assert _resume("clerk-daily")["to_owner"] == "dispatcher"


def test_a_workflow_that_is_neither_research_nor_trade_path_is_refused(world):
    with pytest.raises(rt.ScheduleAuthorityError, match="not a research workflow"):
        rt.owner_switch("live-trader", to_owner="dispatcher", actor="op", reason="r", approval="a")


def test_a_resumed_workflow_is_registered_by_the_dispatcher(world, config, monkeypatch):
    _resume("chief-daily")
    entries = ts.trade_schedule_entries(config)
    assert list(entries) == ["chief-daily"]                               # clerk-daily is enabled but still paused
    assert ts.resumed_at("chief-daily") is not None


def test_the_daemon_registers_only_resumed_trade_workflows(world, config, monkeypatch):
    from apscheduler.schedulers import blocking
    from test_phase_f_schedule_runtime import FakeScheduler

    _resume("chief-daily")
    monkeypatch.setenv("ATS_CONFIG_DIR", str(config))
    monkeypatch.setattr("ats.workflow.ownership.research_schedule_entries", lambda **kw: {})
    ran = []
    monkeypatch.setattr("ats.workflow.trade_schedules.run_trade_workflow",
                        lambda wid, entry, trigger, now=None: ran.append(wid) or {"status": "complete"})
    holder = {}
    monkeypatch.setattr(blocking, "BlockingScheduler", lambda *a, **kw: holder.setdefault("s", FakeScheduler(*a, **kw)))
    dispatcher_daemon._run_dispatcher()                    # the daemon swallows the scheduler's stop
    jobs = {job["id"] for job in holder["s"].jobs}
    assert "phase_e:chief-daily" in jobs and "phase_e:clerk-daily" not in jobs
    assert all(w == "chief-daily" for w in ran)
    # nothing from before the resume is replayed
    assert not ran


# --- the chief workflow's behaviour ------------------------------------------------------------------------ #
@pytest.mark.usefixtures("open_decision_read_gate")
def test_incomplete_research_makes_no_cycle_and_asks_the_model_nothing(e2e_env, broker, monkeypatch):
    monkeypatch.setattr("ats.agents.chief.decide.from_context",
                        lambda *a, **k: pytest.fail("the model must not be asked when research is incomplete"))
    out = ts._chief_daily({"fundamental_mode": "event"})
    assert out["outcome"] == "research_incomplete"
    assert e2e_env.conn.execute("SELECT COUNT(*) FROM decision_cycles").fetchone()[0] == 0


@pytest.mark.usefixtures("open_decision_read_gate")
def test_a_new_chief_cycle_supersedes_yesterdays_waiting_and_rejected_cycles(e2e_env, broker, monkeypatch):
    _seed(e2e_env)
    _chief_llm(monkeypatch)
    repo = DecisionAuditRepository(e2e_env)
    for cycle, status in (("chief-20260101-100000", "pending_approval"), ("chief-20260101-110000", "risk_rejected")):
        repo.create_cycle(cycle_id=cycle, trigger_source="scheduled")
        repo.transition(cycle, to_status=status, actor="test")
    out = ts._chief_daily({"fundamental_mode": "event"})
    assert out["outcome"] == "pending_approval"
    for cycle in ("chief-20260101-100000", "chief-20260101-110000"):
        row = repo.get_cycle(cycle)
        assert row["status"] == "superseded" and "取代" in row["superseded_reason"]
    assert repo.get_cycle(out["cycle_id"])["status"] == "pending_approval"
