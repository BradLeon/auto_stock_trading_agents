"""Independent capture and actual new business execution on isolated inputs.

The matrix is fixed before capture. Capture observes external transports; every
verification reruns computation in a fresh ledger. No legacy entry is invoked.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
import json

from . import acceptance_matrix as matrices, acceptance_assertions as assertions
from .business_replay_inputs import Tape, bind_tape, implementation_hashes, encode
from .evaluation_clock import at, now
from .shadow_replay import freeze_inputs, save_inputs, digest


def _execute(matrix, seed, tape, root, execution, channel=None):
    from .paired_business import _side
    from ..memory import get_store
    from .dispatcher import Dispatcher
    from .store import WorkflowStore
    from . import schedule_runtime
    body = matrix.body
    plan = assertions._plan(body)
    states, cards, chief_errors = [], [], []
    boundary = {}
    with _side(seed, root, plan.run_id), bind_tape(tape), ExitStack() as stack:
        store = get_store()
        workflow_store = WorkflowStore(str(store.path))
        if store.conn.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (plan.run_id,)).fetchone():
            raise ValueError("actual execution requires a fresh workflow run")
        clocks = ExitStack()
        stack.enter_context(clocks)
        clocks.enter_context(at(datetime.fromisoformat(plan.as_of)))
        for task in plan.tasks:
            scope = task.scope.model_dump(mode="json")
            schedule_runtime.check_owner(task.task_id, scope, "legacy")
            token = schedule_runtime.freeze(task.task_id, scope, actor=plan.run_id, reason="isolated new-entry acceptance")
            schedule_runtime.handover(token, to_owner="dispatcher", dispositions={}, actor=plan.run_id,
                                      reason="isolated new-entry acceptance")
        broker = None
        if body["batch_class"] == "trading":
            from ..execution.route_registry import install_route
            from ..execution.simulation import simulated_execution
            from ..execution import broker_write_guard as guard
            route = install_route("simulation", generation=1, environment="paper", account=execution["account"],
                                  actor="acceptance-runner", reason="isolated no-network transport")
            guard.grant_write(route.route_id, route.generation, environment="paper", account=route.account)
            broker = stack.enter_context(simulated_execution(store=store, account=route.account))

        class ApprovalTransport:
            def request_approval(self, request):
                # Replay a recorded human verdict, then bind it to this run's own
                # reviewed revision through the real graph. It is not a grant.
                ordinal = len(cards)
                cards.append(request.model_dump(mode="json"))
                def ask():
                    if channel is None:
                        raise ValueError("capture requires an explicit human approval channel")
                    from ..channel import get_channel
                    ch = get_channel(channel) if isinstance(channel, str) else channel
                    return ch.request_approval(request)
                return tape.call("human_approval", {"cycle_id":execution["cycle_id"], "ordinal":ordinal}, ask)

        def chief_runner(snapshot, manifest):
            clocks.close()  # runtime receipt clocks are captured, never relabel quotes
            from ..graph.chief_state import ChiefDecisionState
            from ..runtime.cli import run_decision_graph
            state = ChiefDecisionState(cycle_id=execution["cycle_id"], as_of=now(UTC),
                use_broker=True, use_llm=True, dry_run=body["batch_class"] != "trading",
                research_snapshot=snapshot.to_payload())
            try:
                result = run_decision_graph(state, channel=ApprovalTransport())
                states.append(ChiefDecisionState.model_validate(result))
            except Exception as exc:
                chief_errors.append(type(exc).__name__ + ":" + str(exc))
                raise
            return states[-1].model_dump(mode="json")

        dispatch = Dispatcher(workflow_store=workflow_store, max_workers=1,
                              chief_runner=chief_runner if plan.enter_decision_cycle else None).dispatch(plan).as_dict()
        if states:
            boundary.update(cycle_id=execution["cycle_id"], risk_review=states[0].risk_review.model_dump(mode="json"),
                            approval_requests=cards)
        if broker is not None and broker.accepted:
            if not states:
                raise ValueError("Chief failed after simulated acceptance: " + json.dumps(chief_errors))
            from ..execution.route_registry import submission_receipts
            from ..trader.execute import place_orders
            from ..broker.ibkr import IBKRBroker
            from ..execution import broker_write_guard as guard
            from ..execution.clerk import clerk_run
            from ..schemas.portfolio import Position
            state = states[0]
            boundary["receipts"] = submission_receipts()
            place_orders([(d,d.qty) for d in state.approved_decisions], state.cycle_id,
                         revision_no=state.revision_no, authorization=state.authorization)
            boundary["retry_receipts"] = submission_receipts()
            # The actual IBKR facade must refuse before constructing a session,
            # including when the very same approved order has a valid test grant.
            try:
                IBKRBroker().place_orders([(d,d.qty) for d in state.approved_decisions], state.cycle_id,
                    revision_no=state.revision_no, chain={"decision_hash":state.revision_hash,
                        "approval_id":state.authorization["approval_id"]})
            except guard.BrokerWriteProhibited as exc:
                refusal = {"refusal_id":exc.refusal_id, "reason_code":exc.reason_code,
                           "destination":"IBKR", "cycle_id":state.cycle_id}
            else:
                raise PermissionError("real broker facade unexpectedly accepted an isolated order")
            store.conn.execute("CREATE TABLE acceptance_broker_refusals(refusal_id TEXT PRIMARY KEY,body TEXT NOT NULL)")
            store.conn.execute("INSERT INTO acceptance_broker_refusals VALUES (?,?)", (refusal["refusal_id"],json.dumps(refusal,sort_keys=True)))
            store.conn.commit()
            boundary["broker_refusal"] = refusal
            # Explicit synthetic fill protocol, fixed before capture. Orders and
            # their chain are produced exclusively by the actual Chief/Trader.
            for order in broker.accepted:
                broker.simulate_fill(order.order_id, shares=order.qty, price=execution["fill_price"],
                                     at=order.submitted_at+timedelta(seconds=1))
            pf = state.portfolio
            class Readback:
                def get_portfolio(self):
                    fills = broker.get_fills()
                    cost = sum(f["shares"]*f["price"] for f in fills)
                    positions = [Position(symbol=o.symbol, qty=o.qty, avg_cost=execution["fill_price"],
                        market_price=execution["fill_price"], market_value=o.qty*execution["fill_price"],
                        unrealized_pnl=0,beta=1) for o in broker.accepted]
                    return pf.model_copy(update={"as_of":max(o.submitted_at for o in broker.accepted)+timedelta(seconds=2),
                        "cash":pf.cash-cost, "gross_exposure":cost, "positions":positions})
                def get_fills(self): return broker.get_fills()
                def completed_orders(self): return []
            cutoff = max(o.submitted_at for o in broker.accepted)+timedelta(seconds=3)
            day = cutoff.date().isoformat()
            with at(cutoff):
                boundary["clerk"] = clerk_run(store=store, broker=Readback(), as_of=cutoff.isoformat(),window_start=day,window_end=day)
            boundary["accepted"] = [o.model_dump(mode="json") for o in broker.accepted]
        if tape.faults:
            raise ValueError("unfixed input consumed: " + json.dumps(encode(tape.faults),sort_keys=True))
        return {"dispatch":dispatch,"schedule":schedule_runtime.snapshot(),"store":str(store.path),
                "side_root":str(Path(root).resolve()), **boundary}


def capture(*, matrix, input_store, root, capture_id, execution=None, channel=None):
    """Observe only the new entry, with requirements fixed before any output.

    Trading explicitly selects the no-network simulation. The caller supplies a
    human channel; no runner-created approval, grant or qualification escapes it.
    """
    from .paired_business import _snapshot
    matrix = matrices.restore(matrix.as_row())
    execution = dict(execution or {})
    if matrix.body["batch_class"] in {"decision","trading"}:
        if not execution.get("cycle_id"):
            raise ValueError("fixed cycle_id required before capture")
    if matrix.body["batch_class"] == "trading":
        import math
        if not execution.get("account") or type(execution.get("fill_price")) not in (int,float) or not math.isfinite(execution["fill_price"]) or execution["fill_price"] <= 0:
            raise ValueError("explicit simulation account and positive synthetic fill price required")
    if isinstance(channel, str):
        from ..channel import get_channel
        channel = get_channel(channel)
    frozen_implementation = implementation_hashes()
    seed = _snapshot()
    from .acceptance_reports import _config
    import hashlib
    if {name:hashlib.sha256(text.encode()).hexdigest() for name,text in seed["config"].items()} != _config(matrix):
        raise ValueError("capture configuration differs from pre-run matrix")
    tape = Tape()
    tape.record_clock = True
    output = _execute(matrix,seed,tape,root,execution,channel)
    if implementation_hashes() != frozen_implementation:
        raise ValueError("implementation drift during capture")
    contents = {"persistent_refs":[],"projection_hash":{"seed":seed,"execution":execution},
        "model_config":{"rows":tape.rows,"dependency_hashes":frozen_implementation,"record_clock":True}}
    required = matrix.body["input_fixing"]
    for name in ("market_runtime","account_state","history_state","ruleset_version"):
        if required[name] != "not_applicable": contents[name] = []
    for row in tape.reads:
        contents.setdefault(row["surface"],[]).append(row["input"])
    if "ruleset_version" in contents:
        # Pin the actual config/seed state even if no governed rules read occurs.
        contents["ruleset_version"].append({"configuration_hash":digest(seed["config"])})
    value = freeze_inputs(run_id=capture_id,consumer_id=matrix.body["identity"]["consumer_id"],
        batch_class=matrix.body["batch_class"],scope=matrix.body["identity"]["scope"],
        logical_eval_time=matrix.body["plan"]["as_of"], contents=contents, reads=tape.reads)
    matrices.check_inputs(matrix,value.packet)
    identity = save_inputs(value,path=input_store)
    return {"input_hash":identity,"input_store":str(input_store),"capture_matrix":matrix.as_row(),
            "observed_dispatch":output["dispatch"],"transport_count":len(tape.rows),"governed_reads":len(tape.reads)}
