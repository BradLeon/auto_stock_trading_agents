"""Write the pre-registered plan for each proposed order.

One row per INTENT, written BEFORE the approval interrupt — the same audit-first rule
`persist_decision` already follows. That ordering is the whole point: a plan recorded
after the outcome is known is worthless, because it can no longer be wrong.

Of the first 52 order rows, 24 errored and 16 were cancelled. Orders evaporating (IBKR
down, approval declined, risk gate blocked) is this system's most common real outcome,
so the ledger records intents, not fills.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("ats.journal")

# Fallback risk units, used when the Chief declares no stop. Recorded in
# `risk_unit_source` so R-multiples from different denominators are never mixed.
_UNIT_EXPECTED_MOVE = "expected_move"      # option-implied 1σ event move (PEAD)
_UNIT_PORTFOLIO_STOP = "portfolio_stop"    # risk.stop_loss_pct, the implicit stop
_UNIT_DECLARED = "declared_stop"           # the Chief named a stop price


def resolve_risk_unit(decision, *, expected_move_pct: float | None,
                      stop_loss_pct: float, last_price: float | None = None
                      ) -> tuple[float | None, str]:
    """How much is at risk on this trade, and by which convention.

    Without a risk unit there is no R-multiple, and R is what makes trades of different
    sizes comparable. Three conventions, most specific first:

      1. a stop the Chief actually declared        -> distance to it
      2. an event trade                            -> option-implied expected move
      3. anything else                             -> the portfolio stop (25%)

    Returns (planned_risk_usd, source). None when notional is unknown — never invent a
    denominator, an R computed from a guess is worse than no R.
    """
    notional = decision.notional_usd
    if not notional and decision.qty and last_price:
        notional = decision.qty * last_price
    if not notional:
        return (None, "")

    ref = decision.limit_price or last_price
    if decision.stop_price and ref:
        frac = abs(ref - decision.stop_price) / ref
        if 0 < frac < 1:
            return (round(notional * frac, 2), _UNIT_DECLARED)
    if expected_move_pct and expected_move_pct > 0:
        return (round(notional * expected_move_pct / 100, 2), _UNIT_EXPECTED_MOVE)
    if stop_loss_pct and stop_loss_pct > 0:
        return (round(notional * stop_loss_pct, 2), _UNIT_PORTFOLIO_STOP)
    return (None, "")


def _evidence(store, symbol: str, fiscal_label: str) -> dict:
    """Evidence quality — this system's analogue of a human journal's "discipline" field.

    "Did I follow my plan?" is trivially yes for a machine. What varies is how good the
    evidence was: a transcript-less v1 score is the system's version of trading on a
    hunch, and the print→score latency says how stale the read was.
    """
    if not fiscal_label:
        return {}
    try:
        run = store.latest_score_run(symbol, fiscal_label)
    except Exception:  # noqa: BLE001 - evidence is best-effort
        return {}
    if not run:
        return {}
    return {"ev_score_total": run["total"], "ev_score_band": run["band"],
            "ev_has_transcript": run["has_transcript"],
            "ev_score_latency_h": run["latency_hours"]}


def _regime(store) -> dict:
    """Snapshot of the risk regime AT DECISION TIME.

    Denormalised on purpose: a later risk review must not retroactively rewrite what we
    believed when we acted.
    """
    try:
        review = store.latest_risk_review()
    except Exception:  # noqa: BLE001
        return {}
    if not review:
        return {}
    payload = review if isinstance(review, dict) else {}
    return {"regime_risk_state": payload.get("risk_state") or getattr(review, "risk_state", None)}


def record_intents(state, *, store=None) -> list[str]:
    """Write one journal_entries row per proposed decision. Returns the entry_ids.

    Never raises: the journal is an observer, and losing a ledger row must not abort a
    trading cycle.
    """
    from ..config import get_config, load_pead_config
    from ..memory import get_store

    store = store or get_store()
    ids: list[str] = []
    if not state.decisions:
        return ids

    stop_loss_pct = get_config().app.risk.stop_loss_pct
    event_data = state.event_data or {}
    regime = _regime(store)
    notes_json = json.dumps(list(state.risk_notes or []), ensure_ascii=False)
    # Reference prices from the portfolio already in memory — a declared stop needs one
    # to become a dollar amount, and fetching quotes here would add latency to the
    # approval path for something we already know.
    marks = {p.symbol: p.market_price for p in getattr(state.portfolio, "positions", [])
             or [] if p.market_price}

    for d in state.decisions:
        try:
            entry_id = store.client_order_id(state.cycle_id, d.symbol, d.action)
            em = (event_data.get(d.symbol) or {}).get("expected_move_pct")
            try:
                label = load_pead_config(d.symbol).fiscal_label
            except Exception:  # noqa: BLE001 - non-PEAD names have no config
                label = ""
            risk_usd, unit = resolve_risk_unit(
                d, expected_move_pct=em, stop_loss_pct=stop_loss_pct,
                last_price=marks.get(d.symbol))
            store.save_journal_entry({
                "entry_id": entry_id, "cycle_id": state.cycle_id,
                "as_of": state.as_of.isoformat(), "symbol": d.symbol, "action": d.action,
                "source": state.source, "setup": _setup_of(d, state.source),
                "intended_notional": d.notional_usd, "intended_qty": d.qty,
                "conviction": d.conviction, "order_type": d.order_type,
                "limit_price": d.limit_price, "stop_price": d.stop_price,
                "target_price": d.target_price,
                "planned_horizon_days": d.planned_horizon_days,
                "invalidation": d.invalidation,
                "planned_risk_usd": risk_usd, "risk_unit_source": unit,
                "rationale": d.rationale,
                "ev_expected_move_pct": em,
                "risk_notes_json": notes_json,
                **regime, **_evidence(store, d.symbol, label),
            })
            ids.append(entry_id)
        except Exception as exc:  # noqa: BLE001 - one row must not break the cycle
            log.warning("journal entry failed for %s: %s", d.symbol, exc)
    return ids


def _setup_of(decision, source: str) -> str:
    """Trust the Chief's declaration; fall back to the cycle source.

    Deliberately NOT inferred by regexing the Chinese free-text rationale — that would
    be wrong often enough to poison expectancy-per-setup, the most useful aggregate a
    journal produces.
    """
    if decision.setup and decision.setup != "unknown":
        return decision.setup
    return {"pead-chief": "pead_event", "manual": "manual"}.get(source, "unknown")


def record_outcome(state, *, store=None) -> None:
    """After execution, roll the order result up onto its ledger row."""
    from ..memory import get_store

    store = store or get_store()
    approval = state.approval
    for entry in state.order_results or []:
        try:
            entry_id = store.client_order_id(state.cycle_id, entry.symbol, entry.action)
            store.update_journal_outcome(entry_id, {
                "terminal_status": entry.status,
                "filled_qty": entry.qty if entry.status == "filled" else None,
                "avg_fill_price": entry.avg_fill_price,
                "approval_status": getattr(approval, "status", None),
                "approval_reviewer": getattr(approval, "reviewer", None),
                "approval_comment": getattr(approval, "comment", None),
                "approval_divergence": _divergence_json(approval, state.decisions),
                "slippage_bps": _slippage_bps(entry),
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("journal outcome failed for %s: %s", entry.symbol, exc)


def _divergence_json(approval, decisions) -> str | None:
    from ..trader.execute import approval_divergence

    div = approval_divergence(approval, decisions)
    return json.dumps(div, ensure_ascii=False) if div else None


def _slippage_bps(entry) -> float | None:
    """Signed cost of execution vs the limit we asked for, in basis points."""
    if not (entry.avg_fill_price and entry.limit_price):
        return None
    diff = entry.avg_fill_price - entry.limit_price
    if entry.action in ("sell", "trim"):
        diff = -diff                     # paying up is negative for a sell too
    return round(diff / entry.limit_price * 10_000, 1)
