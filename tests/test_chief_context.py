"""The Chief's context renders ledger state with its completeness, never as a bare fact (task 2.2)."""
from types import SimpleNamespace

from datetime import datetime, timezone

from ats.agents.chief import assemble

NOW = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)


def _input(status="complete", payload=None, gaps=(), as_of=("2026-10-28T14:00:00+00:00",)):
    return SimpleNamespace(status=status, payload=payload, gaps=list(gaps), source_as_of=list(as_of))


BOOK = {"as_of": "2026-10-28T14:00:00+00:00", "net_liquidation": 252_613.0, "cash": -500.0,
        "daily_pnl": -6_012.0, "realized_pnl": 0.0, "gross_exposure": 200_000.0,
        "net_exposure": 180_000.0, "leverage": 0.8, "maint_margin": 77_553.0,
        "init_margin": 80_764.0, "excess_liquidity": 171_513.0, "margin_source": "ibkr",
        "positions": [
            {"symbol": "TSM", "sec_type": "STK", "qty": 100.0, "avg_cost": 300.0,
             "market_price": 343.0, "market_value": 34_300.0, "unrealized_pnl": 4_300.0, "weight": 0.136},
            {"symbol": "AXTI", "sec_type": "OPT", "qty": -5.0, "avg_cost": 480.31, "market_price": 2.17,
             "market_value": -1_085.0, "unrealized_pnl": 1_316.0, "weight": -0.004, "right": "P",
             "strike": 50.0, "expiry": "20261120", "multiplier": 100.0, "delta": -0.28}]}


def test_the_live_book_lists_account_values_and_every_position():
    text = assemble._ledger_block(_input("complete", BOOK))
    assert "TWS 实时" in text and "净值 $252,613" in text and "维持 $77,553" in text
    assert "TSM: 100 @ 成本 300.00" in text
    # an option's IBKR average cost is per contract; it is shown per share like its price
    assert "AXTI 20261120 50P ×100 [空头] Δ=-0.28: -5 @ 成本 4.80" in text
    # options are listed apart from stock, so a put is never read as a share holding
    assert text.index("正股及其他 1 项") < text.index("TSM:") < text.index("期权 1 项") < text.index("AXTI")


def test_an_unavailable_book_is_stated_and_nothing_stands_in_for_it():
    text = assemble._ledger_block(_input("unavailable", None, gaps=["broker_required_for_account_state"]))
    assert "账户实时状态不可用" in text and "broker_required_for_account_state" in text
    assert "净值" not in text


def test_an_unreconciled_ledger_is_called_out_beside_the_track_record():
    history = _input("partial", {"trades": [], "fills": []},
                     gaps=['ledger_degraded:{"status":"degraded","unattributed_fills":2}'])
    text = assemble._track_record_block(_Store([]), history)
    assert "账本未完整对账" in text and "unattributed_fills" in text


def test_performance_states_its_window_and_basis():
    perf = {"nav_points": 14, "nav_first_as_of": "2026-07-17T02:00:00+00:00",
            "nav_last_as_of": "2026-08-31T15:00:00+00:00", "total_return_pct": 7.77,
            "max_drawdown_pct": -12.45, "closed_episodes": 38, "closed_wins": 20,
            "closed_losses": 15, "closed_flat": 3, "realized_total": 9_000.0,
            "win_rate": 0.571, "profit_factor": 1.8}
    history = SimpleNamespace(status="complete", payload={"performance": perf}, gaps=[],
                              source_as_of=[], queried_at=NOW)
    text = assemble._track_record_block(_Store([]), history)
    assert "14 个快照点 2026-07-17 → 2026-08-31" in text and "未扣出入金" in text
    assert "净值快照止于 2026-08-31（57 天前）" in text
    assert "已平仓回合 38（盈 20 / 亏 15 / 持平 3）" in text and "胜率 57%" in text
    assert "美元回合已实现合计 $9,000" in text


def test_non_usd_round_trips_are_listed_in_their_own_currency():
    perf = {"nav_points": 0, "closed_episodes": 2, "realized_total": 100.0,
            "realized_other_currencies": {"HKD": 17_744.39}}
    text = assemble._track_record_block(_Store([]), _input("complete", {"performance": perf}))
    assert "HKD 回合已实现 +17,744（当地货币，未换算）" in text


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
    replayed = dict(sorted(BOOK.items()))
    perf_live = {"nav_points": 1, "total_return_pct": 1.0, "closed_episodes": 0, "benchmarks": {}}
    perf_replayed = dict(sorted(perf_live.items()))
    assert assemble._ledger_block(_input("complete", BOOK)) == \
        assemble._ledger_block(_input("complete", replayed))
    a = assemble._track_record_block(_Store([]), _input("complete", {"performance": perf_live}))
    b = assemble._track_record_block(_Store([]), _input("complete", {"performance": perf_replayed}))
    assert a == b and "区间收益 +1.00%" in a
