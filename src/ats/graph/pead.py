"""PEAD earnings-event workflow (LangGraph).

    START → load → ┬ prep:  fetch → framework → narrative → expectations → signal_chain → persist_prep → END
                   └ score: fetch → actuals → scorecard → decision(建议) → persist_score → END

v0.2: the score branch produces a risk-aware trade RECOMMENDATION persisted in the
dossier — the Chief (agents/chief) is the only decision maker and executes via the
trader's single approval gate. No interrupt/trader nodes here anymore.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph

from ..agents import risk_manager as risk_agent, risk_validator
from ..agents.pead import prep as prep_agents, score as score_agents
from ..broker import IBKRBroker, IBKRUnavailable
from ..config import get_config, load_pead_config
from ..schemas.market import Ticker
from ..schemas.pead import (
    Actuals,
    ExpectationSet,
    FundamentalBackground,
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
                # carry the prep-phase framework/chain into the score dossier so the
                # re-save doesn't drop them
                out["fundamental_background"] = prior.fundamental_background
                out["signal_chain"] = prior.signal_chain
                out["signal_chain_summary"] = prior.signal_chain_summary
                out["earnings_date"] = prior.earnings_date
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
    from ..data import industry, market_data, options as opt_src, runup as runup_src

    fd = fund_src.fetch(state.symbol)
    out["fundamentals_text"] = fd.to_context()
    out["consensus"] = consensus_src.fetch(state.symbol)
    out["industry_context"] = industry.as_context(industry.fetch_notes())
    # Freshest weekly sector review rides along with the static notes.
    from ..config import load_pead_global

    g = load_pead_global()
    sr = g["sector_review"]
    if sr["inject_prep"]:
        from ..agents.sector import context as sector_context

        block = sector_context.prep_block(sr["sectors"][0], state.symbol)
        if block:
            out["industry_context"] += (
                "\n\n### 最新行业评审（每周更新，比上面的静态笔记更新鲜；分歧时以此为准）\n"
                + block)
    mr = g["macro_review"]
    if mr["inject_prep"]:
        from ..agents.macro import context as macro_context

        mblock = macro_context.prep_block(state.symbol, mr["name"])
        if mblock:
            out["industry_context"] += (
                "\n\n### 最新宏观评审（自上而下：利率/风险偏好/板块倾斜的大背景）\n" + mblock)

    ru = runup_src.compute(state.symbol, cfg.sector_etf, cfg.benchmark)
    # Pass the earnings date so options picks the post-earnings expiration (the one
    # whose Expected Move / IV actually prices the event).
    target_earnings = earnings_calendar.next_earnings_date(state.symbol)
    out["earnings_date"] = target_earnings.isoformat() if target_earnings else ""
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
        row = {"symbol": sc.symbol, "role": sc.role, "price_chg_pct": chg,
               "earnings_date": earnings_calendar.next_earnings_date(sc.symbol),
               "reported": False}
        # Cross-ticker read-through: if this peer already reported and we scored it,
        # carry its fundamental read (guidance/capacity + verdict) into the target's
        # signal-chain analysis — e.g. TSM's CoWoS capacity feeding NVDA's supply thesis.
        row.update(_peer_report(sc.symbol))
        rows.append(row)
    out["peer_rows"] = rows
    return out


def _peer_report(symbol: str) -> dict:
    """Freshest SCORED dossier read-through for a signal-chain peer.

    Returns {} when the peer has no scored dossier yet (leaves reported=False).
    Otherwise flags reported=True and attaches the peer's forward guidance /
    capacity commentary + scorecard band + decision so the target's signal-chain
    LLM can reason on real upstream fundamentals, not just the 20d price move.
    """
    from ..memory import get_store

    store = get_store()
    for meta in store.recent_dossiers(symbol, limit=6):
        if meta.get("phase") != "score":
            continue
        dossier = store.get_dossier(symbol, meta["fiscal_label"])
        if not dossier:
            continue
        guidance = ((dossier.actuals.guidance if dossier.actuals else "") or "").strip()
        band = ((dossier.scorecard.band if dossier.scorecard else "") or "").strip()
        decision = (dossier.decision_summary or "").strip()
        return {
            "reported": True,
            "peer_fiscal": meta["fiscal_label"],
            "peer_band": band,
            "peer_guidance": guidance[:600],
            "peer_decision": decision[:400],
        }
    return {}


def prep_framework(state: PeadState) -> dict:
    """Stable company framework (background / peers / catalysts / risks / valuation band)."""
    if not state.use_llm:
        return {}
    view = prep_agents.framework(state.config, state.fundamentals_text, state.consensus)
    fb = FundamentalBackground(
        background=view.background, peer_comparison=view.peer_comparison,
        watch_metrics=view.watch_metrics,
        catalysts=view.catalysts, key_risks=view.key_risks, valuation=view.valuation)
    return {"fundamental_background": fb}


def prep_narrative(state: PeadState) -> dict:
    cfg = state.config
    c = state.consensus or {}
    consensus_kwargs = _consensus_kwargs(c)
    if not state.use_llm:
        return {"expectation_set": ExpectationSet(
            symbol=state.symbol, fiscal_label=state.fiscal_label, as_of=state.as_of,
            narrative=state.prior_narrative or cfg.narrative_seed,
            **consensus_kwargs)}
    nv = prep_agents.narrative(cfg, state.fundamentals_text, state.consensus,
                               prior_narrative=state.prior_narrative,
                               industry_context=state.industry_context,
                               market_setup=state.market_setup)
    es = ExpectationSet(
        symbol=state.symbol, fiscal_label=state.fiscal_label, as_of=state.as_of,
        narrative=nv.narrative, focus_ranking=nv.focus_ranking, valuation=nv.valuation,
        **consensus_kwargs)
    return {"expectation_set": es}


def _consensus_kwargs(c: dict) -> dict:
    """Extract all consensus fields from the raw consensus dict for ExpectationSet."""
    rating_parts = []
    sb = c.get("rating_strong_buy", 0) or 0
    b = c.get("rating_buy", 0) or 0
    h = c.get("rating_hold", 0) or 0
    s = c.get("rating_sell", 0) or 0
    if sb or b or h or s:
        rating_parts = [f"强买{sb}", f"买{b}", f"持有{h}", f"卖{s}"]
    actions = []
    for up in (c.get("upgrades_downgrades") or [])[:3]:
        actions.append(f"{up.get('date','')} {up.get('firm','')} → {up.get('to_grade','')}")
    return {
        "consensus_eps": c.get("eps"),
        "consensus_eps_low": c.get("eps_low"),
        "consensus_eps_high": c.get("eps_high"),
        "consensus_revenue": c.get("revenue"),
        "consensus_revenue_low": c.get("revenue_low"),
        "consensus_revenue_high": c.get("revenue_high"),
        "consensus_target_price": c.get("target_mean"),
        "consensus_rating_summary": "/".join(rating_parts),
        "consensus_recent_actions": actions,
    }


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
        return {"signal_chain": prep_agents._fallback_chain(state.peer_rows),
                "signal_chain_summary": ""}
    items, summary = prep_agents.signal_chain(state.config, state.peer_rows)
    return {"signal_chain": items, "signal_chain_summary": summary}


def prep_persist(state: PeadState) -> dict:
    from ..agents.pead import report as pead_report
    from ..memory import get_store

    dossier = PeadDossier(
        symbol=state.symbol, fiscal_label=state.fiscal_label, phase="prep", updated_at=_now(),
        earnings_date=state.earnings_date,
        fundamental_background=state.fundamental_background,
        expectation_set=state.expectation_set, market_setup=state.market_setup,
        signal_chain=state.signal_chain, signal_chain_summary=state.signal_chain_summary,
        fundamentals_context=state.fundamentals_text or "",
        scorecard_dims=state.config.scorecard_dims,
        scorecard_weights={d.key: d.weight for d in state.config.scorecard_dims},
        long_threshold=state.config.long_threshold,
        run_up_warn_pct=state.config.run_up_warn_pct)
    get_store().save_dossier(dossier)
    path = pead_report.write_report(dossier)
    if path:
        print(f"   📝 {path}")
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

    # Period guard: refuse to score a transcript that reports a DIFFERENT quarter
    # than the target (a stale/wrong-quarter transcript scored against this
    # quarter's expectations yields a spurious miss). Confirmed mismatch aborts;
    # undetectable period proceeds with a flagged note.
    if text:
        from ..data import fiscal

        ok, why = fiscal.verify_transcript(state.fiscal_label, text, src)
        if not ok:
            raise ValueError(
                f"[period-guard] {state.symbol} score 已中止：{why}。transcript source={src}。"
                f" 请用正确季度的 --transcript 重跑，或等待目标季 transcript 就绪（宁可不打分也不错季）。")
        out["transcript_period_note"] = why

        # Body-quality guard: a scraper may hand back nav chrome / a truncated
        # stub (right URL, wrong content) — that silently zeroes the guidance /
        # backlog / tone dims. Refuse rather than emit a misleading partial score.
        ok_body, why_body = transcript_src.looks_like_transcript(text)
        if not ok_body:
            raise ValueError(
                f"[transcript-guard] {state.symbol} score 已中止：{why_body}。transcript source={src}。"
                f" 请换完整 transcript 源（investing.com/fool.com）用 --transcript 重跑，"
                f"或把全文放到 var/transcripts/（宁可不打分也不喂残缺正文）。")

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

    # 6-layer risk gate (event-risk clip / de-risk / beta / cluster) on top of the
    # scoped L1-2 above. Event data from the options-derived Expected Move.
    if state.portfolio is not None:
        from ..risk import checks as risk_checks

        em = state.market_setup.expected_move_pct if state.market_setup else None
        event_data = {state.symbol: {"expected_move_pct": em}} if em else None
        clipped, notes, _ = risk_checks.pre_trade(
            clipped, state.portfolio, event_data=event_data, apply_base=False)
        adjustments = list(adjustments) + notes
    return {"decisions": clipped, "decision_band": band, "risk_adjustments": adjustments}


def score_persist(state: PeadState) -> dict:
    """Persist the dossier with the decision RECOMMENDATION (the Chief makes the trade call)."""
    from ..agents.pead import report as pead_report
    from ..memory import get_store

    recs = "; ".join(
        f"{d.action} {d.symbol} " + (f"${d.notional_usd:,.0f}" if d.notional_usd
                                     else (f"{d.qty:.0f}股" if d.qty else ""))
        for d in state.decisions) or "观望"
    summary = f"{state.decision_band} | 建议: {recs}"
    if state.risk_adjustments:
        summary += " | guardrail: " + "; ".join(state.risk_adjustments)
    dossier = PeadDossier(
        symbol=state.symbol, fiscal_label=state.fiscal_label, phase="score", updated_at=_now(),
        earnings_date=state.earnings_date,
        fundamental_background=state.fundamental_background,
        expectation_set=state.expectation_set, market_setup=state.market_setup,
        signal_chain=state.signal_chain, signal_chain_summary=state.signal_chain_summary,
        fundamentals_context=state.fundamentals_text or "",
        scorecard_dims=state.config.scorecard_dims,
        scorecard_weights={d.key: d.weight for d in state.config.scorecard_dims},
        long_threshold=state.config.long_threshold,
        run_up_warn_pct=state.config.run_up_warn_pct,
        actuals=state.actuals, scorecard=state.scorecard,
        decision_summary=summary)
    get_store().save_dossier(dossier)
    pead_report.write_report(dossier)
    return {}


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build_pead_graph(checkpointer=None):
    g = StateGraph(PeadState)
    for name, fn in [
        ("load", load),
        ("prep_fetch", prep_fetch), ("prep_framework", prep_framework),
        ("prep_narrative", prep_narrative),
        ("prep_expectations", prep_expectations), ("prep_signal_chain", prep_signal_chain),
        ("prep_persist", prep_persist),
        ("score_fetch", score_fetch), ("score_actuals", score_actuals),
        ("score_scorecard", score_scorecard), ("score_decision", score_decision),
        ("score_persist", score_persist),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "load")
    g.add_conditional_edges("load", route, {"prep": "prep_fetch", "score": "score_fetch"})

    g.add_edge("prep_fetch", "prep_framework")
    g.add_edge("prep_framework", "prep_narrative")
    g.add_edge("prep_narrative", "prep_expectations")
    g.add_edge("prep_expectations", "prep_signal_chain")
    g.add_edge("prep_signal_chain", "prep_persist")
    g.add_edge("prep_persist", END)

    g.add_edge("score_fetch", "score_actuals")
    g.add_edge("score_actuals", "score_scorecard")
    g.add_edge("score_scorecard", "score_decision")
    g.add_edge("score_decision", "score_persist")
    g.add_edge("score_persist", END)

    return g.compile(checkpointer=checkpointer)
