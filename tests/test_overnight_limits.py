"""生产 C3 与方案 A 的授权读取；历史 7.9 测试由 2026-10-08 新裁决更新。

原`test_overnight_limits.py` 断言隔夜单会被改限价、且审批卡显示 `@ 价格`。
任务 7.9 之后**这两条都不再成立**：参考价所需的行情数据未授权给 trader，
历史上自动下单整体停用、取价返回None；现在治理取价须绑定订单，生产 C3 保留。记录当前行为，
而不是把旧断言删掉了事——删掉测试会让「行为变了」这件事无人知晓。

保留文件名与 docstring 的历史说明，因为「为什么不再改限价」是这次停用的关键背景。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ats.schemas.decision import TradeDecision
from ats.trader import execute as texec


def _d(action="buy", **kw):
    # `notional_usd` defaults here rather than being passed positionally, so a test
    # can override it (`qty=10, notional_usd=None`) without colliding.
    kw.setdefault("notional_usd", 3000.0)
    return TradeDecision(symbol="GOOG", action=action,
                         order_type="market", rationale="pead", **kw)


def _auth(**kw):
    base = {"decision_hash": "dh", "approval_id": "ap", "cycle_id": "c",
            "revision_no": 0, "review_id": "rr", "review_at": "2026-10-06T08:00:00",
            "approval_at": "2026-10-06T08:01:00", "ruleset_version": "risk-v1",
            "portfolio_snapshot_id": "pf-1",
            "market_as_of": "2026-10-06T07:59:00"}
    return {**base, **kw}


# --------------------------------------------------------------------------- #
# 停用开关本身
# --------------------------------------------------------------------------- #

def test_auto_execution_is_off():
    assert texec.AUTO_EXECUTION_ENABLED is False


def test_the_disabled_reason_names_production_c3_and_simulation_policy():
    reason = texec.AUTO_EXECUTION_DISABLED_REASON
    assert "生产 C3" in reason and "FakeBroker" in reason


# --------------------------------------------------------------------------- #
# 停用后的行为：拒绝且有据，而非静默丢弃
# --------------------------------------------------------------------------- #

def test_place_orders_refuses_and_records_why(tmp_path, monkeypatch):
    """C3 的核心：禁掉下单，但**每个不成交都要有可见且有据的原因**。

    替代方案是让`_size` 算出 0 股从而静默不下单——那是丢弃，不是停用。
    """
    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "m.sqlite"))
    entries, fills = texec.place_orders(
        [(_d("buy"), 15.0)], "cycle-1", authorization=_auth())

    assert fills == []
    assert len(entries) == 1
    assert entries[0].status == "rejected"
    assert "auto execution disabled" in entries[0].error


def test_refusal_happens_before_the_broker_is_touched(monkeypatch):
    """授权齐全也拒绝——停用开关在授权门之前，且不构造任何券商客户端。"""
    def _explode(*_a, **_k):
        raise AssertionError("the broker must not be constructed while disabled")

    monkeypatch.setattr(texec, "IBKRBroker", _explode)
    monkeypatch.setattr(texec, "IBKRUnavailable", type("E", (Exception,), {}))

    entries, fills = texec.place_orders(
        [(_d("buy"), 15.0)], "cycle-x", authorization=_auth())
    assert fills == []
    assert entries[0].status == "rejected"


def test_every_order_gets_its_own_recorded_refusal():
    """三个标的三个理由行——不能只记第一条而丢掉其余的。"""
    entries, _ = texec.place_orders(
        [(_d("buy"), 15.0), (_d("sell"), 5.0), (_d("buy"), 1.0)],
        "cycle-3", authorization=_auth())
    assert len(entries) == 3
    assert all("auto execution disabled" in e.error for e in entries)


def test_order_sequence_is_preserved_so_the_refusal_is_reconcilable():
    """序号让「未成交」能与后续对账对上，与既有拒绝路径同形。"""
    entries, _ = texec.place_orders(
        [(_d("buy"), 15.0), (_d("sell"), 5.0)], "cycle-4", authorization=_auth())
    assert [e.order_seq for e in entries] == [0, 1]
    assert all(e.cycle_id == "cycle-4" for e in entries)


# --------------------------------------------------------------------------- #
# 参考价：不再取价
# --------------------------------------------------------------------------- #

def test_the_reference_price_is_no_longer_fetched(monkeypatch):
    """核心不变量：trader 不再触碰行情 provider。

    patch provider 为「被调用即失败」，而不是断言返回 None——后者在provider
    仍被调用、只是恰好失败时也会通过。
    """
    monkeypatch.setattr(
        texec, "_last_price_enabled",
        lambda s: pytest.fail(f"provider must not be reached: {s}"))
    with pytest.raises(PermissionError, match="binding_required"):
        texec._last_price("GOOG")


def test_amount_only_decisions_size_to_zero_and_say_so():
    """金额型决策在停用下换算不出股数。

    这是**已知且被接受的**副作用，也是停用而非改造的理由：若保留取价则要
    越权读取，若砍掉取价则金额决策无法下单。停用把两者都变成可见状态。
    """
    with pytest.raises(PermissionError, match="binding_required"):
        texec.size_decisions([_d("buy")])


def test_an_explicit_share_count_still_sizes_normally():
    """已给股数的决策不受影响——停用禁的是「自动下单」，不是「算股数」。"""
    sized = texec.size_decisions([_d("buy", qty=10, notional_usd=None)])
    assert sized[0][1] == 10.0


# --------------------------------------------------------------------------- #
# 隔夜限价改造：停用后不再改限价
# --------------------------------------------------------------------------- #

def test_unbound_overnight_order_is_refused_instead_of_market_fallback():
    with pytest.raises(PermissionError, match="binding_required"):
        texec.as_overnight_limits([_d("buy")], slippage_pct=0.5)


def test_an_explicit_limit_is_still_left_alone():
    """已自带限价的决策不受停用影响——限价是决策自带的，不是这里加的。"""
    d = TradeDecision(symbol="GOOG", action="buy", notional_usd=3000.0,
                      order_type="limit", limit_price=180.0, rationale="pead")
    out, notes = texec.as_overnight_limits([d])
    assert out[0].limit_price == 180.0
    assert notes == []


def test_unbound_price_cannot_produce_a_fictitious_approval_card():
    with pytest.raises(PermissionError, match="binding_required"):
        texec.as_overnight_limits([_d("buy")])


def test_the_preserved_implementation_exists_for_plan_a():
    """方案 A 恢复时要改的是这一处，故它必须仍在、可读、且当前无人调用。"""
    assert callable(texec._last_price_enabled)
    assert texec._last_price is not texec._last_price_enabled


def test_nothing_in_the_package_calls_the_preserved_implementation():
    """若有人日后调用了它，provider 就会重新被触达——这是停用被绕过的唯一路径。"""
    import subprocess
    from pathlib import Path

    from ats.config import REPO_ROOT

    hits = subprocess.run(
        ["grep", "-rn", "_last_price_enabled", "--include=*.py",
         str(REPO_ROOT / "src")],
        capture_output=True, text=True).stdout.strip().splitlines()
    callers = [line for line in hits if "def _last_price_enabled" not in line
               and "/trader/execute.py:" not in line]
    assert not callers, f"the preserved implementation is now called: {callers}"


# --------------------------------------------------------------------------- #
# 时间戳健全性（拒绝记录要可审计）
# --------------------------------------------------------------------------- #

def test_the_refusal_carries_a_timestamp():
    entries, _ = texec.place_orders(
        [(_d("buy"), 15.0)], "cycle-t", authorization=_auth())
    stamp = entries[0].submitted_at
    assert isinstance(stamp, datetime)
    assert stamp.tzinfo is not None
    assert stamp <= datetime.now(timezone.utc)