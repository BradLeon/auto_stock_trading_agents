"""Real committed SQL permission/binding/receipts; external gate verdicts are fixtures.

These physically isolated tests do not certify production qualification or TWS.
"""
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_phase_f_joint_cutover import gates

from ats.config import REPO_ROOT
from ats.workflow import cutover as plane, joint_cutover as joint, read_windows as windows
from ats.workflow import runtime_reads as reads, invocation_bindings as bindings
from ats.workflow.scoped_routes import RouteIdentity
from ats.workflow.cutover_routing import RouteUnavailable
from ats.workflow.consumer_reads import trace_reads, record_read
from ats.workflow.isolation import isolated_run
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.planning import build_plan
from ats.agent.task_projection import ProjectionScope


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    with isolated_run("persistent-runtime", root=tmp_path / "iso"):
        monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", lambda *a, **k: None)
        monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "eligible"})
        monkeypatch.setattr("ats.workflow.shadow_reports.check_batch_report", lambda *a, **k: (True, []))
        yield SimpleNamespace(now=datetime.now(UTC), root=tmp_path / "iso", monkeypatch=monkeypatch)


def deploy(runtime, consumer, purpose, *, workflow=None, scope=None):
    contract = reads._consumer_contract(consumer)
    who = RouteIdentity(contract["domain_id"], consumer, contract["contract_version"], scope or {
        "kind": "entity", "id": "COHR", "entities": ["COHR"],
        "time_policy": "per_invocation", "deployment_purpose": purpose})
    workflow = workflow or purpose
    request = {"batch_id": "deploy:" + who.key, "workflow": workflow, "deployment_policy": "until_stopped",
        "members": [{"boundary": plane.PROJECTION_READ, "identity": who.as_row(), "route": "target", "expected_generation": 0}], "schedules": []}
    adapter = gates()
    adapter["compatibility"] = lambda w, i, r: {"workflow": w, "identity": i.as_row(),
        "valid": True, "reference": "fixture-compatible", "edges": []}
    reference = "permission:" + who.key
    adapter["authorization"] = lambda m, w: {"member": m, "workflow": w, "valid": True, "reference": reference}
    joint.record_authorization(request, reference=reference, valid_until=(runtime.now+timedelta(hours=1)).isoformat(),
        actor="fixture", reason="isolated persistent protocol")
    joint.execute(request, gates=adapter, actor="fixture", reason="isolated commit", mode="isolated")
    return who, request, reference, adapter


@pytest.mark.parametrize("consumer,purpose", [
    ("layer", "layer-review"), ("information", "information-brief"), ("sector", "sector-review"),
    ("fundamental", "fundamental-routine"), ("macro", "macro-review"), ("technical", "technical-review"),
    ("chief", "chief"), ("risk", "risk"), ("trader", "trader"), ("clerk", "clerk")])
def test_ten_consumers_cross_day_exact_input_binding_and_immutable_receipts(runtime, consumer, purpose):
    who, request, reference, adapter = deploy(runtime, consumer, purpose)
    for point in (runtime.now, runtime.now+timedelta(days=2)):
        actual = reads.business_identity(consumer, kind="entity", scope_id="COHR", entities=["COHR"], as_of=point)
        selected = windows.select(actual, workflow=purpose, purpose=purpose, as_of=point)
        binding = bindings.invocation(selected, actual, workflow=purpose, purpose=purpose,
            as_of=point, run_id="run:"+point.isoformat(), plan_hash="fixture-plan")
        with reads.bind_read(who, as_of=point, invocation=binding), trace_reads() as rows:
            reads.guard_input(consumer, {"entity": "COHR"}, as_of=point)
            record_read(consumer, "isolated-read", refs=["input:"+point.isoformat()],
                        source_as_of=[point.isoformat()], queried_at=point.isoformat(), payload_hash="fixture-input")
            assert rows[0]["invocation"]["actual_identity"] == actual.as_row()
            with pytest.raises(RouteUnavailable, match="entity"):
                reads.guard_input(consumer, {"entity": "NVDA"}, as_of=point)
    with sqlite3.connect(plane.default_cutover_db_path()) as conn:
        receipts = [json.loads(r[0]) for r in conn.execute("SELECT receipt_json FROM read_invocation_receipts ORDER BY seq")]
        assert len(receipts) == 2 and len({r["binding"]["as_of"] for r in receipts}) == 2
        assert {r["identity"]["scope"]["deployment_purpose"] for r in receipts} == {purpose}
        assert all(r["generation"] == 1 and r["status"] == "passed" and r["reads"]
                   and r["read_status"] == "observed" and r["deployment"]["permission"] == reference for r in receipts)
        assert conn.execute("SELECT count(*) FROM joint_cutover_authorizations").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM read_invocation_receipts")


