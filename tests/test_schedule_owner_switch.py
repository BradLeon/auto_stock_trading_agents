"""Light schedule-owner switch for research workflows (Phase F 10.4, 2026-10-16).

Real SQL schedule authority and real route store on a physically isolated root; the consumer health check is an
explicit fake (the health module is covered in test_consumer_health.py).
"""
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from ats.workflow import consumer_cutover as cc
from ats.workflow import cutover_wiring as cw
from ats.workflow import read_windows as rw
from ats.workflow import runtime_reads as rr
from ats.workflow import schedule_runtime as rt
from ats.workflow.isolation import isolated_run, verified_isolation_root

OK = {"status": "ok", "problems": [], "warnings": []}
BAD = {"status": "failed", "problems": ["no admitted documents"], "warnings": []}
POINT = "2026-10-14T02:46:31.536419+00:00"
SCOPE_A = {"kind": "entity", "id": "NVDA"}
SCOPE_B = {"kind": "entity", "id": "TSM"}


@pytest.fixture
def world(tmp_path):
    with isolated_run("owner-switch", root=tmp_path / "iso") as environment:
        path = environment.path_for("ATS_CUTOVER_DB")
        cw.bootstrap_wired(path=path)
        cc.switch("information", actor="op", approval="chat", stop_conditions="health fails", health=OK, path=path,
                  observe_until=(datetime.now(UTC) + timedelta(hours=72)).isoformat())
        with closing(rt._connect(write=True)) as conn:
            for scope in (SCOPE_A, SCOPE_B):
                conn.execute("INSERT INTO schedule_scope_owners VALUES(?,?,?,1,0,'')",
                             ("information-brief", rt.canonical(scope), "legacy"))
        yield path


def owners(workflow="information-brief"):
    with closing(rt._connect()) as conn:
        return {(r["scope"], r["owner"], r["generation"]) for r in conn.execute(
            "SELECT * FROM schedule_scope_owners WHERE workflow=?", (workflow,))}


def claim(key, status, scope=SCOPE_A, workflow="information-brief"):
    with closing(rt._connect(write=True)) as conn:
        conn.execute("INSERT INTO schedule_runtime_claims(key,workflow,scope,identity,owner,generation,status,actor,updated_at)"
                     " VALUES(?,?,?,?,?,1,?,?,?)", (key, workflow, rt.canonical(scope), "{}", "legacy", status, "a", "now"))


def go(**kw):
    args = dict(to_owner="dispatcher", actor="operator", reason="drill", approval="user approved", health_check=lambda c: OK)
    return rt.owner_switch(kw.pop("workflow", "information-brief"), **{**args, **kw})


def test_switch_flips_the_template_and_every_scope_row_in_one_generation(world):
    entry = go()
    assert entry["from_owner"] == "legacy" and entry["to_owner"] == "dispatcher" and entry["scopes"] == 3
    assert owners() == {("*", "dispatcher", 2), (rt.canonical(SCOPE_A), "dispatcher", 2), (rt.canonical(SCOPE_B), "dispatcher", 2)}
    assert owners("fundamental-routine") == {("*", "legacy", 1)}            # other workflows untouched
    row = rt.owner_log("information-brief")[0]
    assert row["action"] == "switch" and row["approval"] == "user approved" and json.loads(row["health"])["consumer"] == "information"


def test_going_to_the_dispatcher_needs_approval_a_healthy_consumer_and_a_target_read_route(world):
    with pytest.raises(rt.ScheduleAuthorityError, match="approval"):
        go(approval="")
    with pytest.raises(rt.ScheduleAuthorityError, match="health check failed"):
        go(health_check=lambda c: BAD)
    with pytest.raises(rt.ScheduleAuthorityError, match="not on the target route"):
        go(workflow="fundamental-routine")                                   # fundamental read route is still legacy
    assert owners() == {("*", "legacy", 1), (rt.canonical(SCOPE_A), "legacy", 1), (rt.canonical(SCOPE_B), "legacy", 1)}
    assert rt.owner_log() == []


