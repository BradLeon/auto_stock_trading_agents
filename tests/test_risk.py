"""Risk officer — correlation clustering, stress, assess breaches, pre-trade gate
(hermetic; no live TWS/network)."""

from datetime import datetime, timezone

from ats.memory import get_store
from ats.risk import assess as risk_assess, checks as risk_checks, correlation, stress
from ats.schemas.decision import TradeDecision
from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot, Position
from ats.schemas.risk import RiskReview

NOW = datetime.now(timezone.utc)


def _pos(sym, weight, beta=1.0, sector="optical", upnl=0.0, avg=100.0, qty=10, sec_type="STK"):
    mv = 1_000_000 * weight
    return Position(symbol=sym, sector=sector, sec_type=sec_type, qty=qty, avg_cost=avg,
                    market_price=mv / qty, market_value=mv, unrealized_pnl=upnl,
                    weight=weight, beta=beta)


def _opt(under, qty, delta, *, right="C", strike=100.0, spot=100.0, mult=100.0, iv=0.4,
         vega=0.40, gamma=0.02, theta=-0.01, expiry="20270101", avg=5.0, upnl=0.0, beta=1.5):
    """Option Position with IBKR-style greeks pre-filled (so assess uses them directly,
    no network). market_price is the option premium per share."""
    prem = avg
    mv = prem * qty * mult
    return Position(symbol=f"{under} {expiry} {strike:g} {right}", sector="optical",
                    sec_type="OPT", qty=qty, avg_cost=avg, market_price=prem, market_value=mv,
                    unrealized_pnl=upnl, weight=(mv / 1_000_000), beta=beta,
                    strike=strike, right=right, expiry=expiry, multiplier=mult, underlying=under,
                    delta=delta, gamma=gamma, vega=vega, theta=theta, iv=iv,
                    underlying_price=spot, greeks_source="ibkr")


def _pf(positions, cash=0.0, daily_pnl=0.0):
    return PortfolioSnapshot(as_of=NOW, net_liquidation=1_000_000, cash=cash,
                             gross_exposure=sum(p.market_value for p in positions),
                             daily_pnl=daily_pnl, positions=positions,
                             exposure=ExposureBreakdown())


# --------------------------------------------------------------------------- #
# correlation clustering
# --------------------------------------------------------------------------- #
def test_correlation_clusters():
    # A,B move together; C independent.
    base = [100 + i for i in range(60)]
    prices = {"A": base, "B": [x * 1.01 for x in base],
              "C": [100 + (i % 2) for i in range(60)]}
    weights = {"A": 0.3, "B": 0.3, "C": 0.1}
    cl = correlation.clusters(weights, prices, threshold=0.7)
    top = cl[0]
    assert set(top["members"]) == {"A", "B"} and abs(top["weight"] - 0.6) < 1e-6


def test_stress_beta_shock():
    pf = _pf([_pos("HI", 0.5, beta=2.0), _pos("LO", 0.3, beta=0.5)])
    # beta-weighted: 0.5*2 + 0.3*0.5 = 1.15; -20% market -> -23%
    assert stress.market_shock(pf, -0.20) == -23.0
    assert stress.cluster_shock(pf, ["HI"], -0.35) == -17.5   # 0.5*-0.35


# --------------------------------------------------------------------------- #
# assess breaches
# --------------------------------------------------------------------------- #
def test_assess_beta_and_stop_and_event(monkeypatch):
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})   # skip network clustering
    pf = _pf([
        _pos("HOT", 0.5, beta=3.0),                               # pushes beta over 1.5
        _pos("LOSER", 0.2, beta=1.0, upnl=-400, avg=100, qty=20),  # -20%? cost 2000, upnl -400 = -20%
    ], cash=300_000)
    review = risk_assess.assess(pf, event_data={"HOT": {"expected_move_pct": 30.0}})
    layers = {b.layer for b in review.breaches}
    assert any(l.startswith("L3-组合beta") for l in layers)       # beta 1.7 > 1.5
    assert any(l == "L6-事件" for l in layers)                    # HOT 0.5*30=15% > 3%
    # HOT event risk recorded
    assert any(e.symbol == "HOT" and e.event_loss_pct == 15.0 for e in review.event_risks)


# --------------------------------------------------------------------------- #
# pre-trade gate (hard enforcement)
# --------------------------------------------------------------------------- #
def test_pre_trade_blocks_buy_in_derisk(monkeypatch):
    pf = _pf([_pos("X", 0.3)])
    review = RiskReview(as_of=NOW, risk_state="derisk")
    buy = TradeDecision(symbol="NEW", action="buy", notional_usd=10000)
    sell = TradeDecision(symbol="X", action="trim", notional_usd=5000)
    out, notes, _ = risk_checks.pre_trade([buy, sell], pf, review=review, apply_base=False)
    assert [d.symbol for d in out] == ["X"]                       # buy blocked, sell passes
    assert any("de-risk" in n for n in notes)


