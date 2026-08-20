"""配置结论 → 预算使用率 → basket 权重。

这条链上唯一不能松的是**方向**：使用率只允许把本层预算**往下调**。
weight_cap 是风控配置定的天花板，层级分析师只能决定在护栏内用多少，不能抬高护栏。
"""

import pytest

from ats.agents.sector import cross_section as cs
from ats.schemas.sector import SectorLayer


def _rows(n=4):
    rows = []
    for i in range(n):
        rows.append(cs.FactorRow(symbol=f"S{i}", market_cap=5e10, beta=1.0,
                                 rev_growth=0.20 + 0.05 * i, gross_margin=0.40,
                                 op_margin=0.20, fwd_pe=20.0, mom_60d=5.0,
                                 rating_delta=0.1))
    return rows


def _layer(cap=0.30):
    return SectorLayer(key="L6_memory", label="存储", weight_cap=cap,
                       tickers=[{"symbol": "MU"}])


# --------------------------------------------------------------------------- #
# 使用率映射
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("allocation,expected", [
    ("超配", 1.0), ("标配", 0.6), ("低配", 0.3), ("清仓", 0.0)])
def test_allocation_maps_to_a_budget_share(allocation, expected):
    assert cs.utilization_for(allocation) == expected


def test_underweight_shrinks_the_budget_but_keeps_the_ceiling():
    # weight_cap 30% + 低配(30%) → basket 合计 ≈9% NAV
    assert cs.budget_for(_layer(0.30), "低配") == pytest.approx(0.09)
    assert cs.budget_for(_layer(0.30), "超配") == pytest.approx(0.30)


def test_exit_gives_a_zero_budget():
    assert cs.budget_for(_layer(0.30), "清仓") == 0.0


def test_no_verdict_is_equivalent_to_the_old_behaviour():
    # bind_layer_budget 关掉时调用方传 None —— 必须完全等于绑定前的满额分配。
    assert cs.budget_for(_layer(0.30), None) == pytest.approx(0.30)


def test_a_misconfigured_utilization_above_one_is_clamped(monkeypatch):
    # 配置写错不能抬高天花板 —— 这整套机制只被允许往下调。
    monkeypatch.setattr(cs, "load_risk_yaml_section", lambda k: {"超配": 1.5}, raising=False)
    monkeypatch.setattr("ats.config.load_risk_yaml_section", lambda k: {"超配": 1.5})
    assert cs.utilization_for("超配") == 1.0
    assert cs.budget_for(_layer(0.30), "超配") == pytest.approx(0.30)


def test_an_unreadable_verdict_spends_like_flat_not_like_conviction(monkeypatch):
    # 「读不出这条结论」绝不能花得像「高信心买入」。
    assert cs.utilization_for("梭哈") == cs.DEFAULT_UTILIZATION["标配"]
    monkeypatch.setattr("ats.config.load_risk_yaml_section",
                        lambda k: {"超配": "很多"})
    assert cs.utilization_for("超配") == cs.DEFAULT_UTILIZATION["标配"]


# --------------------------------------------------------------------------- #
# 权重：使用率只改总量，不改层内比例
# --------------------------------------------------------------------------- #
def test_utilization_scales_the_total_without_reordering_the_layer():
    full = cs.rank_cohort(_rows(), layer_cap=0.30)
    full_w = {r.symbol: r.weight for r in full}
    full_rank = {r.symbol: r.rank for r in full}

    light = cs.rank_cohort(_rows(), layer_cap=0.09)
    light_w = {r.symbol: r.weight for r in light}

    assert sum(full_w.values()) == pytest.approx(0.30)
    assert sum(light_w.values()) == pytest.approx(0.09)
    assert {r.symbol: r.rank for r in light} == full_rank        # 排名不变
    for sym in full_w:                                           # 相对比例不变
        assert light_w[sym] / sum(light_w.values()) == pytest.approx(
            full_w[sym] / sum(full_w.values()))


