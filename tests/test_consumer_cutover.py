"""Consumer-wide research-read switch (Phase F simplification, 2026-10-15).

Real route store, real resolver and real runtime route decision on a physically
isolated control database. Health checks are explicit fakes here; the health
module itself is covered in test_consumer_health.py.
"""
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from ats.workflow import consumer_cutover as cc
from ats.workflow import cutover as co
from ats.workflow import cutover_routing as cr
from ats.workflow import cutover_wiring as cw
from ats.workflow import runtime_reads as rr
from ats.workflow import scoped_routes as sr

POINT = "2026-10-14T02:46:31.536419+00:00"
OK = {"status": "ok", "problems": [], "warnings": []}
BAD = {"status": "failed", "problems": ["no admitted documents for the whole universe"], "warnings": []}


@pytest.fixture
def authority(tmp_path):
    from ats.workflow.isolation import isolated_run

    with isolated_run("consumer-cutover", root=tmp_path / "iso") as environment:
        path = environment.path_for("ATS_CUTOVER_DB")
        cw.bootstrap_wired(path=path)
        yield path


def who(consumer="information", symbol="MRVL"):
    return rr.business_identity(consumer, kind="entity", scope_id=symbol, entities=[symbol], as_of=POINT)


def future(hours=72):
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def switch(authority, consumer="information", **kw):
    args = dict(actor="operator", approval="user approved in chat", observe_until=future(),
                stop_conditions="health check fails", health=OK, path=authority)
    return cc.switch(consumer, **{**args, **kw})


def test_unswitched_consumer_defaults_to_the_old_route(authority):
    route = sr.resolve_route(co.PROJECTION_READ, who(), path=authority)
    assert route["route"] == "legacy" and route["generation"] == 0 and route["consumer_wide"] is False


def test_switch_moves_every_scope_of_the_consumer_and_no_other_consumer(authority):
    result = switch(authority)
    assert result["from_route"] == "legacy" and result["to_route"] == "target" and result["generation"] == 1
    for symbol in ("MRVL", "MSFT", "NVDA", "TSM"):
        route = sr.resolve_route(co.PROJECTION_READ, who("information", symbol), path=authority)
        assert route["route"] == "target" and route["consumer_wide"] is True
    assert sr.resolve_route(co.PROJECTION_READ, who("fundamental"), path=authority)["route"] == "legacy"
    assert sr.resolve_route(co.PROJECTION_READ, who("technical"), path=authority)["route"] == "legacy"


def test_an_exact_scope_row_still_wins_over_the_consumer_wide_route(authority):
    switch(authority)
    mrvl = who("information", "MRVL")
    conn = sqlite3.connect(authority)
    conn.executescript(sr._SCHEMA)
    conn.execute("INSERT INTO scoped_cutover_routes VALUES (?,?,?,?,?,?)",
                 (co.PROJECTION_READ, mrvl.key, sr.canonical_json(mrvl.as_row()), "disabled", 5, "now"))
    conn.commit(); conn.close()
    exact = sr.resolve_route(co.PROJECTION_READ, mrvl, path=authority)
    assert exact["route"] == "disabled" and exact["generation"] == 5 and exact["consumer_wide"] is False
    assert sr.resolve_route(co.PROJECTION_READ, who("information", "MSFT"), path=authority)["route"] == "target"


def test_exact_scope_transitions_do_not_see_the_consumer_wide_generation(authority):
    switch(authority)
    exact = sr.resolve_route(co.PROJECTION_READ, who(), path=authority, exact_only=True)
    assert exact["route"] == "legacy" and exact["generation"] == 0


def test_a_global_disabled_boundary_still_overrides_the_consumer_wide_route(authority):
    switch(authority)
    co.set_route(co.PROJECTION_READ, "disabled", actor="operator", reason="emergency", path=authority)
    assert sr.resolve_route(co.PROJECTION_READ, who(), path=authority)["route"] == "disabled"


@pytest.mark.parametrize("missing", ["actor", "approval", "observe_until", "stop_conditions"])
def test_switch_refuses_without_approval_observation_or_stop_conditions(authority, missing):
    with pytest.raises(cc.ConsumerCutoverError, match=missing):
        switch(authority, **{missing: ""})
    assert cc.status(path=authority) == []


def test_switch_refuses_a_past_or_naive_observation_end(authority):
    with pytest.raises(cc.ConsumerCutoverError, match="future"):
        switch(authority, observe_until=future(-1))
    with pytest.raises(cc.ConsumerCutoverError, match="future"):
        switch(authority, observe_until=(datetime.now() + timedelta(days=1)).replace(tzinfo=None).isoformat())