def test_rollback_needs_neither_approval_nor_health_and_bumps_the_generation_again(world):
    go()
    entry = rt.owner_switch("information-brief", to_owner="legacy", actor="operator", reason="drill back")
    assert entry["action"] == "rollback" and entry["to_generation"] == 3
    assert {o for _, o, _ in owners()} == {"legacy"}
    with pytest.raises(rt.ScheduleAuthorityError, match="already owned"):
        rt.owner_switch("information-brief", to_owner="legacy", actor="operator", reason="again")


def test_unfinished_claims_block_the_switch_until_voided_on_purpose(world):
    claim("k-failed", "failed")
    with pytest.raises(rt.ScheduleAuthorityError, match="explicit disposition"):
        go()
    assert {o for _, o, _ in owners()} == {"legacy"}
    entry = go(void_unfinished="entity-binding rehearsal, no output")
    assert entry["voided"] == ["k-failed"]
    with closing(rt._connect()) as conn:
        assert conn.execute("SELECT status FROM schedule_runtime_claims WHERE key='k-failed'").fetchone()[0] == "void"
        history = [dict(r) for r in conn.execute("SELECT * FROM schedule_runtime_history WHERE action='disposition'")]
    assert history and json.loads(history[-1]["payload"])["reason"].startswith("entity-binding")


def test_a_running_claim_is_never_voided(world):
    claim("k-running", "running")
    with pytest.raises(rt.ScheduleAuthorityError, match="still running"):
        go(void_unfinished="whatever")
    with closing(rt._connect()) as conn:
        assert conn.execute("SELECT status FROM schedule_runtime_claims WHERE key='k-running'").fetchone()[0] == "running"


def test_completed_claims_do_not_block_and_survive_the_switch(world):
    claim("k-done", "complete")
    go()
    with closing(rt._connect()) as conn:
        assert conn.execute("SELECT status,owner FROM schedule_runtime_claims WHERE key='k-done'").fetchone()[:] == ("complete", "legacy")


def test_the_old_entry_loses_authority_after_the_switch_and_regains_it_after_the_rollback(world):
    trigger = {"kind": "manual", "trigger_id": "t1"}
    go()
    with pytest.raises(rt.ScheduleAuthorityError, match="not current owner"):
        rt.execute("information-brief", SCOPE_A, trigger, lambda: "legacy ran", owner="legacy")
    assert rt.execute("information-brief", SCOPE_A, trigger, lambda: "dispatcher ran", owner="dispatcher") == "dispatcher ran"
    rt.owner_switch("information-brief", to_owner="legacy", actor="operator", reason="drill back")
    again = rt.execute("information-brief", SCOPE_A, trigger, lambda: "legacy ran", owner="legacy")
    assert again["schedule_skipped"] and again["status"] == "complete"      # the finished trigger is not repeated
    assert rt.execute("information-brief", SCOPE_A, {"kind": "manual", "trigger_id": "t2"},
                      lambda: "legacy ran", owner="legacy") == "legacy ran"


def test_the_log_is_append_only_and_non_research_workflows_are_refused(world):
    go()
    with closing(rt._connect(write=True)) as conn, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE schedule_owner_log SET reason='x'")
    with pytest.raises(rt.ScheduleAuthorityError, match="not a research workflow"):
        rt.owner_switch("chief", to_owner="dispatcher", actor="o", reason="r", approval="a")


def test_a_frozen_scope_or_mixed_owners_refuse_the_light_path(world):
    with closing(rt._connect(write=True)) as conn:
        conn.execute("UPDATE schedule_scope_owners SET frozen=1,token='t' WHERE workflow='information-brief' AND scope=?",
                     (rt.canonical(SCOPE_A),))
    with pytest.raises(rt.ScheduleAuthorityError, match="frozen"):
        go()
    with closing(rt._connect(write=True)) as conn:
        conn.execute("UPDATE schedule_scope_owners SET frozen=0,token='',owner='dispatcher' WHERE workflow='information-brief' AND scope=?",
                     (rt.canonical(SCOPE_A),))
    with pytest.raises(rt.ScheduleAuthorityError, match="mixed"):
        go()


def who(consumer, symbol="NVDA"):
    return rr.business_identity(consumer, kind="entity", scope_id=symbol, entities=[symbol], as_of=POINT)