def test_purpose_ambiguity_and_relabeling_refuse(runtime):
    deploy(runtime, "fundamental", "fundamental-routine")
    point = reads.business_identity("fundamental", kind="entity", scope_id="COHR", entities=["COHR"], as_of=runtime.now)
    with pytest.raises(RouteUnavailable):
        windows.select(point, workflow="fundamental-routine", as_of=runtime.now, purpose="fundamental-event")
    # A second, different explicit Event rule cannot silently win selection.
    digest = hashlib.sha256((REPO_ROOT/"config/workflow/event_routes.yaml").read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="fixed Event"):
        RouteIdentity(point.domain_id, "fundamental", point.contract_version, {
            "kind": "entity", "id": "COHR", "entities": ["COHR"], "time_policy": "per_invocation",
            "deployment_purpose": "fundamental-event", "event_id": "x", "event_version": "1",
            "event_rule": {"route_kind": "earnings:release", "config_sha256": digest}})


@pytest.mark.parametrize("mode", ["routine", "event"])
def test_generic_fundamental_entry_uses_selected_mode_and_refuses_nested_borrowing(runtime, mode):
    purpose = "fundamental-" + mode
    who, *_ = deploy(runtime, "fundamental", purpose)

    @reads.scoped_read("fundamental")
    def selected_pass(request):
        return reads.current_read_context().invocation["purpose"]

    with reads.bind_read(who, as_of=runtime.now):
        assert selected_pass(SimpleNamespace(symbol="COHR", mode=mode)) == purpose
        with pytest.raises(RouteUnavailable, match="purpose"):
            selected_pass(SimpleNamespace(symbol="COHR", mode="event" if mode == "routine" else "routine"))


def test_decision_cycle_mapping_limits_actual_order_entities(runtime):
    scope = {"kind": "portfolio", "id": "portfolio", "entities": ["COHR", "NVDA"],
        "time_policy": "per_invocation", "deployment_purpose": "trader",
        "invocation_rule": {"kind": "decision", "id_source": "cycle_id", "entities": "subset"}}
    who, *_ = deploy(runtime, "trader", "trader", workflow="decision-workflow", scope=scope)
    for cycle in ("cycle-one", "cycle-two"):
        actual = reads.business_identity("trader", kind="decision", scope_id=cycle, entities=["COHR"], as_of=runtime.now)
        assert windows.select(actual, workflow="decision-workflow", purpose="trader", as_of=runtime.now) == who
        inv = bindings.invocation(who, actual, workflow="decision-workflow", purpose="trader", as_of=runtime.now, run_id=cycle)
        with reads.bind_read(who, as_of=runtime.now, invocation=inv):
            reads.guard_input("trader", {"entity": "COHR", "cycle_id": cycle})
            with pytest.raises(RouteUnavailable, match="cycle"):
                reads.guard_input("trader", {"entity": "COHR", "cycle_id": "unrelated-cycle"})
            with pytest.raises(RouteUnavailable, match="entity"):
                reads.guard_input("trader", {"entity": "NVDA"})
    escaped = reads.business_identity("trader", kind="decision", scope_id="other", entities=["AAPL"], as_of=runtime.now)
    with pytest.raises(RouteUnavailable): windows.select(escaped, workflow="decision-workflow", purpose="trader", as_of=runtime.now)
    with pytest.raises(RouteUnavailable, match="dynamic"):
        with reads.bind_read(who, as_of=runtime.now): pass


def test_real_native_packet_records_query_source_and_payload_hash(runtime):
    who, *_ = deploy(runtime, "risk", "risk")
    from ats.data.consumer_api import read_input
    with reads.bind_read(who, as_of=runtime.now), trace_reads() as rows:
        result = read_input("risk", "RISK_RULES", scope={})
        assert result.status == "complete"
        assert rows[-1]["queried_at"] == result.queried_at.isoformat()
        assert len(rows[-1]["payload_hash"]) == 64


def test_runtime_history_cannot_expand_business_entities(runtime, monkeypatch):
    from ats.data.products.sector_inputs import sector_price_history
    who, *_ = deploy(runtime, "technical", "technical-review")
    monkeypatch.setattr("ats.data.sector_snapshot.fetch_close_history", lambda *a, **kw: pytest.fail("escaped provider read"))
    with reads.bind_read(who, as_of=runtime.now):
        with pytest.raises(RouteUnavailable, match="entity"):
            sector_price_history(["NVDA"])


