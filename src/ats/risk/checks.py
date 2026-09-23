"""Pre-trade risk gate.

Phase B (design D6/D7/D15): risk reviews a WHOLE revision and returns a
structured verdict — it never rewrites a proposal. Any cap the proposal
exceeds becomes a violation plus an `allowed_boundary` the Chief can use to
draft the next revision. `pre_trade()` remains only as a deprecated adapter
for call sites not yet migrated to the authorization-gate path.

The evaluation unit is the entire revision: orders are projected sequentially
onto a working copy, so cross-order rules (industry-layer totals, correlation
clusters, portfolio state) judge the MERGED consequence — two orders that are
each individually inside a layer cap cannot sneak past in combination.
"""

from __future__ import annotations

from ..schemas.decision import (
    TradeDecision,
    UnknownActionError,
    is_increasing_action,
    normalize_action,
)
from ..schemas.instruments import normalize_symbol
from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import (DecisionRiskReview, OrderRiskVerdict,
                            AllowedBoundary, RiskDirective, RiskReview,
                            RiskViolation)
from . import assess as risk_assess
from . import marginal


# --------------------------------------------------------------------------- #
# Baseline (pre-revision) state
# --------------------------------------------------------------------------- #
def _baseline(portfolio: PortfolioSnapshot, *, sector: str,
              event_data: dict[str, dict] | None,
              review: RiskReview | None) -> tuple[PortfolioSnapshot, RiskReview, str]:
    """The pre-revision portfolio/review/state, computed without mutating inputs."""
    current_pf = portfolio.model_copy(deep=True)
    supplied_state = _supplied_state(review)
    if review is None or not review.economic_exposures:
        risk_assess.enrich_beta(current_pf)
        risk_assess.enrich_options(current_pf)
        current_review = risk_assess.assess(
            current_pf, sector=sector, event_data=event_data)
        if supplied_state in ("REPAIR_ONLY", "DATA_INVALID", "EMERGENCY"):
            current_review = current_review.model_copy(
                update={"directive": _override_directive(current_review,
                                                         supplied_state)})
    else:
        current_review = review
    return current_pf, current_review, _supplied_state(current_review)


# --------------------------------------------------------------------------- #
# Boundaries (reverse suggestions — the order is never rewritten)
# --------------------------------------------------------------------------- #
def _order_cap_boundary(rc, decision: TradeDecision) -> float | None:
    """Max notional the single-order cap accepts, or None when not binding."""
    return rc.max_single_order_usd


def _event_notional_boundary(decision: TradeDecision, portfolio: PortfolioSnapshot,
                             rc, event_data: dict[str, dict]) -> float | None:
    """Max notional the event-loss budget accepts for an increasing order."""
    if decision.notional_usd is None:
        return None
    from ..config import load_instrument_risk_registry

    meta = load_instrument_risk_registry().resolve(decision.symbol)
    em = (event_data.get(decision.symbol, {}).get("expected_move_pct")
          or event_data.get(meta.risk_symbol, {}).get("expected_move_pct"))
    # An action outside the vocabulary must fail the check, not fall through as
    # "nothing to clip" — that is how an unknown action would reach the broker.
    if not em or not is_increasing_action(decision.action,
                                          where="risk._event_notional_boundary") \
            or not portfolio.net_liquidation:
        return None
    return (rc.max_event_loss_pct * 100.0 / (em * meta.exposure_multiplier)
            * portfolio.net_liquidation)