def test_pre_trade_event_clip(monkeypatch):
    pf = _pf([_pos("X", 0.3)])
    review = RiskReview(as_of=NOW, risk_state="normal")
    # EM 20%, cap 3% NAV -> max weight 15% -> max notional 150k on 1M NAV
    buy = TradeDecision(symbol="COHR", action="buy", notional_usd=300_000)
    out, notes, _ = risk_checks.pre_trade(
        [buy], pf, event_data={"COHR": {"expected_move_pct": 20.0}}, review=review, apply_base=False)
    assert out[0].notional_usd == 150_000 and any("CLIP" in n for n in notes)


def test_pre_trade_no_portfolio_skips():
    buy = TradeDecision(symbol="X", action="buy", notional_usd=1000)
    out, notes, review = risk_checks.pre_trade([buy], None)
    assert out == [buy] and review is None


# --------------------------------------------------------------------------- #
# cash equivalents (unified haircut model)
# --------------------------------------------------------------------------- #
def _set_ce(monkeypatch, mapping):
    from ats.config import get_config
    monkeypatch.setattr(get_config().app.risk, "cash_equivalents", mapping)


def test_cash_equivalent_full_credit(monkeypatch):
    # SGOV haircut=0 -> fully cash: lifts effective cash, drops effective leverage,
    # never occupies single-name / beta. Raw snapshot figures stay untouched.
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})
    _set_ce(monkeypatch, {"SGOV": 0.0})
    pf = _pf([_pos("SGOV", 0.3, beta=0.0), _pos("MEGA", 0.25, beta=1.8)], cash=50_000)
    review = risk_assess.assess(pf)
    assert review.cash_pct == 0.05                            # raw account cash unchanged
    assert abs(review.effective_cash_pct - 0.35) < 1e-6      # 50k + 300k credit
    assert review.effective_leverage == 0.25                 # gross 550k − 300k credit
    assert review.portfolio_beta == 0.45                     # only MEGA: 0.25*1.8
    assert not any("SGOV" in b.actual for b in review.breaches if b.layer == "L1-单票")
    ce = {c.symbol: c for c in review.cash_equivalents}
    assert ce["SGOV"].cash_credit == 300_000 and ce["SGOV"].haircut == 0.0


def test_cash_equivalent_partial_haircut(monkeypatch):
    # haircut=0.2 -> 80% cash credit, 20% residual risk weight (proportional knob).
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})
    _set_ce(monkeypatch, {"BALLAST": 0.2})
    pf = _pf([_pos("BALLAST", 0.5, beta=1.0)], cash=0.0)
    review = risk_assess.assess(pf)
    ce = review.cash_equivalents[0]
    assert ce.cash_credit == 400_000 and ce.haircut == 0.2
    assert abs(review.effective_cash_pct - 0.40) < 1e-6
    # residual risk weight 0.5*0.2=0.10 <= 0.20 cap -> no single-name breach (raw 0.5 would breach)
    assert not any(b.layer == "L1-单票" for b in review.breaches)
    assert review.portfolio_beta == 0.1                      # only risk-weight share: 0.10*1.0


# --------------------------------------------------------------------------- #
# options exemption + explicit symbol→layer mapping
# --------------------------------------------------------------------------- #
def test_bsm_greeks_numeric():
    # ATM call, S=K=100, T=1, r=0, σ=0.2 → known reference values.
    from ats.risk import options_math as om
    g = om.greeks(100.0, 100.0, 1.0, 0.0, 0.2, is_call=True)
    assert abs(g["delta"] - 0.539828) < 1e-4
    assert abs(g["gamma"] - 0.0198476) < 1e-5
    assert abs(g["vega"] - 0.39695) < 1e-3          # per 1% vol point
    assert abs(g["theta"] - (-0.010875)) < 1e-4     # per day
    # put delta = call delta − 1
    p = om.greeks(100.0, 100.0, 1.0, 0.0, 0.2, is_call=False)
    assert abs(p["delta"] - (g["delta"] - 1.0)) < 1e-9
    assert abs(p["gamma"] - g["gamma"]) < 1e-12     # gamma right-agnostic


def test_regt_margin_by_strategy():
    from ats.risk import options_math as om
    # cash-secured-ish short put: max(0.2*100-0, 0.1*100)*100*1 + premium(5*100)
    assert om.regt_margin("sell_put", 100, 100, 5.0, 1) == 20 * 100 + 500
    assert om.regt_margin("covered_call", 100, 100, 5.0, 1) == 0.0
    assert om.regt_margin("buy_call", 100, 110, 5.0, 2) == 5.0 * 100 * 2   # long = premium


