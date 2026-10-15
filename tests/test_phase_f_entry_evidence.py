"""Production-mode business-entry evidence for ``projection_read`` (Phase F 8.5).

Every applicable call point is exercised for the eight real exact scopes
(Information/Fundamental x MRVL/MSFT/NVDA/TSM) with the *production* read mode
bound (``ATS_RUN_MODE`` unset, ``ReadContext.mode == "production"``) inside a
physically isolated sandbox with the broker write prohibition armed. Business
leaves are fakes: no model, collector, network or broker is reachable.

What this proves: with the route at target and a qualified scope the real entry
gates run the business body exactly once under the exact bound identity; with an
ineligible scope or a disabled route it refuses before the body with zero
persistent side effects. Entries whose contract rejects the scope (the
portfolio-level Information CLI for an entity scope) prove refusal only.

What it does not prove (stated so nobody reads more into the bundle):
- The sandbox is verified-isolated, so branches that only differ under
  ``verified_isolation_root()`` (persistent read-window selection, native input on
  a non-target route) are not the ones exercised.
- ``assert_enforced`` is a test double here: it is the very gate this evidence
  populates, so it cannot be consulted while producing it.

Set ``ATS_ENTRY_EVIDENCE_RESTORE_FROM`` to a directory of production SQLite files
to restore the (small) control-plane stores into the sandbox read-only; otherwise a
freshly bootstrapped sandbox is used so the suite is self-contained.
"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.data.consumer_api import read_input
from ats.execution import broker_write_guard as broker_guard
from ats.workflow import cutover as co
from ats.workflow import runtime_reads as rr
from ats.workflow.cutover_routing import RouteUnavailable
from ats.workflow.dispatcher import Dispatcher, TaskAdapterResult
from ats.workflow.isolation import build_environment, isolated_run, verified_isolation_root
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.scoped_routes import canonical_json, resolve_route, transition_route
from ats.workflow.store import WorkflowStore

POINT = "2026-10-14T02:46:31.536419+00:00"
SYMBOLS = ("MRVL", "MSFT", "NVDA", "TSM")
RESTORE_ENV = "ATS_ENTRY_EVIDENCE_RESTORE_FROM"
# Only the control plane is restored. Scheduler owners and workflow memory stay fresh so
# the evidence does not depend on which owner the production state happens to hold.
RESTORE = {"phase_f_cutover.sqlite": "ATS_CUTOVER_DB", "phase_f_batches.sqlite": "ATS_CUTOVER_BATCH_DB",
           "phase_f_routes.sqlite": "ATS_ROUTE_REGISTRY_PATH"}
WATCHED = ("ATS_DB_PATH", "ATS_SHADOW_DB_PATH", "ATS_DISPATCH_STATE_PATH", "ATS_CUTOVER_DB",
           "ATS_CUTOVER_BATCH_DB", "ATS_ROUTE_REGISTRY_PATH", "ATS_PERSISTENT_QUEUE_PATH")
TASK = {"information": "information-brief", "fundamental": "fundamental-routine"}

S_DISPATCH = "ats.workflow.dispatcher.Dispatcher.dispatch"
S_INFORMATION_CLI = "ats.runtime.cli.run_information_pass"
S_PEAD = "ats.runtime.cli.run_pead"
S_FUNDAMENTAL_CLI = "ats.runtime.cli._run_analyst_fundamental"
S_INPUT = "ats.data.consumer_api.read_input"
S_OWNED = "ats.workflow.ownership.run_owned_workflow"

ALL = [pytest.param(c, s, id=f"{c}-{s}") for c in ("information", "fundamental") for s in SYMBOLS]
INFORMATION = [p for p in ALL if p.values[0] == "information"]
FUNDAMENTAL = [p for p in ALL if p.values[0] == "fundamental"]


def identity(consumer, symbol):
    return rr.business_identity(consumer, kind="entity", scope_id=symbol, entities=[symbol], as_of=POINT)


@pytest.fixture(autouse=True)
def clean_guard():
    broker_guard.reset_for_tests()
    yield
    broker_guard.reset_for_tests()


@pytest.fixture
def sandbox(tmp_path):
    """Isolated state and prohibited broker writes, with the production read mode."""
    root = tmp_path / "production-mode-sandbox"
    source = os.environ.get(RESTORE_ENV)
    if source:
        surfaces = build_environment(root).surfaces
        for name, variable in RESTORE.items():
            origin = Path(source) / name
            if origin.is_file():
                target = Path(surfaces[variable])
                target.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(origin.resolve().as_uri() + "?mode=ro", uri=True)) as a, \
                        closing(sqlite3.connect(target)) as b:
                    a.backup(b)
    with isolated_run("entry-evidence", root=root) as env:
        isolated_mode = os.environ.pop("ATS_RUN_MODE")
        try:
            assert isolated_mode == "isolated" and os.environ.get("ATS_RUN_MODE") is None
            assert verified_isolation_root() is not None and broker_guard.is_prohibited()
            yield env
        finally:
            os.environ["ATS_RUN_MODE"] = isolated_mode


@pytest.fixture
def qualification(monkeypatch):
    state = {"status": "eligible"}
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **kwargs: {"status": state["status"]})
    return state


@pytest.fixture
def target(sandbox, qualification, monkeypatch):
    # The evidence registry is what this bundle populates; it cannot gate its own production.
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", lambda *a, **k: None)

    def move(who):
        transition_route(
            co.PROJECTION_READ, who, "target", actor="entry-evidence", reason="production-mode sandbox",
            expected_generation=resolve_route(co.PROJECTION_READ, who)["generation"], mode="isolated",
            qualification=lambda i: {"identity": i.as_row(), "status": "eligible"},
            report=lambda i: {"identity": i.as_row(), "valid": True, "reference": "sandbox"},
            fallback=lambda i: {"identity": i.as_row(), "valid": True, "reference": "sandbox"})
    return move


def fingerprint():
    """Row counts and highest rowid for every table of every mutable surface."""
    result = {}
    for variable in WATCHED:
        path = Path(os.environ.get(variable, ""))
        if not path.is_file():
            result[variable] = None
            continue
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            counts = {}
            for table in tables:
                try:
                    counts[table] = tuple(conn.execute(
                        f'SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM "{table}"').fetchone())
                except sqlite3.OperationalError:
                    counts[table] = (conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0)
            result[variable] = counts
    return result


# Bookkeeping of the refusal itself (the workflow's own run/audit rows). Everything else,
# every business table, must stay identical. Declared in the evidence, never implicit.
RUN_RECORDS = frozenset({"workflow_runs", "agent_runs", "workflow_run_status_history",
                         "schedule_scope_owners", "data_migrations"})


def changed_tables(before, allowed=frozenset()):
    """Tables whose row counts differ; a newly created empty table is not a change."""
    after = fingerprint()
    changed = []
    for surface in after:
        old, new = before[surface] or {}, after[surface] or {}
        for table in sorted(set(old) | set(new)):
            if table in allowed:
                continue
            if old.get(table, (0, 0)) != new.get(table, (0, 0)):
                changed.append(f"{surface}.{table}")
    return changed


def record(record_property, site, who, kind, *, calls=0, changed=0, allowed=frozenset()):
    for name, value in {"entry_exercised": site, "mode": "production",
                        "identity_json": canonical_json(who.as_row()), "path_kind": kind}.items():
        record_property(name, value)
    record_property("business_body_call_count", str(calls))
    if kind == "negative":
        record_property("negative_side_effect_count", str(changed))
        record_property("side_effect_allowlist", ",".join(sorted(allowed)))


def raises_unavailable(call, match=None):
    with pytest.raises(RouteUnavailable, match=match):
        call()


def blocked_or_unavailable(call, match=None):
    """The Dispatcher reports an ineligible scope as blocked work, not as an exception."""
    try:
        result = call()
    except RouteUnavailable as exc:
        assert match is None or match in str(exc)
        return
    assert result.status != "complete" and result.outcomes
    assert all(outcome.status == "blocked" for outcome in result.outcomes), result


# A refused target read on a scope that has a registered recovery policy latches that scope
# stopped (``read_recovery_events``): control-plane safe-stop by design, never a business write.
# It is declared in the evidence and checked strictly: only ``stopped`` rows, only for the
# identities under test.
STOP_LATCH = "read_recovery_events"


def recovery_mark():
    path = Path(os.environ["ATS_CUTOVER_DB"])
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (STOP_LATCH,)).fetchone():
            return 0
        return conn.execute(f"SELECT COALESCE(MAX(event_id), 0) FROM {STOP_LATCH}").fetchone()[0]


def recovery_events_since(mark):
    path = Path(os.environ["ATS_CUTOVER_DB"])
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (STOP_LATCH,)).fetchone():
            return []
        return [tuple(row) for row in conn.execute(
            f"SELECT kind, identity_key FROM {STOP_LATCH} WHERE event_id>? ORDER BY event_id", (mark,))]


def unlatch(members):
    """Sandbox only: re-register the same accepted proof so the next refusal stands alone."""
    from ats.workflow import read_recovery as recovery

    for member in members:
        policy = recovery.current_policy(member)
        if policy and policy["stopped"]:
            recovery.register(member, reference=policy["reference"], sha256=policy["sha256"],
                              actor="entry-evidence", reason="sandbox: isolate the next refusal")


def refuse_twice(call, who, calls, qualification, record_property, site, refused=raises_unavailable,
                 allowed=frozenset(), members=None):
    """Ineligible scope, then disabled route: both refuse before any business body."""
    members = members or [who]
    keys = {member.key for member in members}
    allowed = frozenset(allowed) | {STOP_LATCH}
    latches = 0

    def measured(match=None):
        nonlocal latches
        before, mark = fingerprint(), recovery_mark()
        refused(call, match) if match else refused(call)
        events = recovery_events_since(mark)
        assert calls == [] and rr.current_read_context() is None
        assert all(kind == "stopped" and key in keys for kind, key in events), events
        latches += len(events)
        changed = changed_tables(before, allowed)
        assert changed == [], changed
        return len(changed)
    qualification["status"] = "ineligible"
    measured()
    unlatch(members)
    qualification["status"] = "eligible"
    co.set_route(co.PROJECTION_READ, "disabled", actor="entry-evidence", reason="stop")
    changed = measured("disabled")
    record(record_property, site, who, "negative", calls=len(calls), changed=changed, allowed=allowed)
    record_property("stop_latch_events", str(latches))


# --- Dispatcher.dispatch -------------------------------------------------------------

def dispatcher_case(consumer, symbol, sandbox):
    """The plan expands dependencies, so every task's own exact identity is gated."""
    task = TASK[consumer]
    plan = build_plan(requested_tasks=[task], scope=ProjectionScope(kind="entity", id=symbol),
                      trigger=TriggerContext(kind="manual", workflow_id=task), as_of=POINT,
                      run_id=f"entry-evidence-{consumer}-{symbol}", namespace="dispatcher")
    everyone = [rr.task_identity(plan, item) for item in plan.tasks]
    who = identity(consumer, symbol)
    assert who in everyone and len(everyone) == len(set(everyone))
    calls = []

    def adapter(context):
        calls.append(rr.current_read_context().identity)
        if context.task.task_id == TASK["information"]:
            # Downstream tasks only run on a valid published upstream projection.
            context.store.save_task_projection_envelope(build_envelope(
                role="information_brief", scope=context.task.scope, as_of=POINT,
                payload={"entity": symbol, "headline": "h", "summary": "s",
                         "relevance": "high", "sources": ["ref"]}))
        return TaskAdapterResult()
    store = WorkflowStore(sandbox.path_for("ATS_SHADOW_DB_PATH"))
    adapters = {item.task_id: adapter for item in plan.tasks}
    return plan, who, everyone, calls, Dispatcher(workflow_store=store, adapters=adapters)