# --------------------------------------------------------------------------- #
# The read-only revision review (§5.2/§5.3, design D6/D7/D15)
# --------------------------------------------------------------------------- #
def review_revision(
    orders: list[TradeDecision], portfolio: PortfolioSnapshot | None, *,
    sector: str = "ai_hardware", event_data: dict[str, dict] | None = None,
    review: RiskReview | None = None, apply_caps: bool = True,
    llm_comment: str | None = None,
) -> DecisionRiskReview:
    """Review one revision without modifying it. Deterministic fields decide.

    Returns per-order verdicts, structured violations (each with the boundary
    a revised proposal may use), the allowed boundary, and risk metrics
    before/after the merged consequence. Narrative text (`llm_comment`) is
    recorded verbatim and can never flip the verdict (§5.2 第 4 条).
    """
    from ..config import get_config, load_risk_policy

    notes: list[str] = []
    violations: list[RiskViolation] = []
    order_verdicts: list[OrderRiskVerdict] = []
    per_symbol_max: dict[str, float] = {}

    def _reject(symbol: str, action: str, reasons: list[str],
                max_allowed: float | None = None) -> None:
        order_verdicts.append(OrderRiskVerdict(
            symbol=symbol, action=action, verdict="rejected", reasons=reasons,
            max_allowed_notional=max_allowed))
        for reason in reasons:
            notes.append(f"BLOCK {symbol}: {reason}")

    if portfolio is None:
        # Fail closed: without a portfolio the post-trade state cannot be
        # derived, and "cannot judge" must reject, not skip (§5.3 不可判定).
        for d in orders:
            _reject(d.symbol, str(d.action), ["无组合快照，无法判定交易后风险"])
        violations.append(RiskViolation(
            rule_id="no_portfolio", severity="hard",
            detail="无组合快照 — 无法推导交易后仓位，整条修订驳回"))
        notes.append("STATE FAIL-CLOSED: 无组合快照，修订整体驳回")
        return DecisionRiskReview(
            verdict="rejected", order_verdicts=order_verdicts,
            violations=violations,
            allowed_boundary=AllowedBoundary(), notes=notes)

    rc = get_config().app.risk
    policy = load_risk_policy()
    event_data = event_data or {}
    working_pf, working_review, effective_state = _baseline(
        portfolio, sector=sector, event_data=event_data, review=review)
    if effective_state in ("REPAIR_ONLY", "DATA_INVALID", "EMERGENCY"):
        notes.append(
            f"STATE {effective_state}: de-risk/repair-only，"
            "订单必须净改善且不得恶化其他风险指标")

    def _metrics(r: RiskReview) -> dict[str, float]:
        return {k: round(v, 9) for k, v in
                marginal.risk_utilizations(r, rc, policy).items()}

    before_metrics = _metrics(working_review)

    for decision in orders:
        sym = decision.symbol
        action = str(decision.action)
        # An action outside the vocabulary must be blocked, never read as "no
        # risk to check" — that reading is how an unknown action reaches the broker.
        try:
            normalize_action(decision.action, where="risk.review_revision")
        except UnknownActionError as exc:
            violations.append(RiskViolation(
                rule_id="action_vocabulary", entity=sym, actual=str(decision.action),
                detail=str(exc)))
            _reject(sym, action, [str(exc)])
            continue

        # Cap boundaries: report, never rewrite. The binding rule is whichever
        # boundary is tighter.
        cap = _order_cap_boundary(rc, decision) if apply_caps else None
        event_cap = _event_notional_boundary(decision, working_pf, rc, event_data)
        binding = [c for c in (cap, event_cap) if c is not None]
        max_allowed = min(binding) if binding else None
        if (max_allowed is not None and decision.notional_usd is not None
                and decision.notional_usd > max_allowed):
            if event_cap is not None and event_cap <= (cap if cap is not None else float("inf")):
                rule_id, limit = "max_event_loss_pct", event_cap
            else:
                rule_id, limit = "max_single_order_usd", cap
            violations.append(RiskViolation(
                rule_id=rule_id, entity=sym, limit=round(limit, 2),
                actual=decision.notional_usd, severity="hard",
                detail=f"提案 ${decision.notional_usd:,.0f} 超出可接受上限 ${limit:,.0f}（反向建议，未裁剪）"))
            if max_allowed not in per_symbol_max or max_allowed < per_symbol_max[sym]:
                per_symbol_max[sym] = max_allowed
            _reject(sym, action,
                    [f"{rule_id}: ${decision.notional_usd:,.0f} → 可接受上限 ${max_allowed:,.0f}"],
                    max_allowed=max_allowed)
            continue

        post_pf = marginal.project_trade(working_pf, decision)
        if post_pf.model_dump() == working_pf.model_dump():
            violations.append(RiskViolation(
                rule_id="underivable_post_trade", entity=sym, severity="hard",
                detail="无法从订单字段推导交易后仓位"))
            _reject(sym, action, ["无法从订单字段推导交易后仓位"])
            continue
        risk_assess.enrich_beta(post_pf)
        risk_assess.enrich_options(post_pf)
        post_review = risk_assess.assess(
            post_pf, sector=sector, event_data=event_data)
        verdict = marginal.compare(sym, working_review, post_review, rc, policy)
        if not verdict.allowed:
            violation_count_before = len(violations)
            for delta in verdict.deltas:
                if delta.new_breach:
                    violations.append(RiskViolation(
                        rule_id=f"new_breach:{delta.metric}", entity=sym,
                        limit=1.0, actual=round(delta.after_utilization, 6),
                        severity="hard",
                        detail=f"合并后果产生新破限: {delta.metric} "
                               f"{delta.before_utilization:.2f}→{delta.after_utilization:.2f}"))
                    if delta.metric.startswith("layer:"):
                        bound = _layer_increment_boundary(
                            delta.metric.removeprefix("layer:"), post_review,
                            decision)
                        if bound is not None:
                            per_symbol_max[sym] = (
                                min(per_symbol_max.get(sym, bound), bound))
                elif effective_state in ("REPAIR_ONLY", "DATA_INVALID", "EMERGENCY") \
                        and delta.worsened:
                    violations.append(RiskViolation(
                        rule_id="state_blocks_increase", entity=sym,
                        limit=delta.before_utilization,
                        actual=round(delta.after_utilization, 6), severity="hard",
                        detail=f"修复态不得恶化: {delta.metric}"))
            # String-form breaches that the utilization diff cannot see
            # (correlation clusters, group caps): any layer newly breached by
            # the merged consequence is a structured violation.
            pre_layers = {b.layer for b in working_review.breaches}
            for b in post_review.breaches:
                if b.layer in pre_layers:
                    continue
                violations.append(RiskViolation(
                    rule_id=f"breach:{b.layer}", entity=sym,
                    limit=b.limit, actual=b.actual, severity="hard",
                    detail=f"合并后果触发 {b.layer}: {b.actual} vs {b.limit} → {b.action}"))
                if b.layer.startswith("L3-相关簇") and post_review.clusters:
                    bound = _cluster_increment_boundary(
                        post_review, decision, rc)
                    if bound is not None:
                        per_symbol_max[sym] = (
                            min(per_symbol_max.get(sym, bound), bound))
            if len(violations) == violation_count_before:
                # Rejected by the marginal policy without a metric delta (e.g.
                # "repair mode must improve a breached metric") — still a hard
                # violation: a rejection with no record would look like a pass.
                violations.append(RiskViolation(
                    rule_id="marginal_policy", entity=sym, severity="hard",
                    detail="; ".join(verdict.reasons)))
            _reject(sym, action, list(verdict.reasons),
                    max_allowed=per_symbol_max.get(sym))
            continue
        notes.append(f"ALLOW {sym}: marginal={verdict.classification}")
        order_verdicts.append(OrderRiskVerdict(
            symbol=sym, action=action, verdict="approved"))
        # Only approved orders advance the working state: the merged
        # consequence is what would actually execute.
        working_pf = post_pf
        working_review = post_review

    if llm_comment:
        # Recorded as an observable exception; it never changes the verdict.
        notes.append(f"LLM_COMMENT (记录，不改变判定): {llm_comment}")

    blocked = sorted({ov.action for ov in order_verdicts
                      if ov.verdict == "rejected"})
    max_additional = min(per_symbol_max.values()) if per_symbol_max else None
    after_metrics = (_metrics(working_review) if order_verdicts
                     else dict(before_metrics))
    return DecisionRiskReview(
        verdict="rejected" if violations else "approved",
        order_verdicts=order_verdicts, violations=violations,
        allowed_boundary=AllowedBoundary(
            max_additional_notional=round(max_additional, 2)
            if max_additional is not None else None,
            blocked_actions=blocked,
            per_symbol_max_notional={k: round(v, 2)
                                     for k, v in per_symbol_max.items()}),
        before_metrics=before_metrics, after_metrics=after_metrics,
        pre_state=effective_state,
        post_state=_supplied_state(working_review) if order_verdicts else effective_state,
        notes=notes, llm_comment=llm_comment or "")


