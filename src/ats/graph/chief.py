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
    from ..data.products import workflow_data_boundary

    out: dict = {}
    boundary = workflow_data_boundary("chief_graph")
    log.info("chief input boundary: consumer=%s mode=%s", boundary.consumer, boundary.mode)
    if not state.event_data:
        out["event_data"] = texec.pead_event_data()
    if not state.decide:
        return out
    from ..agents.chief import assemble

    ctx = assemble.build(live_broker=state.use_broker)
    log.info("chief context: %s", ctx.stats())
    out.update(context_text=ctx.as_context(), context_stats=ctx.stats(),
               net_liquidation=ctx.net_liquidation,
               actionable_scores=[list(x) for x in ctx.actionable_scores])
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
    """Hold-filter -> live portfolio -> whole-revision review -> size -> card body.

    Phase B: the review is read-only (`review_revision`) — the proposal is
    never rewritten; whatever passes lands in `approved_decisions` for the
    approval card and the trader, while `decisions` keeps the full proposal
    for the revision loop.
    """
    from ..trader import execute as texec

    decisions = [d for d in state.decisions if d.action != "hold"]
    if not decisions:
        print("(no actionable decisions)")
        return {"decisions": [], "approved_decisions": [],
                "risk_round": state.risk_round + 1}

    from ..risk import checks as risk_checks
    from ..trader import portfolio as tport

    pf = tport.snapshot() if state.use_broker else None
    if pf is None:
        # Degraded mode (paper / offline / broker account data unavailable):
        # the review cannot project a post-trade state, so like the legacy
        # gate it skips with a note. The whole-revision review itself fails
        # closed (`review_revision`); the REAL backstop for this hole is the
        # execution gate's snapshot-freshness check (task 7.5), which refuses
        # any real order without a current portfolio snapshot.
        note = "(no live portfolio — risk checks skipped)"
        print(f"   [risk] {note}")
        review = None
        risk_notes = [note]
        approved = decisions
    else:
        review = risk_checks.review_revision(decisions, pf,
                                             event_data=state.event_data or None)
        risk_notes = list(review.notes)
        approved = [d for d, ov in zip(decisions, review.order_verdicts)
                    if ov.verdict == "approved"]
    for n in risk_notes:
        print(f"   [risk] {n}")
    out: dict = {"decisions": decisions, "approved_decisions": approved,
                 "portfolio": pf, "risk_notes": risk_notes, "risk_review": review,
                 "risk_round": state.risk_round + 1,
                 "portfolio_snapshot_id": f"pf:{pf.as_of.isoformat()}" if pf else ""}
    if not approved:
        print("(所有决策被风控硬约束拦下 — 无单可下，等待修订或人工复核)")
        return out
    # The PEAD after-close window raises orders at 20:00 ET for approval overnight;
    # reprice them as limits BEFORE the approval card is built, so the Boss approves
    # the same prices that get submitted. (Operational repricing by the chief —
    # not a risk rewrite; the review binds notionals, not limit prices.)
    if state.source == "pead-chief":
        from ..config import load_pead_global

        slip = load_pead_global().get("schedule", {}).get("overnight_limit_slippage_pct", 0.5)
        approved, limit_notes = texec.as_overnight_limits(approved, slip)
        for n in limit_notes:
            print(f"   [overnight] {n}")
        risk_notes = list(risk_notes) + limit_notes

    sized = texec.size_decisions(approved)
    summary = texec.build_approval_summary(sized, risk_notes, state.source)
    out.update(risk_notes=risk_notes,
               qty_by_symbol={d.symbol: q for d, q in sized},
               approval_summary=summary)
    return out


def _decision_repo():
    from ..decision.repository import DecisionAuditRepository
    from ..memory import get_store

    return DecisionAuditRepository(get_store())


def _transition_error():
    from ..decision.repository import DecisionAuditError

    return DecisionAuditError


def _orders_payload(decisions) -> list[dict]:
    return [{"symbol": d.symbol, "action": d.action, "qty": d.qty,
             "notional_usd": d.notional_usd, "limit_price": d.limit_price,
             "conviction": d.conviction, "rationale": d.rationale}
            for d in decisions]