def test_refreshing_qualification_does_not_clear_latched_stop_or_revoke_permission(runtime):
    from test_phase_f_read_executor import policy
    from ats.workflow.read_recovery import ReadStopped
    who, _, reference, _ = deploy(runtime, "technical", "technical-review")
    policy(who, runtime.root)
    with reads.bind_read(who, as_of=runtime.now):
        runtime.monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "ineligible", "reasons": ["expired"]})
        with pytest.raises(ReadStopped):
            reads.gate_read(who)
    runtime.monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "eligible"})
    with pytest.raises(ReadStopped):
        with reads.bind_read(who, as_of=runtime.now+timedelta(days=1)):
            reads.gate_read(who)
    with sqlite3.connect(plane.default_cutover_db_path()) as conn:
        assert conn.execute("SELECT count(*) FROM joint_cutover_authorizations").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM joint_cutover_revocations WHERE reference=?", (reference,)).fetchone()[0] == 0


def test_committed_permission_rechecked_before_actual_publication(runtime):
    who, request, reference, adapter = deploy(runtime, "technical", "technical-review")
    from ats.workflow.cutover_wiring import guard_write
    with reads.bind_read(who, as_of=runtime.now):
        joint.revoke_authorization(reference, actor="fixture", reason="mid-run revoke")
        # Test real publication boundary even inside isolation.
        with pytest.raises(RouteUnavailable, match="authorization"):
            guard_write(plane.ANALYST_OUTPUT, what="late projection")


def calendar_release(runtime, *, period="2026-09"):
    from ats.data.stores.schedule_calendar import ScheduleCalendarStore, ScheduleEventCandidate, macro_identity
    calendar = ScheduleCalendarStore()
    candidate = ScheduleEventCandidate(source_id="bls", event_type="cpi", stable_identity=macro_identity("CPI", period, "initial"),
        event_date="2026-10-09", time_precision="date", fetched_at=runtime.now.isoformat())
    saved = calendar.submit_candidate(candidate, at=runtime.now)
    event = calendar.publish_candidate(saved["candidate_id"], at=runtime.now)
    return calendar, calendar.confirm_release(event["event_id"], expected_version=event["event_version"],
        admitted_materials=[{"kind": "official_release", "status": "admitted", "ref": "bls:"+period}],
        source_id="bls", at=runtime.now)


def test_real_calendar_versions_follow_one_declared_rule_and_stale_worker_refuses(runtime):
    calendar, event = calendar_release(runtime)
    entities = reads.configured_entities("portfolio", "portfolio")
    digest = hashlib.sha256((REPO_ROOT/"config/workflow/event_routes.yaml").read_bytes()).hexdigest()
    scope = {"kind": "portfolio", "id": "portfolio", "entities": entities,
        "time_policy": "per_invocation", "deployment_purpose": "macro-review",
        "event_rule": {"route_kind": "cpi:release", "config_sha256": digest}}
    who, *_ = deploy(runtime, "macro", "macro-review", scope=scope)
    point = datetime.now(UTC)
    actual = reads.business_identity("macro", kind="portfolio", scope_id="portfolio", entities=entities,
        as_of=point, event_id=event["event_id"], event_version=str(event["event_version"]))
    assert windows.select(actual, workflow="macro-review", purpose="macro-review", as_of=point) == who
    inv = bindings.invocation(who, actual, workflow="macro-review", purpose="macro-review", as_of=point, run_id="event-run")
    # Publishing a corrected real calendar version invalidates the older worker.
    from ats.data.stores.schedule_calendar import ScheduleEventCandidate, macro_identity
    amended = ScheduleEventCandidate(source_id="bls", event_type="cpi", stable_identity=macro_identity("CPI", "2026-09", "initial"),
        event_date="2026-10-10", time_precision="date", fetched_at=datetime.now(UTC).isoformat())
    saved = calendar.submit_candidate(amended)
    revised = calendar.publish_candidate(saved["candidate_id"])
    revised = calendar.confirm_release(revised["event_id"], expected_version=revised["event_version"],
        admitted_materials=[{"kind": "official_release", "status": "admitted", "ref": "bls:v2"}], source_id="bls")
    with pytest.raises(RouteUnavailable, match="current"):
        bindings.recheck(who, inv)
    later = datetime.now(UTC)
    new = reads.business_identity("macro", kind="portfolio", scope_id="portfolio", entities=entities,
        as_of=later, event_id=revised["event_id"], event_version=str(revised["event_version"]))
    assert windows.select(new, workflow="macro-review", purpose="macro-review", as_of=later) == who
    with pytest.raises(RouteUnavailable): windows.select(new, workflow="other", purpose="macro-review", as_of=later)
    # Rule/version stability cannot override ordinary current qualification.
    runtime.monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "ineligible", "reasons": ["expired"]})
    with pytest.raises(RouteUnavailable): windows.select(new, workflow="macro-review", purpose="macro-review", as_of=later)