def test_option_buy_call_lifts_single_name(monkeypatch):
    # A big long call pushes the underlying's NET delta over the single-name cap.
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})
    stk = _pos("NVDA", 0.10, beta=1.5)
    call = _opt("NVDA", qty=10, delta=0.8, strike=200, spot=200)   # dn=0.8*10*100*200=160k → 0.16
    pf = _pf([stk, call], cash=740_000)
    review = risk_assess.assess(pf)
    # net delta weight ≈ 0.10 (stock) + 0.16 (call) = 0.26 > 0.20
    assert any(b.layer == "L1-单票" and "NVDA" in b.actual for b in review.breaches)
    o = review.option_risks[0]
    assert o.strategy == "buy_call" and abs(o.delta_notional - 160_000) < 1


def test_option_long_put_hedges_single_name(monkeypatch):
    # A long put nets down a concentrated stock below the single-name cap.
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})
    stk = _pos("NVDA", 0.22, beta=1.5)                              # alone: 0.22 > 0.20
    put = _opt("NVDA", qty=3, delta=-0.9, right="P", strike=100, spot=100)  # dn=-27k → -0.027
    pf = _pf([stk, put], cash=700_000)
    review = risk_assess.assess(pf)
    # net 0.22 − 0.027 = 0.193 < 0.20 → no single-name breach
    assert not any(b.layer == "L1-单票" and "NVDA" in b.actual for b in review.breaches)
    assert review.option_risks[0].strategy == "buy_put"


def test_option_stress_full_reval_short_vs_long_put(monkeypatch):
    # Short put loses hard under −20% spot + vol bump; the same long put gains (hedge).
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})
    short_put = _opt("SPY", qty=-20, delta=-0.5, right="P", strike=100, spot=100, iv=0.4)
    r_short = risk_assess.assess(_pf([short_put], cash=900_000))
    worst_short = min(s.loss_pct for s in r_short.stress)
    assert worst_short < -2.0                                       # meaningful tail loss

    long_put = _opt("SPY", qty=20, delta=-0.5, right="P", strike=100, spot=100, iv=0.4)
    r_long = risk_assess.assess(_pf([long_put], cash=900_000))
    # −20% scenario is a GAIN for the long put holder
    down20 = [s for s in r_long.stress if "-20%" in s.scenario][0]
    assert down20.loss_pct > 0


def test_margin_breach_ibkr(monkeypatch):
    # IBKR-authoritative margin over the util cap + thin excess liquidity → hard breaches.
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})
    pf = PortfolioSnapshot(as_of=NOW, net_liquidation=1_000_000, cash=0.0,
                           gross_exposure=500_000, daily_pnl=0.0,
                           positions=[_pos("NVDA", 0.10, beta=1.0)], exposure=ExposureBreakdown(),
                           init_margin=600_000, maint_margin=500_000, excess_liquidity=50_000,
                           buying_power=100_000, margin_source="ibkr")
    review = risk_assess.assess(pf)
    layers = {b.layer for b in review.breaches}
    assert "L2-保证金利用率" in layers                              # 0.6 > 0.5
    assert "L2-剩余流动性" in layers                                # 0.05 < 0.10
    assert review.margin.source == "ibkr" and review.margin.margin_util == 0.6


def test_no_options_regression(monkeypatch):
    # Equity-only book: options machinery stays inert (no option risks, zero net vega).
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})
    pf = _pf([_pos("NVDA", 0.10, beta=1.5)], cash=900_000)
    review = risk_assess.assess(pf)
    assert review.option_risks == []
    assert review.portfolio_greeks.net_vega == 0.0
    assert review.portfolio_beta == 0.15                           # unchanged: only NVDA 0.10*1.5


def test_symbol_layer_mapping_explicit(monkeypatch):
    # KLAC now maps to L6 (config was wrongly "KLA"); TSLA is genuinely unmapped and
    # surfaced explicitly rather than silently dropped.
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})
    pf = _pf([_pos("KLAC", 0.05, beta=1.0), _pos("TSLA", 0.05, beta=1.0)], cash=900_000)
    review = risk_assess.assess(pf)
    m = {sl.symbol: sl.layer for sl in review.symbol_layers}
    assert m["KLAC"] == "L6_equipment"
    assert m["TSLA"] == ""


def test_riskconfig_parses_cash_equivalents():
    from ats.config import get_config
    ce = get_config().app.risk.cash_equivalents
    assert ce.get("SGOV") == 0.0 and "BRK-B" in ce


def test_store_risk_review_roundtrip():
    store = get_store()
    store.save_risk_review(RiskReview(as_of=NOW, risk_state="caution", notes="2 breaches"))
    assert store.latest_risk_review().risk_state == "caution"
    assert len(store.recent_risk_reviews()) >= 1
