"""The Chief's context renders ledger state with its completeness, never as a bare fact (task 2.2)."""
from types import SimpleNamespace

from ats.agents.chief import assemble


def _input(status="complete", payload=None, gaps=(), as_of=("2026-10-28T14:00:00+00:00",)):
    return SimpleNamespace(status=status, payload=payload, gaps=list(gaps), source_as_of=list(as_of))


def test_an_unreconciled_ledger_is_called_out_in_the_context():
    portfolio = _input("partial", {"net_liquidation": 1_000_000.0}, gaps=["source_timestamp_or_reconciliation_missing"])
    history = _input("partial", gaps=['ledger_degraded:{"status":"degraded","unattributed_fills":2}'])
    text = assemble._ledger_block(portfolio, history)
    assert "账本未完整对账" in text
    assert "unattributed_fills" in text and "source_timestamp_or_reconciliation_missing" in text


def test_a_complete_ledger_carries_no_warning():
    portfolio = _input("complete", {"net_liquidation": 1_000_000.0})
    history = _input("complete", {"trades": [], "fills": []})
    text = assemble._ledger_block(portfolio, history)
    assert "账本未完整对账" not in text and "complete" in text


class _Store:
    def __init__(self, decisions):
        self._decisions = decisions

    def recent_decisions(self, limit=8):
        return self._decisions[:limit]


def test_track_record_marks_a_decision_executed_only_when_a_trade_carries_its_cycle_and_symbol():
    decisions = [
        {"cycle_id": "c1", "symbol": "COHR", "action": "buy", "notional_usd": 1000, "rationale": "a"},
        {"cycle_id": "c2", "symbol": "LITE", "action": "buy", "notional_usd": 500, "rationale": "b"}]
    history = _input("partial", {"trades": [{"cycle_id": "c1", "symbol": "COHR"}],
                                 "fills": [{"side": "buy", "symbol": "COHR", "shares": 10, "price": 50.0}],
                                 "performance": {"sharpe": 1.2}})
    text = assemble._track_record_block(_Store(decisions), history)
    assert "buy COHR $1,000（已下单）" in text
    assert "buy LITE $500（未下单）" in text
    assert "内部状态: partial" in text
    assert "近期成交: buy COHR 10@50.00" in text


# --- risk posture (the risk role's latest state reaches the Chief through HISTORY_DATA) ------------------- #
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)


def test_risk_posture_block_shows_the_directive_and_its_age():
    posture = {"as_of": NOW.isoformat(), "risk_state": "caution",
               "block": "RiskDirective=REPAIR_ONLY · 可增加风险=False\n禁止增加实体: COHR"}
    text = assemble._risk_posture_block(posture, NOW)
    assert "REPAIR_ONLY" in text and "COHR" in text and "天前" not in text


def test_a_stale_risk_posture_is_marked_stale_not_dropped():
    posture = {"as_of": (NOW - timedelta(days=5)).isoformat(), "risk_state": "normal", "block": "[风控] 状态=normal"}
    text = assemble._risk_posture_block(posture, NOW)
    assert "状态=normal" in text and "5 天前" in text


def test_missing_risk_review_is_stated():
    assert "风控尚无评审记录" in assemble._risk_posture_block(None, NOW)


def test_internal_state_carries_the_risk_posture_and_the_derisk_instruction():
    from ats.execution.state_api import _risk_posture
    from ats.memory import get_store
    from ats.schemas.risk import RiskReview

    store = get_store()
    assert _risk_posture(store) is None
    store.save_risk_review(RiskReview(as_of=NOW, risk_state="derisk"))
    posture = _risk_posture(store)
    assert posture["risk_state"] == "derisk" and posture["can_increase_risk"] is False
    assert "只允许减仓" in posture["block"]
    store.save_risk_review(RiskReview(as_of=NOW + timedelta(hours=1), risk_state="normal"))
    posture = _risk_posture(store)
    assert posture["can_increase_risk"] is True and "只允许减仓" not in posture["block"]


# --- the rendered input does not depend on how the payload was read (T0.6, 2026-10-29) -------------------- #
def test_ledger_and_performance_render_the_same_whatever_the_key_order():
    """A replay tape returns payloads key-sorted, a live read in insertion order. The
    Chief's context — and so the replay key of its model request — must not differ."""
    live = {"net_liquidation": 1_000_000.0, "daily_pnl": 2.0, "account_id": ""}
    replayed = dict(sorted(live.items()))
    perf_live = {"window_days": 1, "cumulative_pnl": 100.0, "benchmarks": {}}
    perf_replayed = dict(sorted(perf_live.items()))
    history = _input("complete", {"trades": [], "fills": []})
    assert assemble._ledger_block(_input("complete", live), history) == \
        assemble._ledger_block(_input("complete", replayed), history)
    a = assemble._track_record_block(_Store([]), _input("complete", {"performance": perf_live}))
    b = assemble._track_record_block(_Store([]), _input("complete", {"performance": perf_replayed}))
    assert a == b and '"cumulative_pnl": 100.0' in a