def test_weights_never_exceed_the_static_cap():
    for allocation in ("超配", "标配", "低配", "清仓"):
        cap = cs.budget_for(_layer(0.30), allocation)
        rows = cs.rank_cohort(_rows(), layer_cap=cap)
        assert sum(r.weight for r in rows) <= 0.30 + 1e-9


def test_exit_zeroes_every_suggested_weight():
    rows = cs.rank_cohort(_rows(), layer_cap=cs.budget_for(_layer(0.30), "清仓"))
    assert all(r.weight == 0.0 for r in rows)


# --------------------------------------------------------------------------- #
# 单票层：预算照落，但名次不是发现
# --------------------------------------------------------------------------- #
def test_a_one_name_cohort_is_marked_not_applicable_but_still_gets_a_budget():
    """单票层：名次不是发现，但预算照落 —— **受单票限额约束**。

    ⚠️ 落下来的不是层上限的全额，是 `single_name_cap_frac`（40%）那一档：
    单票拿不到超过层上限 40% 的仓位，而它是唯一的票，溢出无处可去，**剩下 60% 就不分配**。
    这是既有行为（不是本次引入），但它是静默的，所以在这里钉住：
    单票层的实际敞口只有层上限的四成，别把 weight_cap 当成它会用满的数。
    """
    rows = cs.rank_cohort(_rows(1), layer_cap=0.10)
    basket = cs.to_basket(rows, "L3_dc_power", 0.10)
    assert basket.cross_section_applicable is False        # 名次只是配置顺序的副产品
    assert sum(r.weight for r in basket.rows) == pytest.approx(0.10 * 0.40)


def test_two_comparable_names_make_the_cross_section_meaningful():
    basket = cs.to_basket(cs.rank_cohort(_rows(2), layer_cap=0.10), "L6_memory", 0.10)
    assert basket.cross_section_applicable is True


# --------------------------------------------------------------------------- #
# 跨层组上限：拆层不得放大**建议敞口**（不只是已有持仓）
# --------------------------------------------------------------------------- #
def _ai_cfg():
    from ats.config import load_sector_config
    return load_sector_config("ai_hardware")


def test_two_overweight_halves_of_a_split_layer_are_capped_by_their_group():
    """2026-08-20 实盘首跑发现的缺口。

    group 上限当时只加在 `risk/assess.py`（查**已有持仓**越没越界），而**分配预算**那条路
    只认单层 weight_cap —— L6 与 L7 同时超配就会提议 25%+20%=45%，而拆分前的 L5_fab 上限
    是 30%。护栏加在了错的一侧：等它响的时候，超额的仓位已经建出来了。
    """
    cfg = _ai_cfg()
    b = cs.budgets_for(cfg, {"L6_memory": "超配", "L7_foundry_pkg": "超配"})
    assert b["L6_memory"] + b["L7_foundry_pkg"] == pytest.approx(0.30)   # 拆分前的口径
    # 按比例缩，不是按配置顺序截断：组上限说的是两者合计能有多少，不是偏好哪一半。
    assert b["L6_memory"] / b["L7_foundry_pkg"] == pytest.approx(0.25 / 0.20)


def test_a_group_under_its_ceiling_is_left_alone():
    cfg = _ai_cfg()
    b = cs.budgets_for(cfg, {"L6_memory": "低配", "L7_foundry_pkg": "低配"})
    assert b["L6_memory"] == pytest.approx(0.25 * 0.3)
    assert b["L7_foundry_pkg"] == pytest.approx(0.20 * 0.3)


def test_group_ceiling_binds_even_when_no_single_layer_breaches():
    # 每一层单独看都在自己的 cap 内 —— 这正是组上限唯一存在的理由。
    cfg = _ai_cfg()
    b = cs.budgets_for(cfg, {"L3_dc_power": "标配", "L4_interconnect": "超配"})
    assert b["L3_dc_power"] <= 0.10 and b["L4_interconnect"] <= 0.15   # 逐层合规
    assert b["L3_dc_power"] + b["L4_interconnect"] == pytest.approx(0.15)  # 合计被卡住