def _ruleset_version() -> str:
    import hashlib

    from ..config import get_config

    body = get_config().app.risk.model_dump_json()
    return "risk-" + hashlib.sha1(body.encode()).hexdigest()[:12]


def _consume_scores(state: ChiefDecisionState) -> None:
    """Terminal side effect (D12): a PEAD score is a one-time event signal."""
    from ..memory import get_store

    store = get_store()
    for pair in state.actionable_scores:
        try:
            store.mark_score_consumed(pair[0], pair[1], state.cycle_id)
        except Exception as exc:  # noqa: BLE001 - consumption must not break persist
            log.warning("score consume failed for %s: %s", pair, exc)


def _write_chief_report(state: ChiefDecisionState) -> None:
    if not (state.use_llm and state.decide):   # audit report; skip for stubs
        return
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


def persist_decision(state: ChiefDecisionState) -> dict:
    """Per-round audit persistence (design D2, task 5.2) — BEFORE the interrupt.

    Appends ONE immutable revision for this round's proposal and persists the
    round's risk review, both idempotently: a crash between risk_gate and the
    approval interrupt replays to the same revision/rows (repository-level
    content-hash and deterministic ids). Terminal side effects are NOT here —
    they run only when the cycle enters a terminal state (D12).
    """
    repo = _decision_repo()
    repo.create_cycle(cycle_id=state.cycle_id, trigger_source=state.source,
                      research_snapshot=state.research_snapshot or None,
                      created_at=state.as_of.isoformat())
    decisions = [d for d in state.decisions if d.action != "hold"]
    if not decisions:
        if state.parent_revision_no is not None or state.risk_round > 1:
            # 修订被驳空（chief_revise 无合规方案可形成）：这不是主理人主动
            # 决定不交易，而是交易意图被风控整体否决 — 静默记为 No Action 会
            # 吞掉原交易意图，必须转人工复核（spec：无法形成合规方案 → 人工
            # 复核终态）。终态转换交给 manual_review 节点。
            return {}
        # No Action is a formal terminal outcome WITH a reason (§5.1/3.4):
        # risk review, approval and execution are never invoked.
        from ..decision.snapshot import record_no_action
        from ..decision.state import CycleStatus

        changed = record_no_action(
            repo, state.cycle_id, reason=state.summary or "无行动决策",
            actor="chief", created_at=state.as_of.isoformat())
        if changed:
            _consume_scores(state)
            _write_chief_report(state)
        return {"cycle_status": CycleStatus.NO_ACTION.value}

    rev = repo.append_revision(
        cycle_id=state.cycle_id, orders=_orders_payload(decisions),
        rationale=state.summary or "", model_version="",
        parent_revision_no=state.parent_revision_no, revision_source="chief",
        created_at=state.as_of.isoformat())
    if state.risk_review is not None:
        repo.record_review(
            review_id=f"{state.cycle_id}:r{rev['revision_no']}:review",
            cycle_id=state.cycle_id, revision_no=rev["revision_no"],
            decision_hash=rev["decision_hash"], ruleset_version=_ruleset_version(),
            portfolio_snapshot_id=state.portfolio_snapshot_id or "unset",
            market_as_of=state.as_of.isoformat(),
            verdict=state.risk_review.verdict,
            violations=[v.model_dump() for v in state.risk_review.violations],
            allowed_boundary=state.risk_review.allowed_boundary.model_dump(),
            before_metrics=state.risk_review.before_metrics,
            after_metrics=state.risk_review.after_metrics,
            notes="\n".join(state.risk_review.notes),
            created_at=state.as_of.isoformat())
    from ..decision.state import CycleStatus

    to_status = (CycleStatus.PENDING_APPROVAL
                 if state.risk_review is not None
                 and state.risk_review.verdict == "approved"
                 else CycleStatus.RISK_REJECTED)
    repo.transition(state.cycle_id, to_status=to_status, actor="risk_gate",
                    revision_no=rev["revision_no"],
                    idempotency_key=_transition_key(
                        state.cycle_id, to_status.value, rev["revision_no"],
                        state.risk_round),
                    created_at=state.as_of.isoformat())
    # Legacy mirror (D2): keep `cycles`/`decisions` read entry points serving.
    repo.mirror_revision_to_legacy(state.cycle_id, rev["revision_no"],
                                   as_of=state.as_of.isoformat())
    # Pre-register the plan BEFORE the approval interrupt. A plan written after the
    # outcome is known can no longer be wrong, so it proves nothing.
    try:
        from ..journal import entries as journal_entries

        journal_entries.record_intents(state, store=repo.store)
    except Exception as exc:  # noqa: BLE001 - the journal observes, it must not block
        log.warning("journal pre-registration failed: %s", exc)
    return {"revision_no": rev["revision_no"], "revision_hash": rev["decision_hash"],
            "cycle_status": to_status.value}


