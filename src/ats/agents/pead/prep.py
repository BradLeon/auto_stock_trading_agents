"""Pre-earnings PEAD agents: narrative, expectations table, signal chain."""

from __future__ import annotations

import logging
from datetime import date

from ...schemas.pead import Expectation, PeadConfig, SignalChainItem
from ..base import run_structured
from .outputs import ExpectationsView, NarrativeView, SignalChainView

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


def narrative(config: PeadConfig, fundamentals_text: str, consensus: dict,
              prior_narrative: str = "", industry_context: str = "") -> NarrativeView:
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
        f"{industry_block}\n"
        "Produce the core thesis (fold in the monitored developments above and any per-dimension "
        "expectation shifts they noted; ground the company's positioning in the industry background "
        "if provided), an ordered list of what matters most THIS quarter (focus_ranking), and a "
        "valuation read (PE / forward PE / ceiling-floor)."
    )
    try:
        return run_structured("manager", NarrativeView, ctx, skill_slug="pead-narrative")
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
    try:
        view: ExpectationsView = run_structured("manager", ExpectationsView, ctx,
                                                skill_slug="pead-expectations")
        return [Expectation(dim_key=r.dim_key, metric=r.metric, conservative=r.conservative,
                            neutral=r.neutral, optimistic=r.optimistic, source=r.source)
                for r in view.rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("pead expectations failed for %s: %s", config.symbol, exc)
        return []


def signal_chain(config: PeadConfig, peer_rows: list[dict]) -> list[SignalChainItem]:
    """peer_rows: [{symbol, role, price_chg_pct, earnings_date, reported}]."""
    if not config.signal_chain:
        return []
    lines = "\n".join(
        f"  - {r['symbol']} ({r['role']}): 20d move {r.get('price_chg_pct')}%, "
        f"earnings {r.get('earnings_date')}, reported={r.get('reported')}" for r in peer_rows)
    ctx = (
        f"{config.symbol} sits in this AI-optical signal chain. Upstream hyperscaler CapEx / "
        f"foundry strength is a LEADING signal; peers are read-throughs.\n{lines}\n\n"
        "For each name give a one-line implication for the target. Then a one-paragraph summary "
        "of whether the chain is net supportive or cautionary heading into the print."
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
        return out or _fallback_chain(peer_rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("pead signal_chain failed for %s: %s", config.symbol, exc)
        return _fallback_chain(peer_rows)


def _fallback_chain(peer_rows: list[dict]) -> list[SignalChainItem]:
    out = []
    for r in peer_rows:
        ed = r.get("earnings_date")
        out.append(SignalChainItem(symbol=r["symbol"], role=r.get("role", "peer"),
                                   earnings_date=ed if isinstance(ed, date) else None,
                                   reported=bool(r.get("reported")),
                                   price_chg_pct=r.get("price_chg_pct"), signal=""))
    return out