def test_dispatcher_plans_bind_a_consumer_on_the_target_route_without_a_registered_window(world):
    point = who("information")
    assert rw.select(point, workflow="information-brief", as_of=POINT, path=world, purpose="information-brief") == point


def test_dispatcher_plans_still_refuse_a_consumer_on_the_old_route(world):
    with pytest.raises(rr.RouteUnavailable, match="read window"):
        rw.select(who("fundamental"), workflow="fundamental-routine", as_of=POINT, path=world, purpose="fundamental-routine")


def test_an_exact_registered_window_still_wins_over_the_consumer_wide_route(world):
    from ats.workflow import cutover as co
    from ats.workflow import scoped_routes as sr

    point = who("information")
    conn = sqlite3.connect(world)
    conn.executescript(sr._SCHEMA)
    conn.execute("INSERT INTO scoped_cutover_routes VALUES (?,?,?,?,?,?)",
                 (co.PROJECTION_READ, point.key, sr.canonical_json(point.as_row()), "disabled", 5, "now"))
    conn.commit(); conn.close()
    with pytest.raises(rr.RouteUnavailable):                                 # exact-but-unauthorized stays refused
        rw.select(point, workflow="information-brief", as_of=POINT, path=world, purpose="information-brief")
    assert verified_isolation_root() is not None


def test_a_running_claim_whose_worker_already_ended_with_an_error_can_be_voided(world):
    claim("k-dead", "running")
    with closing(rt._connect(write=True)) as conn:
        rt._history(conn, "claim", "k-dead", {})
        rt._history(conn, "publication", "k-dead", {"ref": ""})
        rt._history(conn, "execution_error", "k-dead", {"error": "no projection"})
    with pytest.raises(rt.ScheduleAuthorityError, match="explicit disposition"):
        go()
    entry = go(void_unfinished="worker ended with an error after a legacy write; no projection")
    assert entry["voided"] == ["k-dead"]
    with closing(rt._connect()) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM schedule_runtime_history WHERE action='disposition' "
                                          "ORDER BY seq DESC LIMIT 1").fetchone()[0])
    assert payload["was"] == "running" and payload["worker"] == "ended with execution_error"


# --- 5.4: the user-facing words are pause / resume; the stored owner value is still "legacy" (recorded shortcut) ---

def _cli(*argv):
    from ats.runtime import cli
    return cli.main(["dispatch", *argv])


def test_the_old_owner_commands_answer_with_pause_and_resume_and_change_nothing(world, capsys):
    before = owners()
    assert _cli("owner-rollback", "--workflow", "information-brief", "--actor", "o", "--reason", "r") == 2
    assert "ats dispatch pause" in capsys.readouterr().out
    assert _cli("owner-switch", "--workflow", "information-brief", "--actor", "o", "--reason", "r",
                "--approval", "a") == 2
    assert "ats dispatch resume" in capsys.readouterr().out
    assert owners() == before and rt.owner_log() == []


def test_pause_stops_the_workflow_without_approval_and_resume_needs_it(world, capsys, monkeypatch):
    monkeypatch.setattr(rt, "_dispatcher_preconditions", lambda workflow, check=None: {"consumer": "information"})
    assert _cli("resume", "--workflow", "information-brief", "--actor", "o", "--reason", "back") == 2   # no approval
    assert "approval" in capsys.readouterr().out
    assert _cli("resume", "--workflow", "information-brief", "--actor", "o", "--reason", "back",
                "--approval", "user approved") == 0
    assert {o for _, o, _ in owners()} == {"dispatcher"}
    assert _cli("pause", "--workflow", "information-brief", "--actor", "o", "--reason", "stop") == 0
    assert {o for _, o, _ in owners()} == {"legacy"}
    assert [row["to_owner"] for row in rt.owner_log("information-brief")] == ["dispatcher", "legacy"]


def test_pause_is_refused_while_a_claim_is_running(world, capsys, monkeypatch):
    monkeypatch.setattr(rt, "_dispatcher_preconditions", lambda workflow, check=None: {})
    go()
    claim("k-run", "running")
    assert _cli("pause", "--workflow", "information-brief", "--actor", "o", "--reason", "stop",
                "--void-unfinished", "x") == 2
    assert "still running" in capsys.readouterr().out
    assert {o for _, o, _ in owners()} == {"dispatcher"}


