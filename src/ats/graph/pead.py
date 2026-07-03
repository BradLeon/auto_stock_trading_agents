"""PEAD earnings-event workflow (LangGraph).

    START → load → ┬ prep:  fetch → narrative → expectations → signal_chain → persist_prep → END
                   └ score: fetch → actuals → scorecard → decision → boss_review(interrupt)
                            → trader → persist_score → END

Reuses existing building blocks: data sources, PEAD agents (prep/score),
risk_validator (hard-clip), IBKR broker, channel/HITL, Context Memory.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..agents import risk_manager as risk_agent, risk_validator
from ..agents.pead import prep as prep_agents, score as score_agents
from ..broker import IBKRBroker, IBKRUnavailable
from ..config import get_config, load_pead_config
from ..schemas.channel import ApprovalRequest
from ..schemas.decision import BossApproval, TradeDecision
from ..schemas.market import Ticker
from ..schemas.memory import TradeLogEntry
from ..schemas.pead import (
    Actuals,
    ExpectationSet,
    MarketSetup,
    PeadDossier,
    Scorecard,
    ScorecardLine,
)
from .pead_state import PeadState

log = logging.getLogger("ats.graph.pead")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _net_liq(state: PeadState) -> float:
    if state.portfolio and state.portfolio.net_liquidation > 0:
        return state.portfolio.net_liquidation
    return get_config().app.account.net_liquidation_usd


# --------------------------------------------------------------------------- #
# Shared
# --------------------------------------------------------------------------- #
def load(state: PeadState) -> dict:
    cfg = load_pead_config(state.symbol)
    fiscal = state.fiscal_label or cfg.fiscal_label
    out: dict = {"config": cfg, "fiscal_label": fiscal}

    portfolio = None
    if state.use_broker:
        try:
            portfolio = IBKRBroker(sector_by_symbol={state.symbol: "optical"}).get_portfolio()
        except IBKRUnavailable as exc:
            log.warning("portfolio read skipped: %s", exc)
    out["portfolio"] = portfolio

    if state.phase in ("score", "prep"):
        from ..memory import get_store

        prior = get_store().get_dossier(state.symbol, fiscal)
        if prior and prior.expectation_set:
            if state.phase == "score":
                out["expectation_set"] = prior.expectation_set
                out["market_setup"] = prior.market_setup
            else:  # prep: carry the accumulated monitor narrative forward (don't reset to seed)
                out["prior_narrative"] = prior.expectation_set.narrative
    return out


def route(state: PeadState) -> str:
    return state.phase


# --------------------------------------------------------------------------- #
# PREP
# --------------------------------------------------------------------------- #
def prep_fetch(state: PeadState) -> dict:
    cfg = state.config
    out: dict = {}
    if not state.live_data:
        return {"fundamentals_text": "(offline)", "consensus": {}, "peer_rows": []}

    from ..data import consensus as consensus_src, earnings_calendar, fundamentals as fund_src
    from ..data import market_data, options as opt_src, runup as runup_src

    fd = fund_src.fetch(state.symbol)
    out["fundamentals_text"] = fd.to_context()
    out["consensus"] = consensus_src.fetch(state.symbol)

    ru = runup_src.compute(state.symbol, cfg.sector_etf, cfg.benchmark)
    # Pass the earnings date so options picks the post-earnings expiration (the one
    # whose Expected Move / IV actually prices the event).
    target_earnings = earnings_calendar.next_earnings_date(state.symbol)
    opt = opt_src.fetch(state.symbol, target_earnings)
    out["market_setup"] = MarketSetup(
        symbol=state.symbol, as_of=state.as_of,
        pre_earnings_close=ru.get("pre_earnings_close"),
        run_up_vs_sector_pct=ru.get("run_up_vs_sector_pct"),
        run_up_vs_bench_pct=ru.get("run_up_vs_bench_pct"),
        dist_to_ath_pct=ru.get("dist_to_ath_pct"),
        expected_move_pct=opt.get("expected_move_pct"), atm_iv=opt.get("atm_iv"),
        iv_skew=opt.get("iv_skew"),
        notes=[f"options source: {opt.get('source')}"] if opt.get("source") else ["options: n/a"])

    rows = []
    for sc in cfg.signal_chain:
        snap = market_data.fetch_snapshot(Ticker(symbol=sc.symbol))
        chg = None
        if len(snap.history) >= 21:
            chg = round((snap.history[-1].close / snap.history[-21].close - 1) * 100, 2)
        rows.append({"symbol": sc.symbol, "role": sc.role, "price_chg_pct": chg,
                     "earnings_date": earnings_calendar.next_earnings_date(sc.symbol),
                     "reported": False})
    out["peer_rows"] = rows
    return out


def prep_narrative(state: PeadState) -> dict:
    cfg = state.config
    if not state.use_llm:
        return {"expectation_set": ExpectationSet(
            symbol=state.symbol, fiscal_label=state.fiscal_label, as_of=state.as_of,
            narrative=state.prior_narrative or cfg.narrative_seed,
            consensus_eps=state.consensus.get("eps"),
            consensus_revenue=state.consensus.get("revenue"))}
    nv = prep_agents.narrative(cfg, state.fundamentals_text, state.consensus,
                               prior_narrative=state.prior_narrative)
    es = ExpectationSet(
        symbol=state.symbol, fiscal_label=state.fiscal_label, as_of=state.as_of,
        narrative=nv.narrative, focus_ranking=nv.focus_ranking, valuation=nv.valuation,
        consensus_eps=state.consensus.get("eps"), consensus_revenue=state.consensus.get("revenue"))
    return {"expectation_set": es}


def prep_expectations(state: PeadState) -> dict:
    if not state.use_llm:
        return {}
    from ..agents.pead.outputs import NarrativeView

    es = state.expectation_set
    nv = NarrativeView(narrative=es.narrative, focus_ranking=es.focus_ranking, valuation=es.valuation)
    es.expectations = prep_agents.expectations(state.config, nv, state.fundamentals_text,
                                               state.consensus)
    return {"expectation_set": es}


def prep_signal_chain(state: PeadState) -> dict:
    if not state.use_llm:
        return {"signal_chain": prep_agents._fallback_chain(state.peer_rows)}
    return {"signal_chain": prep_agents.signal_chain(state.config, state.peer_rows)}


def prep_persist(state: PeadState) -> dict:
    from ..memory import get_store

    dossier = PeadDossier(
        symbol=state.symbol, fiscal_label=state.fiscal_label, phase="prep", updated_at=_now(),
        expectation_set=state.expectation_set, market_setup=state.market_setup,
        signal_chain=state.signal_chain)
    get_store().save_dossier(dossier)
    return {}


# --------------------------------------------------------------------------- #
# SCORE
# --------------------------------------------------------------------------- #
def score_fetch(state: PeadState) -> dict:
    out: dict = {}
    from ..data import fundamentals as fund_src, transcript as transcript_src

    out["fundamentals_text"] = (fund_src.fetch(state.symbol).to_context()
                                if state.live_data else "(offline)")
    # Fetch the transcript when explicitly provided, or in live mode; skip offline
    # (avoids network in tests / offline runs).
    if state.transcript_source or state.live_data:
        text, src = transcript_src.fetch(state.symbol, state.fiscal_label, state.transcript_source)
    else:
        text, src = "", "offline"
    out["transcript_text"] = text
    out["transcript_resolved_source"] = src

    # Official documents: SEC 8-K earnings release + investor decks from the folder.
    if state.live_data:
        from ..data import documents

        docs = documents.gather(state.symbol)
        out["documents_text"] = "\n\n".join(f"### {label}\n{txt[:25000]}" for label, txt in docs)

    # Need run-up for the decision; recompute if the prep dossier lacked it.
    if state.market_setup is None and state.live_data:
        from ..data import runup as runup_src

        ru = runup_src.compute(state.symbol, state.config.sector_etf, state.config.benchmark)
        out["market_setup"] = MarketSetup(symbol=state.symbol, as_of=state.as_of,
                                          run_up_vs_sector_pct=ru.get("run_up_vs_sector_pct"))
    return out


def score_actuals(state: PeadState) -> dict:
    if not state.use_llm:
        return {"actuals": Actuals(symbol=state.symbol, fiscal_label=state.fiscal_label,
                                   as_of=state.as_of, guidance="(no-llm)")}
    actuals = score_agents.extract_actuals(
        state.config, state.expectation_set, state.transcript_text,
        state.fundamentals_text, state.as_of, state.transcript_resolved_source,
        documents_text=state.documents_text)
    return {"actuals": actuals}


def score_scorecard(state: PeadState) -> dict:
    if not state.use_llm:
        cfg = state.config
        lines = [ScorecardLine(dim_key=d.key, label=d.label, weight=d.weight, score=0.0,
                               weighted=0.0, note="(no-llm)") for d in cfg.scorecard_dims]
        return {"scorecard": Scorecard(symbol=state.symbol, fiscal_label=state.fiscal_label,
                                       as_of=state.as_of, lines=lines, total=0.0,
                                       threshold=cfg.long_threshold,
                                       band=score_agents._band(0.0, cfg.long_threshold))}
    return {"scorecard": score_agents.score(state.config, state.expectation_set,
                                            state.actuals, state.as_of)}


def score_decision(state: PeadState) -> dict:
    run_up = state.market_setup.run_up_vs_sector_pct if state.market_setup else None
    decisions, band, rationale = score_agents.decide(
        state.config, state.scorecard, run_up, state.portfolio, _net_liq(state))

    guardrails = risk_agent.assess(as_of=state.as_of, risk_cfg=get_config().app.risk,
                                   portfolio=state.portfolio,
                                   sector_by_symbol={state.symbol: "optical"})
    # Single-name PEAD: scope portfolio-wide forced-trims / do-not-adds to the
    # target only — don't rebalance unrelated holdings inside a per-ticker decision.
    guardrails.forced_trim = [s for s in guardrails.forced_trim if s == state.symbol]
    guardrails.no_add_list = [s for s in guardrails.no_add_list if s == state.symbol]
    clipped, adjustments = risk_validator.apply_guardrails(
        decisions, guardrails, sector_by_symbol={state.symbol: "optical"},
        net_liquidation=_net_liq(state), portfolio=state.portfolio)
    return {"decisions": clipped, "decision_band": band, "risk_adjustments": adjustments}


def boss_review(state: PeadState) -> dict:
    sc = state.scorecard
    summary = (f"PEAD {state.symbol} {state.fiscal_label}\n"
               f"Scorecard 总分 {sc.total:+.2f} (门槛 {sc.threshold:+.1f}) — {sc.band}\n"
               f"决策情景: {state.decision_band}")
    if state.risk_adjustments:
        summary += "\nGuardrail: " + "; ".join(state.risk_adjustments)
    req = ApprovalRequest(cycle_id=f"pead-{state.symbol}-{state.fiscal_label}".replace(" ", ""),
                          as_of=state.as_of, decisions=state.decisions, context_summary=summary)
    verdict = interrupt(req.model_dump(mode="json"))
    return {"approval": BossApproval.model_validate(verdict)}


def score_trader(state: PeadState) -> dict:
    approval = state.approval
    if approval is None:
        return {"order_results": []}
    to_exec = [d for d in approval.effective_decisions(state.decisions) if d.action != "hold"]
    sized = [(d, _size_qty(d, state)) for d in to_exec]

    if not state.dry_run and state.use_broker:
        try:
            broker = IBKRBroker(sector_by_symbol={state.symbol: "optical"})
            return {"order_results": broker.place_orders(sized, f"pead-{state.symbol}")}
        except IBKRUnavailable as exc:
            log.warning("PEAD execution aborted, IBKR unavailable: %s", exc)
            return {"order_results": [_errored(state, d, q, str(exc)) for d, q in sized]}
    return {"order_results": [_simulated(state, d, q) for d, q in sized]}


def score_persist(state: PeadState) -> dict:
    from ..memory import get_store

    orders = "; ".join(f"{o.action} {o.symbol} {o.qty:.0f} [{o.status}]" for o in state.order_results)
    summary = (f"{state.decision_band} | approval={getattr(state.approval, 'status', '—')} "
               f"| orders: {orders or 'none'}")
    dossier = PeadDossier(
        symbol=state.symbol, fiscal_label=state.fiscal_label, phase="score", updated_at=_now(),
        expectation_set=state.expectation_set, market_setup=state.market_setup,
        signal_chain=state.signal_chain, actuals=state.actuals, scorecard=state.scorecard,
        decision_summary=summary)
    get_store().save_dossier(dossier)
    return {}


# --- trader helpers (mirror the daily cycle) -------------------------------- #
def _size_qty(d: TradeDecision, state: PeadState) -> float:
    if d.qty:
        return d.qty
    if d.notional_usd and state.market_setup and state.market_setup.pre_earnings_close:
        return float(round(d.notional_usd / state.market_setup.pre_earnings_close))
    return 0.0


def _simulated(state: PeadState, d: TradeDecision, qty: float) -> TradeLogEntry:
    return TradeLogEntry(order_id=str(uuid.uuid4())[:8], cycle_id=f"pead-{state.symbol}",
                         symbol=d.symbol, action=d.action, qty=qty, order_type=d.order_type,
                         limit_price=d.limit_price, status="filled", submitted_at=_now(),
                         filled_at=_now(), rationale=d.rationale)


def _errored(state: PeadState, d: TradeDecision, qty: float, error: str) -> TradeLogEntry:
    return TradeLogEntry(order_id="", cycle_id=f"pead-{state.symbol}", symbol=d.symbol,
                         action=d.action, qty=qty, status="error", submitted_at=_now(), error=error)


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build_pead_graph(checkpointer=None):
    g = StateGraph(PeadState)
    for name, fn in [
        ("load", load),
        ("prep_fetch", prep_fetch), ("prep_narrative", prep_narrative),
        ("prep_expectations", prep_expectations), ("prep_signal_chain", prep_signal_chain),
        ("prep_persist", prep_persist),
        ("score_fetch", score_fetch), ("score_actuals", score_actuals),
        ("score_scorecard", score_scorecard), ("score_decision", score_decision),
        ("boss_review", boss_review), ("score_trader", score_trader),
        ("score_persist", score_persist),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "load")
    g.add_conditional_edges("load", route, {"prep": "prep_fetch", "score": "score_fetch"})

    g.add_edge("prep_fetch", "prep_narrative")
    g.add_edge("prep_narrative", "prep_expectations")
    g.add_edge("prep_expectations", "prep_signal_chain")
    g.add_edge("prep_signal_chain", "prep_persist")
    g.add_edge("prep_persist", END)

    g.add_edge("score_fetch", "score_actuals")
    g.add_edge("score_actuals", "score_scorecard")
    g.add_edge("score_scorecard", "score_decision")
    g.add_edge("score_decision", "boss_review")
    g.add_edge("boss_review", "score_trader")
    g.add_edge("score_trader", "score_persist")
    g.add_edge("score_persist", END)

    return g.compile(checkpointer=checkpointer)
