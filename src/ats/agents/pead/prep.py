"""Pre-earnings PEAD agents: narrative, expectations table, signal chain."""

from __future__ import annotations

import logging
from datetime import date

from ...schemas.pead import Expectation, MarketSetup, PeadConfig, SignalChainItem
from ..base import run_structured
from .outputs import ExpectationsView, FundamentalAnalysisView, NarrativeView, SignalChainView

log = logging.getLogger("ats.agents.pead")


def _consensus_text(consensus: dict) -> str:
    """Compact consensus block: estimates + price targets + ratings + recent grade actions."""
    lines = [f"Consensus: EPS {consensus.get('eps')}, Revenue {consensus.get('revenue')}"]
    if consensus.get("target_mean") is not None:
        pt = (f"Price target: mean {consensus.get('target_mean')} "
              f"({consensus.get('target_low')}–{consensus.get('target_high')})")
        if consensus.get("target_current") is not None:
            pt += f", current price {consensus.get('target_current')}"
        lines.append(pt)
    if consensus.get("rating_buy") is not None or consensus.get("rating_strong_buy") is not None:
        lines.append(
            f"Ratings (0m): SB {consensus.get('rating_strong_buy')} / "
            f"B {consensus.get('rating_buy')} / H {consensus.get('rating_hold')} / "
            f"S {consensus.get('rating_sell')} / SS {consensus.get('rating_strong_sell')}")
    for g in (consensus.get("upgrades_downgrades") or [])[:3]:
        lines.append(f"  {g.get('date')} {g.get('firm')}: "
                     f"{g.get('from_grade') or '-'} -> {g.get('to_grade')} ({g.get('action')})")
    return "\n".join(lines)


def framework(config: PeadConfig, fundamentals_text: str,
              consensus: dict) -> FundamentalAnalysisView:
    """Stable company framework: background bullets, peer table, catalysts, risks, valuation."""
    peers = ", ".join(sc.symbol for sc in config.signal_chain) or "(none listed)"
    ctx = (
        f"Build the company framework section for {config.symbol} ({config.fiscal_label}).\n"
        f"Fundamentals:\n{fundamentals_text}\n"
        f"{_consensus_text(consensus)}\n"
        f"Its signal-chain names (candidate peers for the comparison table): {peers}. "
        f"Sector ETF: {config.sector_etf}.\n"
        "Produce: background bullets, a peer-comparison table vs the 1-2 most relevant names, "
        "a grouped quantitative watch-metrics list for this quarter, dated catalysts, "
        "thesis-invalidating key risks, and a valuation read with explicit ceiling/floor "
        "multiples and implied prices. 全部用中文输出。"
    )
    try:
        return run_structured("pead_analyst", FundamentalAnalysisView, ctx,
                              skill_slug="pead-framework")
    except Exception as exc:  # noqa: BLE001
        log.warning("pead framework failed for %s: %s", config.symbol, exc)
        return FundamentalAnalysisView()


def _market_setup_text(ms: MarketSetup | None) -> str:
    """Options/price-action evidence block for the narrative's综合预判 read."""
    if ms is None:
        return ""
    parts = []
    if ms.pre_earnings_close is not None:
        parts.append(f"pre-earnings close ${ms.pre_earnings_close:.2f}")
    if ms.run_up_vs_sector_pct is not None:
        parts.append(f"20d run-up vs sector ETF {ms.run_up_vs_sector_pct:+.1f}%")
    if ms.run_up_vs_bench_pct is not None:
        parts.append(f"vs benchmark {ms.run_up_vs_bench_pct:+.1f}%")
    if ms.dist_to_ath_pct is not None:
        parts.append(f"dist to ATH {ms.dist_to_ath_pct:+.1f}%")
    if ms.expected_move_pct is not None:
        parts.append(f"option Expected Move ±{ms.expected_move_pct:.1f}%")
    if ms.atm_iv is not None:
        # sources are inconsistent: fraction (0.55) vs percent (55.0)
        iv = ms.atm_iv * 100 if ms.atm_iv <= 3 else ms.atm_iv
        parts.append(f"ATM IV {iv:.0f}%")
    if ms.iv_skew is not None:
        parts.append(f"IV Skew (25Δ put-call) {ms.iv_skew:+.1f}pts")
    if not parts:
        return ""
    return "Market setup (options / price action): " + "; ".join(parts) + "\n"


