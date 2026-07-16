"""Chief decision graph (LangGraph) — the ONE funnel every order flows through.

    START → assemble_context → chief_decide → risk_gate → persist_decision
    persist_decision → (route) → boss_review | END      # no decisions / --no-execute
    boss_review(interrupt) → trader → persist → END

All trading workflows converge here: chief 每日收口 (source=scheduled), PEAD
event trades (pead-chief), manual chief runs (chief), and the trader CLI
(stored-decisions / manual, decide=False with seed_decisions). The risk gate
runs BEFORE the approval interrupt so the Boss reviews post-risk decisions;
the decision is persisted BEFORE the interrupt so the audit trail exists even
if the Boss never answers the card. Async channels (Feishu) resume via
checkpoint + thread_id (`ats serve`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..schemas.channel import ApprovalRequest
from ..schemas.decision import BossApproval
from .chief_state import ChiefDecisionState

log = logging.getLogger("ats.graph.chief")

# Sources whose decisions are the Chief's own -> persisted to the decisions table.
# trader CLI sources (manual / stored-decisions) skip it: manual orders are not
# chief decisions, and stored-decisions would duplicate rows already there.
CHIEF_SOURCES = ("chief", "scheduled", "pead-chief")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def assemble_context(state: ChiefDecisionState) -> dict:
    from ..trader import execute as texec

    out: dict = {}
    if not state.event_data:
        out["event_data"] = texec.pead_event_data()
    if not state.decide:
        return out
    from ..agents.chief import assemble

    ctx = assemble.build(live_broker=state.use_broker)
    log.info("chief context: %s", ctx.stats())
    out.update(context_text=ctx.as_context(), context_stats=ctx.stats(),
               net_liquidation=ctx.net_liquidation)
    return out


def chief_decide(state: ChiefDecisionState) -> dict:
    if not state.decide:
        return {"decisions": list(state.seed_decisions)}
    from ..agents.chief import decide

    result = decide.from_context(state.context_text, cycle_id=state.cycle_id,
                                 as_of=state.as_of, use_llm=state.use_llm)
    print(f"👔 chief {state.cycle_id}\n{result.summary}")
    for d in result.decisions:
        size = f"${d.notional_usd:,.0f}" if d.notional_usd else (
            f"w={d.target_weight:.0%}" if d.target_weight else "?")
        print(f"   {d.action.upper()} {d.symbol} {size} conv={d.conviction:.2f} — {d.rationale[:70]}")
    if not result.decisions:
        print("   (无行动 — 零决策)")
    return {"summary": result.summary, "decisions": result.decisions}


def risk_gate(state: ChiefDecisionState) -> dict:
    """Hold-filter -> live portfolio -> 6-layer pre-trade gate -> size -> card body."""
    from ..trader import execute as texec

    decisions = [d for d in state.decisions if d.action != "hold"]
    if not decisions:
        print("(no actionable decisions)")
        return {"decisions": []}

    from ..risk import checks as risk_checks
    from ..trader import portfolio as tport

    pf = tport.snapshot() if state.use_broker else None
    decisions, risk_notes, _ = risk_checks.pre_trade(decisions, pf,
                                                     event_data=state.event_data or None)
    for n in risk_notes:
        print(f"   [risk] {n}")
    if not decisions:
        print("(所有决策被风控硬约束拦下 — 无单可下)")
        return {"decisions": [], "portfolio": pf, "risk_notes": risk_notes}

    sized = texec.size_decisions(decisions)
    summary = texec.build_approval_summary(sized, risk_notes, state.source)
    return {"decisions": decisions, "portfolio": pf,
            "qty_by_symbol": {d.symbol: q for d, q in sized},
            "risk_notes": risk_notes, "approval_summary": summary}


def persist_decision(state: ChiefDecisionState) -> dict:
    """Audit trail BEFORE the approval interrupt — a card the Boss never answers
    still leaves the decision + full context on record."""
    if state.source not in CHIEF_SOURCES:
        return {}
    from ..memory import get_store

    get_store().save_chief_run(cycle_id=state.cycle_id, as_of=state.as_of,
                               summary=state.summary, decisions=state.decisions)
    if state.use_llm and state.decide:   # audit report; skip for stubs
        from ..agents.chief import report as chief_report
        from ..agents.chief.decide import ChiefResult
        from ..config import load_macro_config

        try:
            out_dir = load_macro_config().output_dir
        except Exception:  # noqa: BLE001
            out_dir = ""
        path = chief_report.write(ChiefResult(cycle_id=state.cycle_id, as_of=state.as_of,
                                              summary=state.summary, decisions=state.decisions,
                                              context_text=state.context_text), out_dir)
        if path:
            print(f"📝 {path}")
    return {}


def route_after_persist(state: ChiefDecisionState) -> str:
    if not state.execute or not state.decisions:
        return "end"
    return "review"


def boss_review(state: ChiefDecisionState) -> dict:
    if state.auto_approve:
        return {"approval": BossApproval(status="approved", reviewer="auto",
                                         reviewed_at=_now())}
    request = ApprovalRequest(cycle_id=state.cycle_id, as_of=state.as_of,
                              decisions=state.decisions,
                              context_summary=state.approval_summary)
    verdict = interrupt(request.model_dump(mode="json"))
    return {"approval": BossApproval.model_validate(verdict)}


def trader(state: ChiefDecisionState) -> dict:
    from ..trader import execute as texec

    approval = state.approval
    approved = approval.effective_decisions(state.decisions)
    sized_all = [(d, state.qty_by_symbol.get(d.symbol, 0.0)) for d in state.decisions]

    if state.dry_run or not approved:
        print(f"→ {approval.status}: no orders placed (dry_run={state.dry_run})")
        return {"order_results": texec.cancelled_entries(sized_all, state.cycle_id,
                                                         approval.status)}

    # Boss overrides / direct instructions may add symbols the gate never sized.
    to_place = []
    for d in approved:
        q = state.qty_by_symbol.get(d.symbol) or texec._size(d)
        if q > 0:
            to_place.append((d, q))
    entries, fills = texec.place_orders(to_place, state.cycle_id)
    for e in entries:
        print(f"   {e.action} {e.symbol} x{e.qty:.0f} [{e.status}]"
              + (f" @ {e.avg_fill_price}" if e.avg_fill_price else ""))
    return {"order_results": entries, "fills": fills}


def persist(state: ChiefDecisionState) -> dict:
    from ..memory import get_store
    from ..trader import execute as texec

    context = texec.trade_context_json(state.source, state.approval, state.decisions)
    get_store().save_trades(state.order_results, cycle_id=state.cycle_id,
                            source=state.source, context=context)
    if state.fills:
        get_store().upsert_fills(state.fills)
    return {}


def build_chief_graph(checkpointer=None):
    g = StateGraph(ChiefDecisionState)
    for name, fn in [
        ("assemble_context", assemble_context), ("chief_decide", chief_decide),
        ("risk_gate", risk_gate), ("persist_decision", persist_decision),
        ("boss_review", boss_review), ("trader", trader), ("persist", persist),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "assemble_context")
    g.add_edge("assemble_context", "chief_decide")
    g.add_edge("chief_decide", "risk_gate")
    g.add_edge("risk_gate", "persist_decision")
    g.add_conditional_edges("persist_decision", route_after_persist,
                            {"review": "boss_review", "end": END})
    g.add_edge("boss_review", "trader")
    g.add_edge("trader", "persist")
    g.add_edge("persist", END)

    return g.compile(checkpointer=checkpointer)
