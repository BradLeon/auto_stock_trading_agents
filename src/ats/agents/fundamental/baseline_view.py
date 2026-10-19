"""The pre-earnings baseline as a published projection detail (parent 7.5).

The legacy PEAD report carried sections 1.1-2.4 and 7 (narrative, peers, KPI watch list, valuation band, catalysts
and risks, the expectation table, consensus, market-implied expectations, the synthesis and the signal chain). The
Dispatcher path used to publish only a one-line narrative, so the Chief saw less than the old report did. This turns
the same dossier into the `baseline` field of `FundamentalExpectationUpdate`, size-capped so a baseline stays a
readable card rather than a transcript.
"""
from __future__ import annotations

_TEXT = 1800        # characters kept per long text field
_CELL = 360         # characters kept per expectation-table cell
_ITEMS = 8          # list entries kept per list field
_CHAIN = 10


def _clip(value, limit: int = _TEXT) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _clip_list(values, limit: int = _ITEMS, width: int = 400) -> list[str]:
    return [_clip(v, width) for v in list(values or [])[:limit] if str(v or "").strip()]


def baseline_detail(dossier, *, provenance: str = "") -> dict:
    """`dossier` is a PeadDossier whose expectation_set is the baseline; returns a `BaselineDetail` dict."""
    es = dossier.expectation_set
    bg = getattr(dossier, "fundamental_background", None)
    ms = getattr(dossier, "market_setup", None)
    weights = dict(getattr(dossier, "scorecard_weights", {}) or {})
    out: dict = {
        "built_at": es.as_of.isoformat(timespec="seconds"),
        "provenance": _clip(provenance, 600),
        "focus_ranking": _clip_list(es.focus_ranking),
        "synthesis": _clip(es.narrative),
        "valuation": _clip(es.valuation or (bg.valuation if bg else "")),
        "expectation_table": [
            {"dim_key": e.dim_key, "metric": _clip(e.metric, _CELL), "weight": weights.get(e.dim_key),
             "conservative": _clip(e.conservative, _CELL), "neutral": _clip(e.neutral, _CELL),
             "optimistic": _clip(e.optimistic, _CELL), "source": _clip(e.source, 160)}
            for e in es.expectations[:_ITEMS + 4]],
        "consensus": {
            "eps": es.consensus_eps, "eps_low": es.consensus_eps_low, "eps_high": es.consensus_eps_high,
            "revenue": es.consensus_revenue, "revenue_low": es.consensus_revenue_low,
            "revenue_high": es.consensus_revenue_high, "target_price": es.consensus_target_price,
            "rating_summary": _clip(es.consensus_rating_summary, 200),
            "recent_actions": _clip_list(es.consensus_recent_actions, 3, 200),
            "note": _clip(es.consensus_note, 300)},
    }
    if bg is not None:
        out.update({
            "core_narrative": _clip(bg.background),
            "peer_comparison": _clip(bg.peer_comparison),
            "watch_metrics": _clip(bg.watch_metrics),
            "catalysts": _clip_list(bg.catalysts),
            "key_risks": _clip_list(bg.key_risks),
            # The legacy report's "thesis-invalidating risks" are the falsifiers: what, if seen, breaks the baseline.
            "falsifiable_conditions": _clip_list(bg.key_risks)})
    if ms is not None:
        out["market_implied"] = {
            "reference_price": ms.pre_earnings_close, "expected_move_pct": ms.expected_move_pct,
            "atm_iv": ms.atm_iv, "iv_skew": ms.iv_skew, "run_up_vs_sector_pct": ms.run_up_vs_sector_pct,
            "run_up_vs_bench_pct": ms.run_up_vs_bench_pct, "dist_to_ath_pct": ms.dist_to_ath_pct,
            "notes": _clip_list(ms.notes, 4, 200)}
    out["signal_chain"] = [
        {"symbol": item.symbol, "role": item.role, "reported": item.reported,
         "price_chg_pct": item.price_chg_pct, "signal": _clip(item.signal, 240)}
        for item in list(getattr(dossier, "signal_chain", []) or [])[:_CHAIN]]
    out["signal_chain_summary"] = _clip(getattr(dossier, "signal_chain_summary", ""), 600)
    return out
