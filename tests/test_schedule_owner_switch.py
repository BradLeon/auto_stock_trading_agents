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
