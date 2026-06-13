"""Analyst agents: build context from data, call the LLM, return typed reports.

Each function falls back to a neutral stub when use_llm is False (tests/offline)
or when required data is missing — the cycle always produces a report.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..schemas.market import MarketSnapshot

log = logging.getLogger("ats.agents")
from ..schemas.reports import (
    FundamentalReport,
    IndustryReport,
    MacroReport,
    TechnicalReport,
)
from .base import run_structured
from .outputs import AnalystView, FundamentalView, IndustryView, MacroView, TechnicalView


def _clean(view: AnalystView) -> dict:
    """View -> report kwargs, clamping conviction to the [0,1] contract."""
    d = view.model_dump()
    d["conviction"] = max(0.0, min(1.0, float(d.get("conviction", 0.0))))
    return d


# --------------------------------------------------------------------------- #
# Context formatting
# --------------------------------------------------------------------------- #
def _fmt_indicators(snap: MarketSnapshot) -> str:
    if not snap.indicators:
        return "(no indicators)"
    return "\n".join(f"  {k}: {v:.3f}" for k, v in snap.indicators.items())


def _recent_closes(snap: MarketSnapshot, n: int = 10) -> str:
    bars = snap.history[-n:]
    return ", ".join(f"{b.date}:{b.close:.2f}" for b in bars) if bars else "(no history)"


def _price_context(snap: MarketSnapshot) -> str:
    if not snap.history:
        return "No market history available."
    closes = [b.close for b in snap.history]
    hi, lo = max(closes), min(closes)
    return (
        f"Last price: {snap.last_price:.2f}\n"
        f"~52w range: {lo:.2f} – {hi:.2f}\n"
        f"Indicators:\n{_fmt_indicators(snap)}\n"
        f"Recent closes: {_recent_closes(snap)}"
    )


# --------------------------------------------------------------------------- #
# Technical (richest real data today)
# --------------------------------------------------------------------------- #
def technical(symbol: str, snapshot: MarketSnapshot | None, as_of: datetime, use_llm: bool) -> TechnicalReport:
    stub = TechnicalReport(as_of=as_of, symbol=symbol, signal="neutral", conviction=0.45,
                           thesis="[stub] no LLM/data", trend="unknown")
    if not use_llm or snapshot is None or not snapshot.history:
        return stub
    ctx = (f"Technical analysis for {symbol}.\n{_price_context(snapshot)}\n\n"
           "Judge trend, momentum, and key levels. Set support/resistance from the data.")
    try:
        view = run_structured("technical_analyst", TechnicalView, ctx, skill_slug="technical-analyst")
        return TechnicalReport(as_of=as_of, symbol=symbol, **_clean(view))
    except Exception as exc:  # noqa: BLE001 - one analyst must not abort the cycle
        log.warning("technical analyst failed for %s: %s", symbol, exc)
        return stub


# --------------------------------------------------------------------------- #
# Fundamental (limited data until SEC/financials source lands)
# --------------------------------------------------------------------------- #
def fundamental(symbol: str, snapshot: MarketSnapshot | None, as_of: datetime, use_llm: bool) -> FundamentalReport:
    if not use_llm:
        # Deterministic bullish stub for offline/test runs (drives the manager stub).
        return FundamentalReport(as_of=as_of, symbol=symbol, signal="bullish", conviction=0.6,
                                 thesis="[stub] no LLM")
    stub = FundamentalReport(as_of=as_of, symbol=symbol, signal="neutral", conviction=0.4,
                             thesis="[fallback] fundamental analyst unavailable")
    price = _price_context(snapshot) if snapshot else "No market data."
    ctx = (f"Fundamental analysis for {symbol}.\n{price}\n\n"
           "NOTE: dedicated financial statements / filings are not yet wired in. "
           "Reason from your knowledge of the company plus the price context; be "
           "explicit about uncertainty and keep conviction modest.")
    try:
        view = run_structured("fundamental_analyst", FundamentalView, ctx, skill_slug="fundamental-analyst")
        return FundamentalReport(as_of=as_of, symbol=symbol, **_clean(view))
    except Exception as exc:  # noqa: BLE001
        log.warning("fundamental analyst failed for %s: %s", symbol, exc)
        return stub


# --------------------------------------------------------------------------- #
# Industry
# --------------------------------------------------------------------------- #
def industry(sector: str, brief: str, as_of: datetime, use_llm: bool) -> IndustryReport:
    stub = IndustryReport(as_of=as_of, sector=sector, signal="neutral", conviction=0.5,
                          thesis="[stub] no LLM", supply_chain_notes=brief)
    if not use_llm:
        return stub
    ctx = (f"Industry analysis for sector '{sector}'.\nSupply chain: {brief}\n\n"
           "Assess sector cyclicality, bottlenecks, and margin transmission up/down "
           "the chain. No live industry feed yet — reason from general knowledge and "
           "state assumptions.")
    try:
        view = run_structured("industry_analyst", IndustryView, ctx, skill_slug="industry-analyst")
        data = _clean(view)
        if not data.get("supply_chain_notes"):
            data["supply_chain_notes"] = brief
        return IndustryReport(as_of=as_of, sector=sector, **data)
    except Exception as exc:  # noqa: BLE001
        log.warning("industry analyst failed for %s: %s", sector, exc)
        return stub


# --------------------------------------------------------------------------- #
# Macro (single, global)
# --------------------------------------------------------------------------- #
def macro(as_of: datetime, use_llm: bool) -> MacroReport:
    stub = MacroReport(as_of=as_of, signal="neutral", conviction=0.4, thesis="[stub] no LLM")
    if not use_llm:
        return stub
    ctx = ("Assess the current US equity market regime for a swing/position horizon. "
           "Cover rates, inflation, employment, geopolitics, and breadth (SPX/NDX, VIX, "
           "fear & greed). NOTE: live macro feeds (FRED/VIX) are not yet wired in — "
           "reason from general knowledge, flag staleness, keep conviction modest.")
    try:
        view = run_structured("macro_analyst", MacroView, ctx, skill_slug="macro-analyst")
        return MacroReport(as_of=as_of, **_clean(view))
    except Exception as exc:  # noqa: BLE001
        log.warning("macro analyst failed: %s", exc)
        return stub
