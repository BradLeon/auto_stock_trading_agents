"""跨层组上限：**每层单独看都合规，合计却越限**。

这是组上限唯一存在的理由 —— 逐层看不出任何问题。它最初是拆层时的脚手架
（一个 30% 的 cap 拆成 25%+20%=45%，不能在「重构」的名义下悄悄放大敞口）。
2026-08-21 `ai_hardware` 已取消它（拆 L6/L7 的依据正是它们**不同向**，再用共享上限
绑回去自相矛盾），但**机制保留**：别的 sector 或以后的拆层还会需要。

所以这里用**合成配置**测机制，不绑任何 sector 的当期政策 —— 否则每次调 cap 都要改测试，
而那会让人倾向于不调。
"""

from datetime import datetime, timezone

import pytest

from ats.risk.assess import assess
from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot, Position

NOW = datetime.now(timezone.utc)

DEMO_SECTOR = """name: demo
layers:
  - key: LA
    label: A层
    tickers: [{symbol: MU}]
  - key: LB
    label: B层
    tickers: [{symbol: TSM}]
  - key: LC
    label: C层
    tickers: [{symbol: NVDA}]
"""

DEMO_RISK = """limits:
  max_position_pct: 0.99
sector_layer_caps:
  demo:
    LA: {weight_cap: 0.25}
    LB: {weight_cap: 0.20}
    LC: {weight_cap: 0.15}
layer_groups:
  demo:
    ab:
      label: A+B
      layers: [LA, LB]
      weight_cap: 0.30
"""


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """一个带跨层组的合成 sector，通过 ATS_CONFIG_DIR 生效。"""
    from ats.config import reset_config_cache

    (tmp_path / "sectors").mkdir()
    (tmp_path / "settings.yaml").write_text("environment: paper\n", encoding="utf-8")
    (tmp_path / "risk.yaml").write_text(DEMO_RISK, encoding="utf-8")
    (tmp_path / "sectors" / "demo.yaml").write_text(DEMO_SECTOR, encoding="utf-8")
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    reset_config_cache()
    yield
    reset_config_cache()


def _portfolio(**weights):
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


def test_group_breaches_even_when_every_member_layer_is_within_its_own_cap(demo):
    # LA 20%(cap 25%) + LB 15%(cap 20%) —— 逐层都合规，合计 35% 却越过组的 30%。
    r = assess(_portfolio(MU=0.20, TSM=0.15), sector="demo")
    layers = {le.key: le for le in r.chain_layers}
    assert layers["LA"].breached is False
    assert layers["LB"].breached is False
    g = _groups(r)["ab"]
    assert g.breached is True and g.is_group is True
    assert round(g.weight, 4) == 0.35
    assert any("组ab" in b.layer for b in r.breaches)


def test_group_breach_blocks_every_member_layer(demo):
    # 只封「组」是封不住新买单的：下游按**层键**判断能不能买。
    r = assess(_portfolio(MU=0.20, TSM=0.15), sector="demo")
    assert {"LA", "LB"} <= set(r.directive.blocked_layers)


def test_group_within_ceiling_does_not_breach(demo):
    r = assess(_portfolio(MU=0.10, TSM=0.10), sector="demo")
    g = _groups(r)["ab"]
    assert g.breached is False and round(g.weight, 4) == 0.20
    assert not any("组ab" in b.layer for b in r.breaches)


def test_group_rows_stay_out_of_chain_layers(demo):
    # chain_layers 的既有消费者一律假设每行是一个层：blocked_layers 会把 key 当层键，
    # 「占比最高的层」会挑出一个必然更大的合计行。所以 group 必须待在自己的字段里。
    r = assess(_portfolio(MU=0.10, TSM=0.10), sector="demo")
    assert "ab" not in {le.key for le in r.chain_layers}
    assert {g.key for g in r.chain_layer_groups} == {"ab"}


def test_a_sector_without_groups_reports_none(demo):
    # 取消组之后不该留下空壳行 —— 否则报告里会多出一段没有内容的「跨层上限」。
    from ats.config import load_sector_config

    r = assess(_portfolio(NVDA=0.10), sector="demo")
    assert load_sector_config("demo").layer_groups          # 本 fixture 有组
    assert _groups(r)["ab"].weight == 0.0                   # 组内无持仓 → 0，不报 breach
    assert _groups(r)["ab"].breached is False


def test_layer_cap_check_reads_the_static_ceiling(demo):
    """层集中度 breach 必须读**静态** weight_cap，不受层级分析师使用率影响。

    使用率答的是「新增资金投多少」，breach 答的是「已有持仓越没越界」。共用一个数，
    一条「低配」结论就会把满仓但合规的层瞬间判成超限、触发不必要的减仓。
    """
    from ats.config import load_sector_config

    cap = load_sector_config("demo").layer_by_key("LA").weight_cap
    r = assess(_portfolio(MU=cap + 0.05), sector="demo")
    la = next(le for le in r.chain_layers if le.key == "LA")
    assert la.cap == cap                     # 配置里的静态值，不是使用率调整后的
    assert la.breached is True
