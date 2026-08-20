"""跨层上限：拆层不得放大总敞口。

拆层有一个机械性副作用——一个 cap 变成两个各自独立的 cap，两半同时满仓就能超过
拆分前允许的总量。group 上限是为此**新加的一道护栏**（而不是放宽旧的），所以它要
锁住的核心场景是：**每一层单独看都合规，合计却越限。**
"""

from datetime import datetime, timezone

from ats.risk.assess import assess
from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot, Position

NOW = datetime.now(timezone.utc)


def _portfolio(**weights):
    """按目标权重造一个只有正股的组合（净值 100 万）。"""
    positions = []
    for sym, w in weights.items():
        mv = 1_000_000 * w
        positions.append(Position(symbol=sym, sector="semis", sec_type="STK", qty=10,
                                  avg_cost=100.0, market_price=mv / 10, market_value=mv,
                                  unrealized_pnl=0.0, weight=w, beta=1.0))
    return PortfolioSnapshot(as_of=NOW, net_liquidation=1_000_000,
                             cash=1_000_000 * (1 - sum(weights.values())),
                             gross_exposure=sum(p.market_value for p in positions),
                             daily_pnl=0.0, positions=positions,
                             exposure=ExposureBreakdown())


def _groups(review):
    return {g.key: g for g in review.chain_layer_groups}


def test_group_breaches_even_when_every_member_layer_is_within_its_own_cap():
    # L6_memory 20% (cap 25%) + L7_foundry_pkg 15% (cap 20%) —— 逐层都合规，
    # 合计 35% 却越过 fab_memory 的 30%。这正是拆层想偷偷放大的那个敞口。
    r = assess(_portfolio(MU=0.20, TSM=0.15))
    layers = {le.key: le for le in r.chain_layers}
    assert layers["L6_memory"].breached is False
    assert layers["L7_foundry_pkg"].breached is False
    g = _groups(r)["fab_memory"]
    assert g.breached is True and g.is_group is True
    assert round(g.weight, 4) == 0.35
    assert any("组fab_memory" in b.layer for b in r.breaches)


def test_group_breach_blocks_every_member_layer():
    # 只封「组」是封不住新买单的：下游按**层键**判断能不能买。
    r = assess(_portfolio(MU=0.20, TSM=0.15))
    assert {"L6_memory", "L7_foundry_pkg"} <= set(r.directive.blocked_layers)


def test_group_within_ceiling_does_not_breach():
    r = assess(_portfolio(MU=0.10, TSM=0.10))
    g = _groups(r)["fab_memory"]
    assert g.breached is False and round(g.weight, 4) == 0.20
    assert not any("组fab_memory" in b.layer for b in r.breaches)


def test_group_rows_stay_out_of_chain_layers():
    # chain_layers 的既有消费者一律假设每行是一个层：blocked_layers 会把 key 当层键，
    # 「占比最高的层」会挑出一个必然更大的合计行。所以 group 必须待在自己的字段里。
    r = assess(_portfolio(MU=0.10, TSM=0.10))
    keys = {le.key for le in r.chain_layers}
    assert "fab_memory" not in keys and "dc_infra" not in keys
    assert {g.key for g in r.chain_layer_groups} == {"fab_memory", "dc_infra"}


def test_layer_cap_check_reads_the_static_ceiling():
    # 层集中度 breach 必须读**静态** weight_cap，不受层级分析师使用率影响：
    # 使用率答的是「新增资金投多少」，breach 答的是「已有持仓越没越界」。
    r = assess(_portfolio(MU=0.30))
    memory = next(le for le in r.chain_layers if le.key == "L6_memory")
    assert memory.cap == 0.25          # risk.yaml 的静态值
    assert memory.breached is True