def test_switch_refuses_a_failing_health_check_and_changes_nothing(authority):
    with pytest.raises(cc.ConsumerCutoverError, match="health check did not pass"):
        switch(authority, health=BAD)
    assert cc.status(path=authority) == []
    assert sr.resolve_route(co.PROJECTION_READ, who(), path=authority)["route"] == "legacy"


@pytest.mark.parametrize("consumer", ["chief", "risk", "trader", "clerk", "unknown"])
def test_only_research_read_consumers_can_switch(authority, consumer):
    with pytest.raises(cc.ConsumerCutoverError, match="research-read consumer"):
        switch(authority, consumer)


def test_log_is_append_only_and_records_every_action(authority):
    switch(authority)
    cc.record_health("information", OK, actor="data-refresh", path=authority)
    actions = [row["action"] for row in cc.log(path=authority)]
    assert actions == ["switch", "health"]
    assert cc.latest_health("information", path=authority)["status"] == "ok"
    first = cc.log(path=authority)[0]
    assert first["approval"] == "user approved in chat" and first["stop_conditions"] == "health check fails"
    with closing(sqlite3.connect(authority)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE consumer_cutover_log SET reason='x'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM consumer_cutover_log")


def test_consumer_wide_row_is_only_ever_written_for_projection_read(authority):
    switch(authority)
    with closing(sqlite3.connect(authority)) as conn:
        assert {r[0] for r in conn.execute("SELECT boundary FROM consumer_cutover_routes")} == {co.PROJECTION_READ}
    for boundary in (co.DISPATCHER_SCHEDULE, co.APPROVAL_LIFECYCLE, co.CLERK_PUBLICATION, co.LIVE_TRADER, co.ANALYST_OUTPUT):
        with closing(sqlite3.connect(authority)) as conn:
            assert cc.lookup(conn, boundary, who()) is None
        assert sr.resolve_route(boundary, who(), path=authority)["consumer_wide"] is False


def test_runtime_decision_for_a_switched_consumer_uses_no_scope_evidence(authority, monkeypatch):
    switch(authority)

    def refuse(*args, **kwargs):
        raise AssertionError("exact-scope evidence must not be consulted for a consumer-wide route")
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", refuse)
    monkeypatch.setattr("ats.data.assurance.qualification", refuse)
    decision = cr.read_route(consumer_id="information", identity=who(), path=authority)
    assert decision.route == "target" and decision.consumer_wide is True and decision.qualified


def test_runtime_decision_for_an_exact_scope_target_still_requires_its_evidence(authority, monkeypatch):
    mrvl = who()
    conn = sqlite3.connect(authority)
    conn.executescript(sr._SCHEMA)
    conn.execute("INSERT INTO scoped_cutover_routes VALUES (?,?,?,?,?,?)",
                 (co.PROJECTION_READ, mrvl.key, sr.canonical_json(mrvl.as_row()), "target", 1, "now"))
    conn.commit(); conn.close()
    seen = []

    def enforced(*args, **kwargs):
        seen.append("enforcement")
        raise co.CutoverError("boundary 'projection_read' declared but not enforced for this scope/mode")
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", enforced)
    with pytest.raises(co.CutoverError, match="not enforced"):
        cr.read_route(consumer_id="information", identity=mrvl, path=authority)
    assert seen == ["enforcement"]


# --- the deadlock this change exists to break ----------------------------------------------------

@pytest.fixture
def production_semantics(authority, monkeypatch):
    """No isolation exemption: the native input gate behaves as it does in production.

    Paths stay redirected to the isolated databases, so nothing real is touched.
    The environment variable is restored here, by hand, while the isolated run is still
    open: restoring it through monkeypatch can run after the isolated run exits and leak
    ``ATS_RUN_MODE=isolated`` into later tests.
    """
    import os

    saved = os.environ.pop("ATS_RUN_MODE", None)
    monkeypatch.setattr("ats.workflow.isolation.verified_isolation_root", lambda: None)
    monkeypatch.setattr("ats.workflow.read_windows.assert_current",
                        lambda *a, **k: pytest.fail("a consumer-wide route must not need a deployment window"))
    try:
        yield authority
    finally:
        if saved is not None:
            os.environ["ATS_RUN_MODE"] = saved


def native_gate(consumer="information", symbol="MRVL"):
    # A real entry computes "now" once and binds it; use one point for identity and cutoff.
    point = datetime.now(UTC)
    scope = rr.business_identity(consumer, kind="entity", scope_id=symbol, entities=[symbol], as_of=point)
    return rr.guard_input(consumer, {"entities": [symbol]}, as_of=point, business_scope=scope)


def test_native_input_is_refused_before_the_switch_which_is_the_original_deadlock(production_semantics):
    from ats.workflow.cutover_routing import RouteUnavailable

    with pytest.raises(RouteUnavailable, match="native target input requires an enabled exact business scope"):
        native_gate()


def test_native_input_is_served_after_the_consumer_wide_switch(production_semantics):
    from ats.workflow.cutover_routing import RouteUnavailable

    switch(production_semantics)
    for symbol in ("MRVL", "MSFT", "NVDA", "TSM"):
        assert native_gate(symbol=symbol).consumer_wide is True
    with pytest.raises(RouteUnavailable):
        native_gate("fundamental")  # a different consumer is not switched


def test_the_switch_does_not_open_any_other_boundary(production_semantics):
    switch(production_semantics)
    states = co.read_states(path=production_semantics) if hasattr(co, "read_states") else None
    for boundary in (co.DISPATCHER_SCHEDULE, co.APPROVAL_LIFECYCLE, co.CLERK_PUBLICATION, co.LIVE_TRADER, co.ANALYST_OUTPUT):
        assert sr.resolve_route(boundary, who(), path=production_semantics)["route"] != "target"
    assert states is None or all(s.route != "target" for b, s in states.items() if b != co.PROJECTION_READ)


def test_there_is_no_way_back_to_the_old_route_any_more():
    """2026-10-20: the migration is complete; the rollback action and the daily auto-rollback are gone."""
    assert not hasattr(cc, "rollback") and not hasattr(cc, "daily_check")


def _move_to_legacy(authority, consumer="technical"):
    """What the removed auto-rollback did to Technical on 2026-10-20 (written directly: no operator entry exists)."""
    from contextlib import closing as _closing
    with _closing(cc._transaction(authority)) as conn:
        cc._write(conn, consumer, route="legacy", action="rollback", actor="daily-health", approval="",
                  reason="automatic rollback: daily data-product health check failed")
        conn.execute("COMMIT")


def test_restore_puts_a_consumer_that_was_moved_to_the_old_route_back_on_the_new_one(authority):
    switch(authority, "technical")
    _move_to_legacy(authority)
    assert sr.resolve_route(co.PROJECTION_READ, who("technical"), path=authority)["route"] == "legacy"
    result = cc.restore("technical", actor="operator", approval="user approved in chat",
                        reason="auto-rolled back on 2026-10-20", health=OK, path=authority)
    assert result["from_route"] == "legacy" and result["to_route"] == "target" and result["action"] == "restore"
    assert result["generation"] == 3
    assert sr.resolve_route(co.PROJECTION_READ, who("technical"), path=authority)["route"] == "target"
    last = cc.log(consumer_id="technical", path=authority)[-1]
    assert last["action"] == "restore" and last["approval"] == "user approved in chat"


def test_restore_refuses_without_approval_or_reason_or_a_passing_check_or_when_already_new(authority):
    switch(authority, "technical")
    _move_to_legacy(authority)
    for kwargs in ({"approval": ""}, {"reason": ""}, {"actor": ""}):
        args = dict(actor="operator", approval="ok", reason="why", health=OK, path=authority)
        args.update(kwargs)
        with pytest.raises(cc.ConsumerCutoverError, match="requires"):
            cc.restore("technical", **args)
    with pytest.raises(cc.ConsumerCutoverError, match="health check did not pass"):
        cc.restore("technical", actor="operator", approval="ok", reason="why", health=BAD, path=authority)
    assert sr.resolve_route(co.PROJECTION_READ, who("technical"), path=authority)["route"] == "legacy"
    cc.restore("technical", actor="operator", approval="ok", reason="why", health=OK, path=authority)
    with pytest.raises(cc.ConsumerCutoverError, match="already on the new route"):
        cc.restore("technical", actor="operator", approval="ok", reason="why", health=OK, path=authority)


def test_restore_only_moves_research_consumers(authority):
    with pytest.raises(cc.ConsumerCutoverError, match="research-read consumer"):
        cc.restore("chief", actor="operator", approval="ok", reason="why", health=OK, path=authority)
