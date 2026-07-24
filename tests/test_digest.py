"""Daily digests — render a structured .md + a thumbnail card from Context Memory."""

from datetime import datetime, timezone

from ats.memory import get_store
from ats.runtime import digest
from ats.schemas.memory import PerformanceRecord
from ats.schemas.risk import Breach, RiskReview

NOW = datetime.now(timezone.utc)


def test_perf_risk_digest_renders_md_and_card(monkeypatch):
    pushed = []
    monkeypatch.setattr(digest, "_push", lambda k, t, b: pushed.append((k, t, b)))

    store = get_store()
    store.save_performance(PerformanceRecord(
        cycle_id="c1", as_of=NOW, net_liquidation=200000, daily_pnl=1500, cumulative_pnl=8000))
    store.save_risk_review(RiskReview(
        as_of=NOW, risk_state="caution", portfolio_beta=1.7,
        breaches=[Breach(layer="L3-组合beta", limit="≤1.5", actual="1.7", action="block 加 beta")]))

    path = digest.perf_risk_digest()
    assert path is not None and path.exists()
    md = path.read_text(encoding="utf-8")
    assert "绩效风控" in md and "200,000" in md and "L3-组合beta" in md

    assert pushed, "a Feishu card should be pushed"
    kind, title, body = pushed[0]
    # card humanizes the breach: "L3-组合beta" -> "组合 beta（加权） 1.7，限额 ≤1.5"
    assert kind == "info" and "caution" in title and "组合 beta" in body and "≤1.5" in body


def test_digests_skip_gracefully_on_empty_store(monkeypatch):
    monkeypatch.setattr(digest, "_push", lambda *a: None)
    # nothing saved -> both return None, never raise
    assert digest.perf_risk_digest() is None
    assert digest.intel_digest() is None


def test_digest_respects_config_switch(monkeypatch):
    monkeypatch.setattr(digest, "_enabled", lambda key: key != "perf_risk")
    store = get_store()
    store.save_risk_review(RiskReview(as_of=NOW, risk_state="normal"))
    assert digest.perf_risk_digest() is None      # disabled -> skipped even with data
