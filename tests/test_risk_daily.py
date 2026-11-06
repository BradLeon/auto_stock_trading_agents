"""风控日报: the book against the rules, in plain language; deterministic, read-only, never fails what asked for it."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ats.agents.risk_officer import daily
from ats.config import get_config
from ats.memory import get_store
from ats.schemas.risk import Breach, Cluster, EconomicExposure, RiskDirective, RiskReview, StressResult

NOW = datetime.now(timezone.utc)


def _review(**kw) -> RiskReview:
    base = dict(
        as_of=NOW, net_liquidation=200_000, effective_cash_pct=-0.60, effective_leverage=0.96, portfolio_beta=2.09,
        drawdown_pct=-22.7, daily_pnl_pct=-1.0,
        economic_exposures=[EconomicExposure(economic_entity="QQQ", label="QQQ", risk_symbol="QQQ",
                                             net_delta_weight=0.40)],
        clusters=[Cluster(members=["MU", "LITE"], weight=0.6, avg_corr=0.8)],
        stress=[StressResult(scenario="市场 -20%", loss_pct=-30.0)],
        breaches=[Breach(layer="L4-回撤", limit="≥-15%", actual="-22.7%", action="de-risk：block 所有新买")],
        directive=RiskDirective(state="EMERGENCY", can_increase_risk=False, allowed_actions=["reduce"],
                                blocked_entities=["QQQ"], risk_budget_remaining={"portfolio_beta": 0.0}))
    base.update(kw)
    return RiskReview(**base)


def _data(store, review, reviews=()):
    return {"now": NOW, "rc": get_config().app.risk, "review": review, "source": "live", "note": "",
            "rows": daily.limit_rows(review, get_config().app.risk), "layers": [], "reviews": list(reviews)}


def test_every_limit_shows_how_much_of_it_is_used():
    rows = {r["name"]: r for r in daily.limit_rows(_review(), get_config().app.risk)}
    assert rows["单个标的最大净敞口"]["used"] > 1.0 and rows["单个标的最大净敞口"]["note"] == "QQQ"
    assert rows["最大回撤"]["used"] > 1.0 and rows["有效现金占比"]["used"] > 1.0       # borrowing past the -50% floor
    assert daily._status(rows["最大相关簇权重"]["used"]) in ("正常", "接近")


def test_the_report_states_the_state_the_breaches_and_what_to_do():
    text = daily.render(_data(get_store(), _review()))
    assert "**EMERGENCY**" in text and "不可以增加风险" in text and "不得增加的标的：QQQ" in text
    assert "**破限**" in text and "L4-回撤" in text
    assert "1. L4-回撤 破限" in text and "风险状态是 EMERGENCY" in text
    assert "规则全文" in text


def test_a_clean_book_asks_for_nothing():
    clean = _review(breaches=[], effective_cash_pct=0.3, effective_leverage=0.5, portfolio_beta=1.0,
                    drawdown_pct=-2.0, stress=[StressResult(scenario="s", loss_pct=-8.0)],
                    economic_exposures=[EconomicExposure(economic_entity="X", label="X", risk_symbol="X",
                                                         net_delta_weight=0.1)],
                    clusters=[Cluster(members=["A"], weight=0.2)],
                    directive=RiskDirective(state="NORMAL", allowed_actions=["reduce"]))
    d = _data(get_store(), clean)
    assert daily.attention(d) == [] and "无。" in daily.render(d)


def test_the_gates_recent_verdicts_and_their_rules_are_listed():
    store = get_store()
    orders = [{"action": "buy", "symbol": "MU", "notional_usd": 20000}]
    violations = [{"rule_id": "fundamental_uncovered", "entity": "MU", "detail": "没有新近基本面研究"}]
    store.conn.execute(
        "INSERT INTO decision_revisions (cycle_id, revision_no, decision_hash, orders_json, created_at) "
        "VALUES ('c-risk-daily', 1, 'h1', ?, ?)", (json.dumps(orders), NOW.isoformat()))
    store.conn.execute(
        "INSERT INTO decision_risk_reviews (review_id, cycle_id, revision_no, decision_hash, ruleset_version, "
        "portfolio_snapshot_id, market_as_of, verdict, violations_json, notes, created_at) "
        "VALUES ('r1', 'c-risk-daily', 1, 'h1', 'v', 'pf', 'm', 'rejected', ?, '', ?)",
        (json.dumps(violations), NOW.isoformat()))
    store.conn.commit()
    got = daily.recent_reviews(store, now=NOW + timedelta(minutes=1))
    assert got and got[0]["verdict"] == "rejected" and got[0]["orders"][0]["symbol"] == "MU"
    text = daily.render(_data(store, _review(), got))
    assert "c-risk-daily" in text and "驳回" in text and "fundamental_uncovered" in text and "BUY MU" in text
    assert any("被风控驳回" in item for item in daily.attention(_data(store, _review(), got)))


def test_publish_never_raises(monkeypatch):
    monkeypatch.setattr(daily, "collect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert daily.publish(push=False) == {"error": "boom"}