def narrative(config: PeadConfig, fundamentals_text: str, consensus: dict,
              prior_narrative: str = "", industry_context: str = "",
              market_setup: MarketSetup | None = None) -> NarrativeView:
    # Prefer the living thesis accumulated by the monitor between earnings; fall
    # back to the static seed only on the first-ever prep (nothing accumulated yet).
    if prior_narrative.strip():
        base = (f"Living thesis accumulated from continuous monitoring — CONTINUE and refine "
                f"this, do NOT discard the developments already captured here:\n{prior_narrative}\n")
    else:
        base = f"Seed narrative: {config.narrative_seed or '(none)'}\n"
    # Stable sector/supply-chain background (Obsidian). Reference only — use it to
    # judge positioning, NOT to recap the industry.
    industry_block = ""
    if industry_context.strip():
        industry_block = (
            "\nIndustry / supply-chain background (STABLE reference, may be dated — use it to judge "
            f"{config.symbol}'s position/moat/cycle-stage/pricing-power in the chain; do NOT recap "
            f"the industry, only extract what bears on THIS quarter's thesis):\n{industry_context}\n")
    ctx = (
        f"Build the pre-earnings core narrative for {config.symbol} ({config.fiscal_label}).\n"
        f"{base}"
        f"Fundamentals:\n{fundamentals_text}\n"
        f"{_consensus_text(consensus)}\n"
        f"{_market_setup_text(market_setup)}"
        f"{industry_block}\n"
        "Produce the core thesis (fold in the monitored developments above and any per-dimension "
        "expectation shifts they noted; ground the company's positioning in the industry background "
        "if provided; if market-setup data is given, weave its read — EM, IV Skew, excess run-up — "
        "into the综合预判), an ordered list of what matters most THIS quarter (focus_ranking), and a "
        "valuation read (PE / forward PE / ceiling-floor). 全部用中文输出。"
    )
    try:
        return run_structured("pead_analyst", NarrativeView, ctx, skill_slug="pead-narrative")
    except Exception as exc:  # noqa: BLE001
        log.warning("pead narrative failed for %s: %s", config.symbol, exc)
        return NarrativeView(narrative=prior_narrative or config.narrative_seed,
                             focus_ranking=[], valuation="")


def expectations(config: PeadConfig, narrative_view: NarrativeView,
                 fundamentals_text: str, consensus: dict) -> list[Expectation]:
    dims = "\n".join(f"  - {d.key}: {d.label} (weight {d.weight:.0%})" for d in config.scorecard_dims)
    ctx = (
        f"Set the conservative/neutral/optimistic expectations for {config.symbol} "
        f"({config.fiscal_label}) for EACH scorecard dimension below.\n"
        f"Narrative: {narrative_view.narrative}\n"
        f"{_consensus_text(consensus)}\n"
        f"Fundamentals:\n{fundamentals_text}\n\n"
        f"Scorecard dimensions:\n{dims}\n\n"
        "For each dimension output one row: dim_key (exactly as above), metric, and the "
        "conservative / neutral(base-case) / optimistic levels, with a source. Ground the "
        "neutral case in consensus and prior guidance."
    )
    # On the full prep context (large fundamentals_text) sonnet intermittently emits
    # a valid ExpectationsView with rows=[] — a non-None but empty tool call that the
    # run_structured None-retry can't catch. An empty expectations table is a degraded
    # result worth retrying (same rationale as the None guard), so retry until non-empty.
    for attempt in range(3):
        try:
            view: ExpectationsView = run_structured("pead_analyst", ExpectationsView, ctx,
                                                    skill_slug="pead-expectations")
        except Exception as exc:  # noqa: BLE001
            log.warning("pead expectations failed for %s (attempt %d): %s",
                        config.symbol, attempt + 1, exc)
            continue
        if view.rows:
            return [Expectation(dim_key=r.dim_key, metric=r.metric, conservative=r.conservative,
                                neutral=r.neutral, optimistic=r.optimistic, source=r.source)
                    for r in view.rows]
        log.warning("pead expectations empty for %s (attempt %d) — retrying",
                    config.symbol, attempt + 1)
    log.warning("pead expectations still empty for %s after retries", config.symbol)
    return []


