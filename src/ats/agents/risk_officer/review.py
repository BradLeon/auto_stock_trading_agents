"""Risk-officer review: deterministic 6-layer assess -> one LLM narrative memo.

The figures always come from the deterministic engine (risk/assess.py). The LLM
only layers judgment; an LLM failure degrades to a numbers-only memo, never a crash.
"""

from __future__ import annotations

import logging

from ...schemas.risk import LayerConclusion, RiskMemo, RiskReview
from ..base import run_structured
from .outputs import RiskMemoLLMView

log = logging.getLogger("ats.agents.risk_officer.review")


def _context(review: RiskReview) -> str:
    from ...config import get_config
    from ...memory import get_store

    rc = get_config().app.risk
    limits = (
        f"硬限额: 单票≤{rc.max_position_pct:.0%} · 杠杆≤{rc.max_gross_leverage} · "
        f"现金地板≥{rc.cash_floor_pct:.0%} · beta≤{rc.beta_cap} · "
        f"相关簇≤{rc.cluster_weight_cap:.0%} · 回撤≤-{rc.max_drawdown_pct:.0%} · "
        f"日亏≤-{rc.daily_loss_limit_pct:.0%} · 压测≤-{rc.max_stress_loss_pct:.0%} · "
        f"事件≤{rc.max_event_loss_pct:.0%}")
    macro = ""
    try:
        mr = get_store().latest_macro_review()
        if mr is not None:
            macro = f"\n宏观 regime（参考）: {mr.regime}"
    except Exception:  # noqa: BLE001
        pass
    return (
        "以下是本组合的确定性 6 层风控读数（数值为准，勿改）：\n\n"
        f"{review.as_memo_context()}\n\n{limits}{macro}\n\n"
        "请据此产出风控官评估：总评、现金等价物真实可用弹药解读、逐层结论、距限额余量、"
        "可操作建议、最值得盯的风险点。全部用中文。")


def run(*, use_llm: bool = True) -> RiskMemo | None:
    """Snapshot -> assess -> persist -> LLM memo. Returns None if IBKR is unavailable."""
    from ...memory import get_store
    from ...risk import assess as risk_assess
    from ...trader import portfolio as tport

    pf = tport.snapshot()
    if pf is None:
        return None
    risk_assess.enrich_beta(pf)
    risk_assess.enrich_options(pf)
    review = risk_assess.assess(pf)
    get_store().save_risk_review(review)

    if not use_llm:
        return RiskMemo(as_of=review.as_of, assessment="(no-llm) 仅确定性读数", review=review)

    try:
        view: RiskMemoLLMView = run_structured("risk_officer", RiskMemoLLMView,
                                               _context(review), skill_slug="risk-officer")
    except Exception as exc:  # noqa: BLE001
        log.warning("risk-officer memo LLM failed: %s", exc)
        return RiskMemo(as_of=review.as_of,
                        assessment="(LLM 不可用 — 仅确定性读数，见下表)", review=review)

    return RiskMemo(
        as_of=review.as_of,
        assessment=view.assessment,
        cash_equivalent_read=view.cash_equivalent_read,
        layer_conclusions=[LayerConclusion(layer=lc.layer, conclusion=lc.conclusion)
                           for lc in view.layer_conclusions],
        headroom=view.headroom,
        recommended_actions=view.recommended_actions,
        top_risks=view.top_risks,
        review=review)