@pytest.mark.parametrize("consumer,symbol", ALL)
def test_dispatcher_runs_the_bound_task_for_a_qualified_target(
        consumer, symbol, sandbox, target, record_property):
    plan, who, everyone, calls, dispatcher = dispatcher_case(consumer, symbol, sandbox)
    for member in everyone:
        target(member)
    dispatcher.dispatch(plan)
    assert sorted(map(canonical_json, (i.as_row() for i in calls))) == sorted(
        canonical_json(i.as_row()) for i in everyone)
    assert calls.count(who) == 1 and rr.current_read_context() is None
    record(record_property, S_DISPATCH, who, "positive", calls=calls.count(who))


@pytest.mark.parametrize("consumer,symbol", ALL)
def test_dispatcher_refuses_before_the_task_body(
        consumer, symbol, sandbox, target, qualification, record_property):
    plan, who, everyone, calls, dispatcher = dispatcher_case(consumer, symbol, sandbox)
    for member in everyone:
        target(member)
    refuse_twice(lambda *a: dispatcher.dispatch(plan), who, calls, qualification, record_property, S_DISPATCH,
                 refused=lambda call, match=None: blocked_or_unavailable(call, match),
                 allowed=RUN_RECORDS, members=everyone)


# --- read_input ------------------------------------------------------------------------