def test_a_trigger_voided_by_a_pause_is_not_executed_again_after_the_resume(world, monkeypatch):
    """Spec 工作流停用与恢复 / 停用时作废未完成触发: the voided trigger stays void; a later arrival of the same logical
    trigger (a catch-up after the resume) is skipped, never run."""
    from ats.workflow.run_contracts import TriggerContext
    monkeypatch.setattr(rt, "_dispatcher_preconditions", lambda workflow, check=None: {})
    trigger = TriggerContext(kind="schedule", schedule_id="information-brief", scheduled_for=POINT)
    go()
    with pytest.raises(RuntimeError, match="boom"):
        rt.execute("information-brief", SCOPE_A, trigger, lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                   owner="dispatcher", actor="attempt-1")
    entry = rt.owner_switch("information-brief", to_owner="legacy", actor="operator", reason="stop",
                            void_unfinished="paused on purpose")
    assert len(entry["voided"]) == 1
    go()
    again = rt.execute("information-brief", SCOPE_A, trigger, lambda: pytest.fail("a voided trigger was executed"),
                       owner="dispatcher", actor="attempt-2")
    assert again["schedule_skipped"] is True and again["status"] == "void"
    with closing(rt._connect()) as conn:
        assert conn.execute("SELECT status FROM schedule_runtime_claims WHERE key=?", (entry["voided"][0],)).fetchone()[0] == "void"


