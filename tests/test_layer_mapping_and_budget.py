"""Layer attribution of holdings, allocation-driven layer ceilings, true-exposure rows (hermetic)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.config import load_instrument_risk_registry, load_sector_config
from ats.memory import get_store
from ats.risk import assess as risk_assess
from ats.risk import exposure as risk_exposure
from ats.risk import layer_budget
from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot, Position

NOW = datetime.now(timezone.utc)


def _pos(sym, mv, *, beta=1.0, qty=10, nav=1_000_000):
    return Position(symbol=sym, sector="x", sec_type="STK", qty=qty, avg_cost=mv / qty,
                    market_price=mv / qty, market_value=mv, weight=mv / nav, beta=beta)


def _pf(positions, cash=0.0):
    return PortfolioSnapshot(as_of=NOW, net_liquidation=1_000_000, cash=cash,
                             gross_exposure=sum(p.market_value for p in positions),
                             positions=positions, exposure=ExposureBreakdown())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})


def test_instrument_registry_places_the_named_holdings():
    reg = load_instrument_risk_registry()
    assert reg.resolve("GOOG").layer_key == "L2_cloud"
    assert reg.resolve("DRAM").layer_key == "L6_memory"
    assert reg.resolve("FORM").layer_key == "L8_equipment"
    nvdx, skuu = reg.resolve("NVDX"), reg.resolve("SKUU")
    assert (nvdx.economic_entity, nvdx.exposure_multiplier, nvdx.layer_symbol) == ("NVDA", 2.0, "NVDA")
    assert (skuu.economic_entity, skuu.exposure_multiplier) == ("SK_HYNIX", 2.0)


def test_holdings_land_in_the_layers_the_user_named():
    pf = _pf([_pos("GOOG", 50_000), _pos("DRAM", 40_000), _pos("SKUU", 10_000), _pos("NVDX", 20_000),
              _pos("FORM", 30_000), _pos("NVDA", 30_000)])
    review = risk_assess.assess(pf)
    layer_of = {s.symbol: s.layer for s in review.symbol_layers}
    assert layer_of == {"GOOG": "L2_cloud", "DRAM": "L6_memory", "SKUU": "L6_memory", "NVDX": "L5_chip_design",
                        "FORM": "L8_equipment", "NVDA": "L5_chip_design"}
    weights = {c.key: c.weight for c in review.chain_layers}
    assert weights["L5_chip_design"] == pytest.approx(0.03 + 0.04)       # NVDA + NVDX at 2x
    assert weights["L6_memory"] == pytest.approx(0.04 + 0.02)            # DRAM + SKUU at 2x
    assert "L1_app" not in weights                                        # GOOG is counted in L2 only
    nvda = next(e for e in review.economic_exposures if e.economic_entity == "NVDA")
    assert nvda.net_delta_weight == pytest.approx(0.07)                  # one entity, true exposure


def test_the_limits_the_user_relaxed():
    from ats.config import get_config

    rc = get_config().app.risk
    assert rc.max_position_pct == 0.35 and rc.max_gross_leverage == 1.5 and rc.cash_floor_pct < 0


def test_borrowing_is_fine_inside_the_leverage_limit_and_a_breach_beyond_it():
    inside = risk_assess.assess(_pf([_pos("AMZN", 600_000), _pos("TSM", 600_000)], cash=-200_000))   # 1.2x, cash -20%
    assert not any(b.layer in ("L2-现金", "L2-杠杆") for b in inside.breaches)
    beyond = risk_assess.assess(_pf([_pos("AMZN", 800_000), _pos("TSM", 800_000)], cash=-600_000))   # 1.6x
    assert any(b.layer == "L2-杠杆" for b in beyond.breaches)
    assert any(b.layer == "L2-现金" for b in beyond.breaches)       # -60% is past the -50% borrowing floor


def _allocation_store(tmp_path, monkeypatch, layers, *, age_days=1):
    from ats.agent.task_projection import ProjectionScope

    stamp = (NOW - timedelta(days=age_days)).isoformat()

    class _Env:
        payload = {"layers": layers}
        as_of = stamp
        created_at = stamp

    class _Store:
        def reusable_task_projection(self, *, agent_role, scope, schema_name=None, **_):
            assert agent_role == "sector_allocation" and scope == ProjectionScope(kind="sector", id="ai_hardware")
            return _Env()

    return _Store()


def test_a_low_allocation_lowers_the_layer_ceiling_and_never_raises_it(tmp_path, monkeypatch):
    scfg = load_sector_config("ai_hardware")
    store = _allocation_store(tmp_path, monkeypatch, [
        {"layer": "L4_interconnect", "allocation": "低配"}, {"layer": "L5_chip_design", "allocation": "超配"},
        {"layer": "L6_memory", "allocation": "清仓"}])
    caps = layer_budget.effective_caps(scfg, store)
    assert caps["L4_interconnect"].effective == pytest.approx(0.15 * 0.3)
    assert caps["L4_interconnect"].static == 0.15 and caps["L4_interconnect"].allocation == "低配"
    assert caps["L5_chip_design"].effective == pytest.approx(0.20)       # 超配 = the static ceiling, not above it
    assert caps["L6_memory"].effective == 0.0
    assert caps["L8_equipment"].source == "static_no_allocation" and caps["L8_equipment"].effective == 0.15


def test_a_stale_or_missing_allocation_falls_back_to_the_static_ceiling(tmp_path, monkeypatch):
    scfg = load_sector_config("ai_hardware")
    stale = _allocation_store(tmp_path, monkeypatch, [{"layer": "L4_interconnect", "allocation": "低配"}],
                              age_days=layer_budget.MAX_ALLOCATION_AGE_DAYS + 1)
    caps = layer_budget.effective_caps(scfg, stale)
    assert caps["L4_interconnect"].effective == 0.15 and caps["L4_interconnect"].source == "static_stale"
    none = layer_budget.effective_caps(scfg, None)
    assert none["L4_interconnect"].effective == 0.15 and none["L4_interconnect"].source == "static_no_allocation"


def test_assess_uses_the_effective_ceiling_and_a_breach_only_blocks_new_buys(tmp_path, monkeypatch):
    monkeypatch.setattr(layer_budget, "latest_allocations",
                        lambda store, sector, now=None: ({"L8_equipment": "低配"}, NOW.isoformat(), ""))
    review = risk_assess.assess(_pf([_pos("ASML", 100_000)]))            # 10% in L8; static 15%, 低配 -> 4.5%
    layer = next(c for c in review.chain_layers if c.key == "L8_equipment")
    assert layer.breached and layer.cap == pytest.approx(0.045) and layer.static_cap == 0.15
    breach = next(b for b in review.breaches if b.layer == "L1-L8_equipment")
    assert "低配" in breach.limit and "新买单" in breach.action          # no forced sale, only no new money


def test_exposure_rows_show_multiplier_and_option_delta_beside_market_value():
    pf = _pf([_pos("NVDX", 100_000, qty=100), _pos("QQQ", 50_000, qty=50)])
    review = risk_assess.assess(pf)
    rows = {r["name"]: r for r in risk_exposure.exposure_rows(review, pf)}
    assert rows["NVDX"]["market_value"] == 100_000 and rows["NVDX"]["exposure"] == pytest.approx(200_000)
    assert rows["NVDX"]["factor"].startswith("2×") and rows["NVDX"]["mapped"] is True
    assert rows["QQQ"]["mapped"] is False and [r["name"] for r in risk_exposure.unmapped(list(rows.values()))] == ["QQQ"]