def input_case(consumer, symbol, monkeypatch):
    calls = []
    if consumer == "information":
        product, scope = "DOC_DATA", {"entity": symbol, "kind": "documents"}

        def documents(**kwargs):
            calls.append(kwargs["entities"])
            return []
        monkeypatch.setattr("ats.data.products.unstructured.admitted_documents", documents)
        products = SimpleNamespace(unstructured=object())
    else:
        product, scope = "COMPANY_DATA", {"entity": symbol, "kind": "evidence"}

        class Products:
            def neutral_evidence(self, **kwargs):
                calls.append(kwargs["entity"])
                return {"rows": [], "rejected": []}
        products = Products()
    who = identity(consumer, symbol)
    return who, calls, lambda *a: read_input(consumer, product, scope=scope, products=products,
                                              as_of=datetime.fromisoformat(POINT), business_scope=who)


@pytest.mark.parametrize("consumer,symbol", ALL)
def test_native_input_reads_for_a_qualified_target(
        consumer, symbol, sandbox, target, monkeypatch, record_property):
    who, calls, read = input_case(consumer, symbol, monkeypatch)
    target(who)
    packet = read()
    assert packet.status in {"complete", "partial", "no_coverage"} and len(calls) == 1
    record(record_property, S_INPUT, who, "positive", calls=len(calls))


