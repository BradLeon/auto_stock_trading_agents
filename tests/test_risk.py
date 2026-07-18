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
def test_options_exempt_from_equity_rules(monkeypatch):
    # An option down 50% at 30% weight would trip stop-loss + single-name if treated as
    # stock; being secType=OPT it is exempt, surfaced in review.options, and excluded from
    # portfolio beta and effective leverage (margin exemption).
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})
    opt = _pos("COHR", 0.30, beta=3.0, upnl=-5000, avg=100, qty=100, sec_type="OPT")
    stk = _pos("NVDA", 0.10, beta=1.5)
    pf = _pf([opt, stk], cash=600_000)
    review = risk_assess.assess(pf)
    assert not any("COHR" in b.actual for b in review.breaches)   # no stop-loss / single-name
    assert any(o.symbol == "COHR" for o in review.options)
    assert review.portfolio_beta == 0.15                          # only NVDA 0.10*1.5
    assert review.effective_leverage == 0.10                      # gross 400k − 300k option MV


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