def _transition_key(cycle_id: str, to_status: str, revision_no: int, round_no: int) -> str:
    import hashlib

    body = f"cycle|{cycle_id}|risk_gate|{to_status}|{revision_no}|round{round_no}"
    return hashlib.sha1(body.encode()).hexdigest()[:32]


def route_after_persist(state: ChiefDecisionState) -> str:
    """Conditional edge after per-round persistence (task 5.3)."""
    if not state.decisions:
        if state.parent_revision_no is not None or state.risk_round > 1:
            return "manual_review"          # revision emptied after a rejection
        return "end"                        # No Action terminal already recorded
    if not state.execute:
        return "end"
    review = state.risk_review
    if review is None:
        # Degraded no-portfolio mode: legacy pass-through to approval (the
        # execution gate remains the real-order backstop).
        return "review"
    if review.verdict == "approved":
        return "review"
    if state.risk_round >= state.max_risk_rounds:
        return "manual_review"
    return "revise"


def chief_revise(state: ChiefDecisionState) -> dict:
    """Accept the review's counterproposal and draft the next revision (5.5).

    The boundary comes from the review's `allowed_boundary` — the CHIEF chooses
    to adopt it and cites the parent revision; risk never rewrote anything.
    """
    from ..schemas.decision import is_increasing_action

    review = state.risk_review
    revised: list[TradeDecision] = []
    notes = list(state.risk_notes)
    notes.append(f"chief_revise: 采纳第 {state.risk_round} 轮风控边界，生成修订")
    if review is not None:
        for d, ov in zip(state.decisions, review.order_verdicts):
            if ov.verdict == "approved":
                revised.append(d)
            elif (ov.max_allowed_notional and d.notional_usd
                  and is_increasing_action(d.action)):
                # accept the boundary explicitly, citing the violation
                reason = ov.reasons[0] if ov.reasons else "risk boundary"
                revised.append(d.model_copy(update={
                    "notional_usd": ov.max_allowed_notional,
                    "rationale": f"[revise@r{state.risk_round}] 采纳边界 "
                                 f"${ov.max_allowed_notional:,.0f}: {reason}"}))
            else:
                notes.append(f"chief_revise: 放弃 {d.action} {d.symbol}（{', '.join(ov.reasons) or '不可修订'}）")
    return {"decisions": revised, "approved_decisions": [],
            "parent_revision_no": state.revision_no or None,
            "risk_notes": notes}


def manual_review(state: ChiefDecisionState) -> dict:
    """Bounded loop exhausted (§5.4): leave the cycle for a human."""
    from ..decision.state import CycleStatus

    repo = _decision_repo()
    event, changed = repo.transition(
        state.cycle_id, to_status=CycleStatus.MANUAL_REVIEW, actor="risk_gate",
        payload={"reason": f"{state.risk_round} 轮修订后仍被驳回"},
        created_at=state.as_of.isoformat())
    if changed:
        repo.conn.execute(
            "UPDATE decision_cycles SET final_outcome = ?, updated_at = ? "
            "WHERE cycle_id = ?",
            ("manual_review", state.as_of.isoformat(), state.cycle_id))
        repo.conn.commit()
        _consume_scores(state)
        _write_chief_report(state)
    return {"cycle_status": CycleStatus.MANUAL_REVIEW.value}