@pytest.mark.parametrize("consumer,symbol", ALL)
def test_native_input_refuses_before_the_product_read(
        consumer, symbol, sandbox, target, qualification, monkeypatch, record_property):
    who, calls, read = input_case(consumer, symbol, monkeypatch)
    target(who)
    refuse_twice(read, who, calls, qualification, record_property, S_INPUT)


# --- CLI entries -------------------------------------------------------------------------

@pytest.mark.parametrize("consumer,symbol", INFORMATION)
def test_portfolio_information_cli_refuses_an_entity_scope(
        consumer, symbol, sandbox, target, monkeypatch, record_property):
    """Contract-incompatible entry: refusal only, before collection and the business body."""
    from ats.runtime import cli

    who, calls = identity(consumer, symbol), []
    target(who)
    monkeypatch.setattr("ats.agents.information.entry.run_information_pass", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(cli, "_run_managed_article_ingestion",
                        lambda **kwargs: pytest.fail("a refused request must not collect data"))
    before = fingerprint()
    with pytest.raises(RouteUnavailable, match="does not match business request"):
        cli.run_information_pass(use_llm=False, ingest_research=False, read_scope=who.scope, read_as_of=POINT)
    assert calls == [] and rr.current_read_context() is None and changed_tables(before) == []
    record(record_property, S_INFORMATION_CLI, who, "negative", calls=len(calls), changed=0)


def pead_case(symbol, monkeypatch):
    from ats.runtime import cli

    who, calls = identity("fundamental", symbol), []
    monkeypatch.setattr("ats.graph.pead.build_pead_graph", lambda **kwargs: SimpleNamespace(
        invoke=lambda state, config=None: calls.append(rr.current_read_context().identity) or {}))
    return who, calls, lambda *a: cli.run_pead(symbol, "prep", dry_run=True, offline=True, use_llm=False,
                                               read_scope=who.scope, read_as_of=POINT)


def fundamental_cli_case(symbol, monkeypatch):
    from ats.runtime import cli

    who, calls = identity("fundamental", symbol), []
    monkeypatch.setattr("ats.agents.fundamental.entry.build_run_request", lambda *args, **kwargs: object())
    monkeypatch.setattr("ats.agents.fundamental.entry.run_fundamental_pass", lambda request: calls.append(
        rr.current_read_context().identity) or {"mode": "routine", "symbol": symbol, "summary": {}, "published": []})
    args = SimpleNamespace(symbol=symbol, mode="routine", trigger=None, no_llm=True, fiscal_label=None, cutoff=None)
    return who, calls, lambda *a: cli._run_analyst_fundamental(args, read_scope=who.scope, read_as_of=POINT)


CLI_CASES = {S_PEAD: pead_case, S_FUNDAMENTAL_CLI: fundamental_cli_case}


@pytest.mark.parametrize("site", sorted(CLI_CASES))
@pytest.mark.parametrize("consumer,symbol", FUNDAMENTAL)
def test_fundamental_cli_runs_once_for_a_qualified_target(
        site, consumer, symbol, sandbox, target, monkeypatch, record_property):
    who, calls, run = CLI_CASES[site](symbol, monkeypatch)
    target(who)
    run()
    assert calls == [who] and rr.current_read_context() is None
    record(record_property, site, who, "positive", calls=len(calls))


@pytest.mark.parametrize("site", sorted(CLI_CASES))
@pytest.mark.parametrize("consumer,symbol", FUNDAMENTAL)
def test_fundamental_cli_refuses_before_the_business_body(
        site, consumer, symbol, sandbox, target, qualification, monkeypatch, record_property):
    who, calls, run = CLI_CASES[site](symbol, monkeypatch)
    target(who)
    refuse_twice(run, who, calls, qualification, record_property, site)


# --- run_owned_workflow ------------------------------------------------------------------

OWNER_RECORDS = frozenset({"schedule_scope_owners", "data_migrations"})


def owned_case(consumer, symbol, sandbox, monkeypatch):
    from ats.workflow import ownership

    task = TASK[consumer]
    who, calls = identity(consumer, symbol), []
    monkeypatch.setattr(ownership, "load_workflow_owners", lambda *a, **k: {
        "workflows": {task: {"mode": "shadow", "task_ids": [task]}}})

    def adapter(context):
        calls.append(rr.current_read_context().identity)
        if context.task.task_id == TASK["information"]:
            context.store.save_task_projection_envelope(build_envelope(
                role="information_brief", scope=context.task.scope, as_of=POINT,
                payload={"entity": symbol, "headline": "h", "summary": "s",
                         "relevance": "high", "sources": ["ref"]}))
        return TaskAdapterResult()
    monkeypatch.setattr(ownership, "Dispatcher", lambda workflow_store: Dispatcher(
        workflow_store=workflow_store, adapters={name: adapter for name in TASK.values()}))
    scope = ProjectionScope(kind="entity", id=symbol)

    def run(*args):
        return ownership.run_owned_workflow(
            task, scope=scope, trigger=TriggerContext(kind="manual", workflow_id=task),
            request={"requested_tasks": [task], "as_of": POINT})
    return who, calls, run


def every_identity(consumer, symbol):
    names = ["information"] if consumer == "information" else ["information", "fundamental"]
    return [identity(name, symbol) for name in names]


@pytest.mark.parametrize("consumer,symbol", ALL)
def test_owned_workflow_claims_and_runs_for_a_qualified_target(
        consumer, symbol, sandbox, target, monkeypatch, record_property):
    who, calls, run = owned_case(consumer, symbol, sandbox, monkeypatch)
    for member in every_identity(consumer, symbol):
        target(member)
    result = run()
    assert result["owner"] == "shadow" and calls.count(who) == 1 and rr.current_read_context() is None
    record(record_property, S_OWNED, who, "positive", calls=calls.count(who))


@pytest.mark.parametrize("consumer,symbol", ALL)
def test_owned_workflow_refuses_before_the_trigger_claim(
        consumer, symbol, sandbox, target, qualification, monkeypatch, record_property):
    who, calls, run = owned_case(consumer, symbol, sandbox, monkeypatch)
    for member in every_identity(consumer, symbol):
        target(member)
    refuse_twice(run, who, calls, qualification, record_property, S_OWNED, allowed=OWNER_RECORDS,
                 members=every_identity(consumer, symbol))