# --------------------------------------------------------------------------- #
# Deprecated transitional adapter (design D6)
# --------------------------------------------------------------------------- #
def pre_trade(decisions: list[TradeDecision], portfolio: PortfolioSnapshot | None, *,
              sector: str = "ai_hardware", event_data: dict[str, dict] | None = None,
              review: RiskReview | None = None, apply_base: bool = True
              ) -> tuple[list[TradeDecision], list[str], RiskReview | None]:
    """DEPRECATED: old clipping-shaped contract over the new read-only review.

    Kept ONLY so call sites not yet migrated (graph/chief, graph/pead,
    runtime/cli) keep running during the transition — it is registered for
    retirement in `config/workflow/legacy_retirement.yaml` and its exit
    condition is "all call sites consume `review_revision` results". The
    returned decisions are the ORIGINAL, unmodified objects: risk no longer
    clips anything. Orders that fail are absent from the approved list, with
    the boundary they exceeded recorded in the notes.
    """
    import logging

    logging.getLogger("ats.risk.checks").warning(
        "pre_trade() is deprecated; migrate call sites to review_revision()")
    # Legacy contract point: without a portfolio the old path skipped checks.
    # The adapter preserves that for un-migrated call sites; the NEW entry
    # (`review_revision`) fails closed instead. The skip dies with the adapter.
    if portfolio is None:
        return decisions, ["(no live portfolio — risk checks skipped)"], None
    result = review_revision(
        decisions, portfolio, sector=sector, event_data=event_data,
        review=review, apply_caps=apply_base)
    approved = [d for d, ov in zip(decisions, result.order_verdicts)
                if ov.verdict == "approved"]
    initial_review: RiskReview | None = None
    if portfolio is not None:
        _, initial_review, _ = _baseline(portfolio, sector=sector,
                                         event_data=event_data, review=review)
    return approved, result.notes, initial_review