def boss_review(state: ChiefDecisionState) -> dict:
    # The card shows EXACTLY the revision the review approved (§10.3): the
    # boss binds to this revision via its hash, never to a free-form edit.
    if state.auto_approve:
        return {"approval": BossApproval(status="approved", reviewer="auto",
                                         reviewed_at=_now())}
    request = ApprovalRequest(cycle_id=state.cycle_id, as_of=state.as_of,
                              decisions=state.approved_decisions,
                              context_summary=state.approval_summary)
    verdict = interrupt(request.model_dump(mode="json"))
    return {"approval": BossApproval.model_validate(verdict)}


def trader(state: ChiefDecisionState) -> dict:
    from ..trader import execute as texec

    approval = state.approval
    approved = approval.effective_decisions(state.approved_decisions)
    sized_all = [(d, state.qty_by_symbol.get(d.symbol, 0.0))
                 for d in state.approved_decisions]

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
    from ..decision.state import CycleStatus
    from ..memory import get_store
    from ..trader import execute as texec

    store = get_store()
    # The verdict is only known here — persist_decision runs BEFORE the interrupt, so
    # it wrote approval_status = None and nothing ever came back to fill it in.
    if state.approval is not None:
        store.set_cycle_approval(state.cycle_id, state.approval.status)

    by_symbol = {d.symbol: d for d in state.approved_decisions or state.decisions}
    for entry in state.order_results:
        context = texec.trade_context_json(
            state.source, state.approval, state.approved_decisions or state.decisions,
            decision=by_symbol.get(entry.symbol), risk_notes=state.risk_notes)
        store.save_trades([entry], cycle_id=state.cycle_id,
                          source=state.source, context=context)
    if state.fills:
        store.upsert_fills(state.fills)
    try:
        from ..journal import entries as journal_entries

        journal_entries.record_outcome(state, store=store)
    except Exception as exc:  # noqa: BLE001
        log.warning("journal outcome write failed: %s", exc)
    # Terminal transition (D12): side effects fire exactly once, on first entry.
    repo = _decision_repo()
    executed = any(e.status == "filled" for e in state.order_results)
    status = CycleStatus.EXECUTED if (executed or (state.approval is not None
                                                   and state.approval.status == "approved")) \
        else CycleStatus.APPROVAL_REJECTED
    outcome = ("dry_run" if state.dry_run else
               "executed" if executed else str(state.approval.status if state.approval else "unknown"))
    try:
        event, changed = repo.transition(
            state.cycle_id, to_status=status, actor="trader",
            payload={"outcome": outcome},
            idempotency_key=_transition_key(state.cycle_id, status.value,
                                            state.revision_no, 0),
            created_at=_now().isoformat())
    except _transition_error() as exc:
        # No audit row (unit-driven states, legacy paths): nothing to finalize.
        log.warning("terminal transition skipped for %s: %s", state.cycle_id, exc)
        return {}
    if changed:
        repo.conn.execute(
            "UPDATE decision_cycles SET final_outcome = ?, updated_at = ? "
            "WHERE cycle_id = ?", (outcome, _now().isoformat(), state.cycle_id))
        repo.conn.commit()
        _consume_scores(state)
        _write_chief_report(state)
    return {"cycle_status": status.value}


def build_chief_graph(checkpointer=None):
    g = StateGraph(ChiefDecisionState)
    for name, fn in [
        ("assemble_context", assemble_context), ("chief_decide", chief_decide),
        ("risk_gate", risk_gate), ("persist_decision", persist_decision),
        ("chief_revise", chief_revise), ("manual_review", manual_review),
        ("boss_review", boss_review), ("trader", trader), ("persist", persist),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "assemble_context")
    g.add_edge("assemble_context", "chief_decide")
    g.add_edge("chief_decide", "risk_gate")
    g.add_edge("risk_gate", "persist_decision")
    g.add_conditional_edges("persist_decision", route_after_persist,
                            {"review": "boss_review", "revise": "chief_revise",
                             "manual_review": "manual_review", "end": END})
    g.add_edge("chief_revise", "risk_gate")     # bounded by max_risk_rounds
    g.add_edge("manual_review", END)
    g.add_edge("boss_review", "trader")
    g.add_edge("trader", "persist")
    g.add_edge("persist", END)

    return g.compile(checkpointer=checkpointer)
