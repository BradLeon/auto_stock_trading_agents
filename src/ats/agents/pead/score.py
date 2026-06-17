"""Post-earnings PEAD agents: actuals extraction, Surprise Scorecard, decision.

Division of labor (same philosophy as risk_validator): the LLM judges semantics
(actual vs expected per dimension), but the WEIGHTING, THRESHOLD BANDS, and the
DECISION TREE are deterministic code — auditable and not left to the model.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ...schemas.decision import TradeDecision
from ...schemas.pead import (
    ActualMetric,
    Actuals,
    ExpectationSet,
    PeadConfig,
    Scorecard,
    ScorecardLine,
)
from ...schemas.portfolio import PortfolioSnapshot
from ..base import run_structured
from .outputs import ActualsView, ScoresView

log = logging.getLogger("ats.agents.pead")


# --------------------------------------------------------------------------- #
# Actuals extraction (LLM from transcript + reported financials)
# --------------------------------------------------------------------------- #
def extract_actuals(config: PeadConfig, expectations: ExpectationSet | None,
                    transcript_text: str, fundamentals_text: str, as_of: datetime,
                    transcript_source: str = "", documents_text: str = "") -> Actuals:
    exp_lines = ""
    if expectations:
        exp_lines = "\n".join(
            f"  - {e.dim_key}: neutral={e.neutral}" for e in expectations.expectations)
    # Opus has ample context; keep enough of each source to reach guidance + segments.
    transcript_block = transcript_text[:40000] if transcript_text else "(no transcript)"
    docs_block = documents_text[:40000] if documents_text else "(no official docs)"
    ctx = (
        f"Extract Q actuals for {config.symbol} ({config.fiscal_label}).\n"
        f"Expectations (neutral case) per dimension:\n{exp_lines}\n\n"
        f"Reported financials (statements + QoQ/YoY):\n{fundamentals_text}\n\n"
        f"Official documents (SEC 8-K earnings release + investor presentation):\n{docs_block}\n\n"
        f"Earnings call transcript:\n{transcript_block}\n\n"
        "Using ALL of the above (press release + presentation carry segment QoQ/YoY, guidance, "
        "and market commentary), give per scorecard dimension the actual value, a 'vs_expected' "
        "tag (远超/超/中性/低于/远低于 + 🔴/✅/⚪/⚠️) and a note. Extract forward GUIDANCE and key "
        "qualitative call signals separately. Set reported_eps / reported_revenue if present."
    )
    try:
        view: ActualsView = run_structured("manager", ActualsView, ctx, skill_slug="pead-actuals")
        return Actuals(
            symbol=config.symbol, fiscal_label=config.fiscal_label, as_of=as_of,
            reported_eps=view.reported_eps, reported_revenue=view.reported_revenue,
            metrics=[ActualMetric(dim_key=m.dim_key, metric=m.metric, actual=m.actual,
                                  vs_expected=m.vs_expected, note=m.note) for m in view.metrics],
            guidance=view.guidance, transcript_signals=view.transcript_signals,
            transcript_source=transcript_source)
    except Exception as exc:  # noqa: BLE001
        log.warning("pead actuals failed for %s: %s", config.symbol, exc)
        return Actuals(symbol=config.symbol, fiscal_label=config.fiscal_label, as_of=as_of,
                       transcript_source=transcript_source,
                       guidance="[fallback] actuals extraction unavailable")


# --------------------------------------------------------------------------- #
# Surprise Scorecard (LLM scores per dim; code weights + bands)
# --------------------------------------------------------------------------- #
def score(config: PeadConfig, expectations: ExpectationSet | None, actuals: Actuals,
          as_of: datetime) -> Scorecard:
    exp_by = {e.dim_key: e for e in (expectations.expectations if expectations else [])}
    act_by = {m.dim_key: m for m in actuals.metrics}
    dim_lines = []
    for d in config.scorecard_dims:
        e = exp_by.get(d.key)
        a = act_by.get(d.key)
        dim_lines.append(
            f"  - {d.key} ({d.label}): neutral={e.neutral if e else 'n/a'} | "
            f"actual={a.actual if a else 'n/a'} ({a.vs_expected if a else ''})")
    ctx = (
        f"Score each dimension for {config.symbol} on a -2..+2 scale: -2 far below, 0 in line, "
        f"+2 far above the NEUTRAL expectation. Be calibrated and skeptical — 'in line' is 0, "
        f"not positive.\n" + "\n".join(dim_lines) +
        "\n\nReturn one item per dim_key with score and a one-line note.")

    scores: dict[str, tuple[float, str]] = {}
    try:
        view: ScoresView = run_structured("manager", ScoresView, ctx, skill_slug="pead-scorer")
        for it in view.items:
            scores[it.dim_key] = (max(-2.0, min(2.0, float(it.score))), it.note)
    except Exception as exc:  # noqa: BLE001
        log.warning("pead scorer failed for %s: %s", config.symbol, exc)

    lines, total = [], 0.0
    for d in config.scorecard_dims:
        s, note = scores.get(d.key, (0.0, "no score"))
        weighted = s * d.weight
        total += weighted
        lines.append(ScorecardLine(dim_key=d.key, label=d.label, weight=d.weight,
                                   score=s, weighted=round(weighted, 4), note=note))
    total = round(total, 4)
    return Scorecard(symbol=config.symbol, fiscal_label=config.fiscal_label, as_of=as_of,
                     lines=lines, total=total, threshold=config.long_threshold,
                     band=_band(total, config.long_threshold))


def _band(total: float, threshold: float) -> str:
    if total >= threshold:
        return f"达到做多门槛 (≥{threshold:+.1f})"
    if total >= 0.5:
        return f"温和正面但未达门槛 (<{threshold:+.1f})"
    if total >= -0.5:
        return "中性观望"
    return "负面"


# --------------------------------------------------------------------------- #
# Decision tree (pure, deterministic) — mirrors the doc's scenario table
# --------------------------------------------------------------------------- #
def decide(config: PeadConfig, scorecard: Scorecard, run_up_vs_sector: float | None,
           portfolio: PortfolioSnapshot | None, net_liquidation: float,
           small_long_pct: float = 0.03, trim_fraction: float = 0.30,
          ) -> tuple[list[TradeDecision], str, str]:
    """Return (decisions, scenario_band, rationale). Action is fully deterministic."""
    total = scorecard.total
    thr = config.long_threshold
    held_qty = _held_qty(portfolio, config.symbol)
    holding = held_qty > 0
    run_up = run_up_vs_sector

    # Cleared the (ticker-specific) long bar.
    if total >= thr:
        if run_up is not None and run_up > config.run_up_warn_pct:
            return ([], "做多门槛达成但抢跑透支→观望",
                    f"总分 {total:+.2f} ≥ 门槛 {thr:+.1f}，但财报前 20 日相对 {config.sector_etf} "
                    f"抢跑 +{run_up:.1f}%（>{config.run_up_warn_pct:.0f}% 警戒），透支风险高，观望。")
        notional = round(small_long_pct * net_liquidation, 0)
        d = TradeDecision(symbol=config.symbol, action="buy", notional_usd=notional,
                          order_type="market", conviction=min(1.0, total / max(thr, 0.5)),
                          rationale=f"Scorecard {total:+.2f} ≥ 门槛 {thr:+.1f}，抢跑可控；小仓位试探做多。")
        return ([d], "达成门槛→小仓位做多", d.rationale)

    # Below the long bar.
    if holding and total < thr:
        # Beat-but-not-enough / weak: de-risk per the doc's "分步减仓".
        qty = round(held_qty * trim_fraction)
        if qty >= 1:
            d = TradeDecision(symbol=config.symbol, action="trim", qty=float(qty),
                              order_type="market", conviction=0.5,
                              rationale=f"Scorecard {total:+.2f} 未达门槛 {thr:+.1f}，已持仓 → "
                                        f"减仓 {trim_fraction:.0%} 锁定，保留核心仓位。")
            return ([d], "未达门槛+持仓→分步减仓", d.rationale)

    if total <= -0.5:
        return ([], "负面但不做空(订单簿支撑)",
                f"总分 {total:+.2f} 偏负，但长期订单/预订支撑下行，不做空。")

    return ([], scorecard.band,
            f"总分 {total:+.2f} 未达门槛 {thr:+.1f}，无明确 edge，观望。")


def _held_qty(portfolio: PortfolioSnapshot | None, symbol: str) -> float:
    if not portfolio:
        return 0.0
    for p in portfolio.positions:
        if p.symbol == symbol:
            return p.qty
    return 0.0