def test_bounded_query_window_cannot_be_moved_past_input_cutoff(runtime):
    who, *_ = deploy(runtime, "technical", "technical-review")
    actual = reads.business_identity("technical", kind="entity", scope_id="COHR", entities=["COHR"], as_of=runtime.now)
    prior = {"start": (runtime.now-timedelta(days=30)).isoformat(), "end": runtime.now.isoformat()}
    inv = bindings.invocation(who, actual, workflow="technical-review", purpose="technical-review", as_of=runtime.now, query_window=prior)
    assert bindings.recheck(who, inv) == actual
    with pytest.raises(RouteUnavailable, match="cutoff"):
        bindings.invocation(who, actual, workflow="technical-review", purpose="technical-review", as_of=runtime.now,
            query_window={**prior, "end": (runtime.now+timedelta(seconds=1)).isoformat()})


def test_nested_clerk_entry_cannot_relabel_its_query_window(runtime):
    entities = reads.configured_entities("portfolio", "portfolio")
    who, *_ = deploy(runtime, "clerk", "clerk", scope={"kind":"portfolio", "id":"portfolio",
        "entities":entities, "time_policy":"per_invocation", "deployment_purpose":"clerk"})
    day = runtime.now.date().isoformat()
    actual = reads.business_identity("clerk", kind="portfolio", scope_id="portfolio", entities=entities, as_of=runtime.now)
    window = {"start":runtime.now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(), "end":runtime.now.isoformat()}
    inv = bindings.invocation(who, actual, workflow="clerk", purpose="clerk", as_of=runtime.now, query_window=window)

    @reads.scoped_read("clerk")
    def bookkeeping(*, window_start=None, window_end=None, as_of=None):
        return reads.current_read_context().invocation["query_window"]

    with reads.bind_read(who, as_of=runtime.now, invocation=inv):
        assert bookkeeping(as_of=day) == window
        with pytest.raises(RouteUnavailable, match="window"):
            bookkeeping(as_of=day, window_start=(runtime.now-timedelta(days=1)).date().isoformat())


def test_new_projection_keeps_evaluation_time_and_real_publication_time():
    from ats.agent.task_projection import build_envelope, projection_context
    point = (datetime.now(UTC)-timedelta(minutes=5)).isoformat()
    published = datetime.now(UTC).isoformat()
    with projection_context(workflow_run_id="actual", agent_run_id="task", as_of=point):
        envelope = build_envelope(role="technical_review", scope=ProjectionScope(kind="entity", id="COHR"),
            payload={"entity":"COHR", "summary":"fixed evaluation", "signal":"neutral"},
            as_of=published, created_at=published, input_refs=["source:original-time"])
    assert envelope.as_of == point and envelope.created_at == published
    assert envelope.input_refs == ["source:original-time"]


def test_event_task_binds_actual_trigger_and_refuses_conflicting_metadata(runtime):
    trigger = TriggerContext(kind="event", workflow_id="macro-review", event_id="published", event_version="2")
    plan = build_plan(requested_tasks=["macro-review"], scope=ProjectionScope(kind="portfolio", id="portfolio"),
        trigger=trigger, as_of=runtime.now, run_id="actual-event", enter_decision_cycle=False)
    identity = reads.task_identity(plan, plan.tasks[0])
    assert identity.scope["event_id"] == "published" and identity.scope["event_version"] == "2"
    from dataclasses import replace
    bad = replace(plan, task_inputs={"event_id":"other", "event_version":"2"})
    with pytest.raises(RouteUnavailable, match="actual trigger"):
        reads.task_identity(bad, bad.tasks[0])


from test_phase_f_paired_business import isolated_run as isolated, input_fixture as business_inputs