def _skip_rows():
    with closing(rt._connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM schedule_runtime_history WHERE action=?", (rt.SKIPPED_NOT_DISPATCHER_OWNED,))]


def test_a_paused_workflow_leaves_one_skip_record_per_trigger_for_scheduled_and_manual_runs(world):
    """Spec 已停用的工作流: not run, and the reason is recorded (scheduled and manual alike), once per trigger."""
    from ats.agent.task_projection import ProjectionScope
    from ats.workflow.ownership import run_owned_workflow
    from ats.workflow.run_contracts import TriggerContext
    scope = ProjectionScope(kind="entity", id="NVDA")
    scheduled = TriggerContext(kind="schedule", workflow_id="information-brief", schedule_id="information-brief",
                               scheduled_for=POINT)
    manual = TriggerContext(kind="manual", workflow_id="information-brief")
    for trigger in (scheduled, scheduled, manual):   # the scheduled tick arrives twice (catch-up)
        assert run_owned_workflow("information-brief", scope=scope, trigger=trigger)["status"] == "legacy"
    rows = _skip_rows()
    assert len(rows) == 2
    payloads = [json.loads(r["payload"]) for r in rows]
    assert {p["kind"] for p in payloads} == {"schedule", "manual"}
    assert all(p["workflow"] == "information-brief" and "paused" in p["reason"] for p in payloads)


def test_a_dispatcher_owned_workflow_leaves_no_skip_record(world, monkeypatch):
    from ats.workflow.ownership import owner_mode
    monkeypatch.setattr(rt, "_dispatcher_preconditions", lambda workflow, check=None: {})
    go()
    from ats.agent.task_projection import ProjectionScope
    assert owner_mode("information-brief", scope=ProjectionScope(kind="entity", id="NVDA")) == "dispatcher"
    assert _skip_rows() == []


def _log(workflow, action, at, voided=()):
    with closing(rt._connect(write=True)) as conn:
        conn.executescript(rt._OWNER_LOG_SCHEMA)
        to_owner = "dispatcher" if action == "switch" else "legacy"
        conn.execute("INSERT INTO schedule_owner_log(workflow,action,from_owner,to_owner,from_generation,to_generation,"
                     "actor,approval,reason,scopes,voided,health,at) VALUES(?,?,?,?,1,2,'o','','r',1,?,'{}',?)",
                     (workflow, action, "legacy" if action == "switch" else "dispatcher", to_owner,
                      rt.canonical(list(voided)), at))


def _expected(*items):
    return {"config_hashes": {}, "window": [], "wake_expected": [], "expected": [
        {"key": key, "workflow": "information-brief", "identity": {"scheduled_for": at}, "paths": ["dispatcher"]}
        for key, at in items]}


def test_expected_triggers_during_a_pause_are_skipped_not_missing(world):
    """Spec 调度覆盖 / 停用期间的触发: a trigger due while the workflow was paused is 'skipped by pause', not an omission;
    one due before the pause or after the resume is still a real omission."""
    _log("information-brief", "switch", "2026-10-01T00:00:00+00:00")
    _log("information-brief", "rollback", "2026-10-10T00:00:00+00:00")     # pause
    _log("information-brief", "switch", "2026-10-20T00:00:00+00:00")      # resume
    result = rt.compare_triggers(_expected(("before", "2026-10-05T14:00:00+00:00"),
                                           ("during", "2026-10-15T14:00:00+00:00"),
                                           ("after", "2026-10-25T14:00:00+00:00")))
    status = {row["key"]: (row["status"], row["missing_paths"]) for row in result["rows"]}
    assert status["during"] == (rt.SKIPPED_BY_PAUSE, [])
    assert status["before"] == ("missing", ["dispatcher"])
    assert status["after"] == ("missing", ["dispatcher"])
    assert not result["clean"]


def test_a_pause_that_is_still_open_covers_every_later_trigger(world):
    _log("information-brief", "rollback", "2026-10-10T00:00:00+00:00")
    result = rt.compare_triggers(_expected(("later", "2026-10-30T14:00:00+00:00")))
    assert result["rows"][0]["status"] == rt.SKIPPED_BY_PAUSE


def test_a_workflow_that_was_never_moved_to_the_dispatcher_is_not_treated_as_paused(world):
    """Owner `legacy` is also the starting state; only a recorded pause opens a window."""
    result = rt.compare_triggers(_expected(("never", "2026-10-15T14:00:00+00:00")))
    assert result["rows"][0]["status"] == "missing" and result["rows"][0]["missing_paths"] == ["dispatcher"]


def test_a_trigger_that_has_a_claim_is_never_reclassified_as_skipped(world):
    _log("information-brief", "rollback", "2026-10-10T00:00:00+00:00")
    claim("ran-anyway", "complete")
    result = rt.compare_triggers(_expected(("ran-anyway", "2026-10-15T14:00:00+00:00")))
    assert result["rows"][0]["status"] == "complete"


def test_a_trigger_voided_by_the_pause_is_reported_as_such(world):
    _log("information-brief", "switch", "2026-10-01T00:00:00+00:00")
    claim("voided-one", "void")
    _log("information-brief", "rollback", "2026-10-10T00:00:00+00:00", voided=["voided-one"])
    result = rt.compare_triggers(_expected(("voided-one", "2026-10-09T14:00:00+00:00")))
    assert result["rows"][0]["status"] == rt.VOIDED_BY_PAUSE
    assert result["rows"][0]["missing_results"] is False


def test_an_event_trigger_dropped_during_a_pause_is_skipped_not_missing(world):
    """Event rows carry no planned instant: the moment the calendar path dropped the trigger (its skip record) is used."""
    _log("information-brief", "rollback", "2026-10-10T00:00:00+00:00")
    rt.observe_skipped_not_owned("k-ev", {"workflow": "information-brief", "trigger": {
        "kind": "event", "event_id": "earnings:NVDA:FY2027Q3:release", "event_version": "2"}})
    event = {"config_hashes": {}, "window": [], "wake_expected": [], "expected": [
        {"key": "ev", "workflow": "information-brief",
         "identity": {"event_id": "earnings:NVDA:FY2027Q3:release", "event_version": "2"}, "paths": ["dispatcher"]},
        {"key": "ev-unseen", "workflow": "information-brief",
         "identity": {"event_id": "earnings:TSM:FY2027Q3:release", "event_version": "1"}, "paths": ["dispatcher"]}]}
    status = {row["key"]: row["status"] for row in rt.compare_triggers(event)["rows"]}
    assert status == {"ev": rt.SKIPPED_BY_PAUSE, "ev-unseen": "missing"}