def _peer_line(r: dict) -> str:
    """One signal-chain peer row for the LLM. When the peer has a scored dossier
    (reported=True), append its fundamental read-through — guidance/capacity +
    band + decision — so upstream fundamentals drive the analysis, not just price."""
    base = (f"  - {r['symbol']} ({r['role']}): 20d move {r.get('price_chg_pct')}%, "
            f"earnings {r.get('earnings_date')}, reported={r.get('reported')}")
    if r.get("reported") and (r.get("peer_guidance") or r.get("peer_decision")):
        base += (f"\n      【已发布财报 {r.get('peer_fiscal', '')} · band={r.get('peer_band') or '—'}】"
                 f"\n      指引/产能: {r.get('peer_guidance') or '—'}"
                 f"\n      结论: {r.get('peer_decision') or '—'}")
    return base


def signal_chain(config: PeadConfig,
                 peer_rows: list[dict]) -> tuple[list[SignalChainItem], str]:
    """peer_rows: [{symbol, role, price_chg_pct, earnings_date, reported,
    peer_fiscal?, peer_band?, peer_guidance?, peer_decision?}].
    Returns (items, summary) — summary is the net supportive/cautionary paragraph."""
    if not config.signal_chain:
        return [], ""
    lines = "\n".join(_peer_line(r) for r in peer_rows)
    ctx = (
        f"{config.symbol} sits in this AI-hardware signal chain. Upstream foundry/lithography "
        f"capacity + hyperscaler CapEx are LEADING signals; peers are read-throughs.\n{lines}\n\n"
        "For each name give a one-line implication for the target. 对于标注【已发布财报】的上游/同业，"
        "优先解读其指引/产能读数对本标的的直接含义——例如上游产能松动=本标的供给上限抬升/出货上修，"
        "上游指引下修=需求或供给预警。Then a one-paragraph summary of whether the chain is net "
        "supportive or cautionary heading into the print."
    )
    items_by_symbol = {r["symbol"]: r for r in peer_rows}
    try:
        view: SignalChainView = run_structured("industry_analyst", SignalChainView, ctx,
                                               skill_slug="pead-signal-chain")
        out = []
        for it in view.items:
            r = items_by_symbol.get(it.symbol, {})
            ed = r.get("earnings_date")
            out.append(SignalChainItem(
                symbol=it.symbol, role=r.get("role", "peer"),
                earnings_date=ed if isinstance(ed, date) else None,
                reported=bool(r.get("reported")), price_chg_pct=r.get("price_chg_pct"),
                signal=it.signal))
        return (out, view.summary) if out else (_fallback_chain(peer_rows), view.summary)
    except Exception as exc:  # noqa: BLE001
        log.warning("pead signal_chain failed for %s: %s", config.symbol, exc)
        return _fallback_chain(peer_rows), ""


def _fallback_chain(peer_rows: list[dict]) -> list[SignalChainItem]:
    out = []
    for r in peer_rows:
        ed = r.get("earnings_date")
        out.append(SignalChainItem(symbol=r["symbol"], role=r.get("role", "peer"),
                                   earnings_date=ed if isinstance(ed, date) else None,
                                   reported=bool(r.get("reported")),
                                   price_chg_pct=r.get("price_chg_pct"), signal=""))
    return out