def test_stable_trader_permission_reads_real_governed_quote_and_rejects_other_cycle(runtime, monkeypatch):
    from ats.data import execution_prices as prices
    from ats.data.consumer_api import read_input
    from ats.schemas.decision import TradeDecision
    from test_phase_f_trader_a import quote
    monkeypatch.setattr(prices, "session_context", lambda now: (True, now-timedelta(days=1)))
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price", lambda symbol, **kw: quote(symbol=symbol))
    who, *_ = deploy(runtime, "trader", "trader", workflow="decision-workflow", scope={
        "kind":"portfolio", "id":"portfolio", "entities":["COHR","NVDA"],
        "time_policy":"per_invocation", "deployment_purpose":"trader",
        "invocation_rule":{"kind":"decision","id_source":"cycle_id","entities":"subset"}})
    actual=reads.business_identity("trader", kind="decision", scope_id="actual-cycle", entities=["COHR"], as_of=runtime.now)
    inv=bindings.invocation(who,actual,workflow="decision-workflow",purpose="trader",as_of=runtime.now,run_id="actual-cycle")
    orders=[TradeDecision(symbol="COHR", action="buy", notional_usd=1000)]
    with reads.bind_read(who, as_of=runtime.now, invocation=inv):
        with prices.price_request("trader",orders,cycle_id="actual-cycle",purpose="preapproval_normalization"):
            assert reads.current_read_context().identity == who
            result=read_input("trader","MARKET_DATA",scope={"kind":"execution_price","entity":"COHR",
                "cycle_id":"actual-cycle","purpose":"preapproval_normalization","currency":"USD"})
            assert result.status == "complete" and result.input_refs
        with prices.price_request("trader",orders,cycle_id="other-cycle",purpose="preapproval_normalization"):
            with pytest.raises(RouteUnavailable,match="cycle"):
                read_input("trader","MARKET_DATA",scope={"kind":"execution_price","entity":"COHR",
                    "cycle_id":"other-cycle","purpose":"preapproval_normalization","currency":"USD"})


def test_real_dispatcher_two_invocations_under_one_stable_permission(business_inputs, isolated, monkeypatch):
    from ats.workflow.isolation import verified_isolation_root
    from ats.agent.task_projection import ProjectionScope
    from ats.workflow.planning import build_plan
    from ats.workflow.run_contracts import TriggerContext
    from ats.workflow.dispatcher import Dispatcher
    from ats.workflow.store import WorkflowStore
    from ats.workflow import cutover as plane
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced",lambda *a,**kw:None)
    monkeypatch.setattr("ats.data.assurance.qualification",lambda **kw:{"status":"eligible"})
    monkeypatch.setattr("ats.workflow.shadow_reports.check_batch_report",lambda *a,**kw:(True,[]))
    r=SimpleNamespace(now=datetime.now(UTC),root=verified_isolation_root(),monkeypatch=monkeypatch)
    who,*_=deploy(r,"technical","technical-review")
    dispatcher=Dispatcher(workflow_store=WorkflowStore(str(isolated.path)), max_workers=1)
    for number in range(2):
        point=datetime.now(UTC)
        plan=build_plan(requested_tasks=["technical-review"],scope=ProjectionScope(kind="entity",id="COHR"),
            trigger=TriggerContext(kind="manual",workflow_id="technical-review",trigger_id=f"persistent-{number}"),
            as_of=point.isoformat(),run_id=f"persistent-{number}",enter_decision_cycle=False,config_dir=business_inputs.root,
            task_inputs={"data_vintage_refs":[business_inputs.document.sha256]})
        plan=windows.bind_plan(plan)
        result=dispatcher.dispatch(plan)
        assert result.complete, result.as_dict()
        assert all(e.input_refs and (e.as_of<=plan.as_of if o.reused else e.as_of==plan.as_of)
                   for o in result.outcomes for e in o.projections), result.as_dict()
    with sqlite3.connect(plane.default_cutover_db_path()) as conn:
        receipts=[json.loads(row[0]) for row in conn.execute("SELECT receipt_json FROM read_invocation_receipts")]
        assert {row["binding"]["run_id"] for row in receipts} == {"persistent-0","persistent-1"}
        assert all(row["execution_plan_hash"] and row["reads"] and row["read_status"]=="observed" for row in receipts), receipts
        assert any(read["api"] == "Dispatcher.projection_reuse" for row in receipts for read in row["reads"])
        assert any(read.get("source_as_of") and read.get("queried_at") and read.get("payload_hash")
                   for row in receipts for read in row["reads"])
        assert conn.execute("SELECT count(*) FROM joint_cutover_authorizations").fetchone()[0] == 1