def _supplied_state(review: RiskReview | None) -> str:
    if review is None:
        return "NORMAL"
    if review.directive:
        return review.directive.state
    return "EMERGENCY" if review.risk_state == "derisk" else "NORMAL"


def _override_directive(review: RiskReview, state: str) -> RiskDirective:
    base = review.directive or RiskDirective()
    return base.model_copy(update={
        "state": state,
        "can_increase_risk": False,
        "allowed_actions": ["reduce", "hedge_if_verified_improving"],
    })


def _layer_increment_boundary(layer_key: str, post_review: RiskReview,
                              decision: TradeDecision) -> float | None:
    """Max notional this order could have while keeping the layer under its cap."""
    layer = next((l for l in post_review.chain_layers if l.key == layer_key), None)
    if layer is None or layer.cap is None or not post_review.net_liquidation \
            or not layer.cap:
        return None
    order_weight = (decision.notional_usd or 0.0) / post_review.net_liquidation
    pre_weight = layer.weight - order_weight
    return max((layer.cap - pre_weight) * post_review.net_liquidation, 0.0)


def _cluster_increment_boundary(post_review: RiskReview, decision: TradeDecision,
                                rc) -> float | None:
    """Max notional this order could have while keeping the top cluster under cap."""
    if not post_review.clusters or not post_review.net_liquidation:
        return None
    top = post_review.clusters[0]
    order_weight = (decision.notional_usd or 0.0) / post_review.net_liquidation
    pre_weight = top.weight - order_weight
    return max((rc.cluster_weight_cap - pre_weight) * post_review.net_liquidation, 0.0)
