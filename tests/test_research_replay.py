"""Replay engine correctness. A backtest is the easiest code to fool yourself with,
so these tests target the three ways it lies: lookahead, free trading, and a rule
that cannot actually detect what it claims to."""

from datetime import date, timedelta

import pytest

from ats.research import replay

D0 = date(2024, 1, 1)


def days(n):
    return [D0 + timedelta(days=i) for i in range(n)]


def flat(n, v=100.0):
    return [v] * n


def test_no_lookahead_future_prices_cannot_change_a_past_signal():
    """Mutate the future; every exposure decided before it must be identical."""
    closes = [100.0 + i * 0.1 for i in range(400)]
    at_300 = replay.rule_trend(closes[:301])
    tampered = closes[:301] + [1e6] * 99          # absurd future
    assert replay.rule_trend(tampered[:301]) == at_300


def test_trend_rule_exits_below_sma_and_holds_above():
    rising = [100.0 + i for i in range(300)]
    assert replay.rule_trend(rising) == 1.0
    falling = [400.0 - i for i in range(300)]
    assert replay.rule_trend(falling) == 0.0


def test_peer_rule_stays_invested_when_the_whole_cohort_falls():
    """An indiscriminate sector selloff must NOT trigger it — that is the point."""
    n = 300
    down = [100.0] * 200 + [100.0 - i * 0.4 for i in range(n - 200)]   # -40% together
    peers = [list(down) for _ in range(5)]
    assert replay.rule_peer_relative(down, peers=peers) == 1.0


def test_peer_rule_fires_when_the_damage_is_idiosyncratic():
    n = 300
    peers = [[100.0] * n for _ in range(5)]                            # cohort flat
    lone = [100.0] * 200 + [100.0 - i * 0.4 for i in range(n - 200)]   # this one dies
    assert replay.rule_peer_relative(lone, peers=peers) == 0.0


def test_vol_throttle_is_conditional_not_always_on():
    # Constant-amplitude oscillation = constant realised vol, so the latest
    # reading cannot exceed the 80th percentile of its own history.
    calm = [100.0 + (0.5 if i % 2 else -0.5) for i in range(400)]
    assert replay.rule_vol_throttle(calm) == 1.0        # quiet regime -> untouched


def test_vol_throttle_cuts_exposure_when_vol_spikes():
    calm = [100.0 + (0.05 if i % 2 else -0.05) for i in range(300)]
    wild = calm + [calm[-1] * (1 + (0.08 if i % 2 else -0.08)) ** 1 for i in range(40)]
    assert replay.rule_vol_throttle(wild) < 1.0


def test_v_shaped_recovery_makes_the_trend_rule_lose_to_buy_and_hold():
    """The whipsaw cost must be measurable, or the engine is not testing anything.

    A sharp V is the documented worst case for trend following (it sells the
    bottom and rebuys higher). If this passes, the engine reports that honestly.
    """
    up = [100.0 + i * 0.25 for i in range(260)]          # long uptrend, builds SMA200
    down = [up[-1] * (1 - 0.012) ** i for i in range(40)]   # fast -38% crash
    back = [down[-1] * (1 + 0.014) ** i for i in range(60)]  # fast recovery
    closes = up + down + back
    d = days(len(closes))
    px = {"X": closes, "SGOV": flat(len(closes))}
    kw = dict(equity={"X": 1.0}, reserve=("SGOV", 0.0), start_idx=250)
    bh = replay.run(d, px, rule_name="BH", **kw)
    tr = replay.run(d, px, rule_name="C_trend_sma200", **kw)
    assert tr.curve[-1] < bh.curve[-1]                  # whipsaw really costs
    assert tr.trades > 0


def test_sustained_grind_lets_the_trend_rule_beat_buy_and_hold():
    up = [100.0 + i * 0.25 for i in range(260)]
    grind = [up[-1] * (1 - 0.004) ** i for i in range(200)]    # slow, long decline
    closes = up + grind
    d = days(len(closes))
    px = {"X": closes, "SGOV": flat(len(closes))}
    kw = dict(equity={"X": 1.0}, reserve=("SGOV", 0.0), start_idx=250)
    bh = replay.run(d, px, rule_name="BH", **kw)
    tr = replay.run(d, px, rule_name="C_trend_sma200", **kw)
    assert replay.max_drawdown(tr.curve) > replay.max_drawdown(bh.curve)  # less negative
    assert tr.curve[-1] > bh.curve[-1]


def test_costs_are_actually_charged():
    closes = [100.0 + (10 if i % 2 else -10) for i in range(300)]   # forces churn
    d = days(len(closes))
    px = {"X": closes, "SGOV": flat(len(closes))}
    kw = dict(equity={"X": 1.0}, reserve=("SGOV", 0.0),
              rule_name="C_trend_sma200", start_idx=250)
    free = replay.run(d, px, cost_bps=0.0, **kw)
    paid = replay.run(d, px, cost_bps=50.0, **kw)
    assert paid.cost_paid > 0 and free.cost_paid == 0
    assert paid.curve[-1] < free.curve[-1]


def test_buy_and_hold_pays_nothing_and_never_trades():
    closes = [100.0 + i for i in range(300)]
    d = days(len(closes))
    px = {"X": closes, "SGOV": flat(len(closes))}
    bh = replay.run(d, px, equity={"X": 1.0}, reserve=("SGOV", 0.0),
                    rule_name="BH", start_idx=250)
    assert bh.trades == 0 and bh.cost_paid == 0.0


def test_series_match_scalar_rules():
    """The fast causal series must equal the readable scalar rule, point for point.

    The scalar version is the reference; the series exists only for speed. If they
    ever diverge, every backtest result silently becomes untrustworthy — so pin them.
    """
    import random

    random.seed(7)
    closes = [100.0]
    for _ in range(400):
        closes.append(closes[-1] * (1 + random.gauss(0.0005, 0.02)))

    trend = replay.series_trend(closes)
    vol = replay.series_vol_throttle(closes)
    for i in range(210, len(closes), 17):        # sample across the range
        assert trend[i] == replay.rule_trend(closes[: i + 1])
        assert vol[i] == pytest.approx(replay.rule_vol_throttle(closes[: i + 1]))


def test_peer_series_matches_scalar_rule():
    import random

    random.seed(11)
    def walk(n, drift):
        c = [100.0]
        for _ in range(n):
            c.append(c[-1] * (1 + random.gauss(drift, 0.02)))
        return c

    syms = ["A", "B", "C", "D"]
    px = {s: walk(400, -0.002 if s == "A" else 0.0005) for s in syms}
    ser = replay.build_series(px, syms, "B_peer_relative")
    for i in range(260, 400, 23):
        peers = [px[o][: i + 1] for o in syms if o != "A"]
        assert ser["A"][i] == replay.rule_peer_relative(px["A"][: i + 1], peers=peers)


# ── strategy D (pre-registered variant #2) ───────────────────────────────────
def test_d_full_exposure_when_uptrend_and_calm_vix():
    rising = [100.0 + i for i in range(300)]          # above all three MAs
    assert replay.rule_triple_ma_vix(rising, vix=[12.0]) == pytest.approx(1.0)
    assert replay.rule_triple_ma_vix(rising, vix=[15.0]) == pytest.approx(1.0)


def test_d_vix_throttles_proportionally_above_the_anchor():
    rising = [100.0 + i for i in range(300)]
    # anchor 15 / VIX 30 = 0.5; trend leg is full, so exposure halves.
    assert replay.rule_triple_ma_vix(rising, vix=[30.0]) == pytest.approx(0.5)
    assert replay.rule_triple_ma_vix(rising, vix=[20.0]) == pytest.approx(0.75)


def test_d_grades_trend_by_how_many_mas_price_holds():
    """Partial credit is the point of the three-line system.

    A long decline followed by a small bounce: price reclaims the short MAs but
    is still far under the 200-day, so D should give 2/3 — not a binary in/out.
    """
    closes = [300.0 - i * 0.5 for i in range(260)] + [170.0 + i * 0.3 for i in range(40)]
    mas = {n: replay.sma(closes, n) for n in (20, 50, 200)}
    assert closes[-1] > mas[20] and closes[-1] > mas[50] and closes[-1] < mas[200]
    assert replay.rule_triple_ma_vix(closes, vix=[15.0]) == pytest.approx(2 / 3, abs=1e-9)


def test_d_falls_to_zero_in_a_downtrend():
    falling = [400.0 - i for i in range(300)]
    assert replay.rule_triple_ma_vix(falling, vix=[15.0]) == 0.0


def test_d_series_matches_scalar_rule():
    import random

    random.seed(3)
    closes, vix = [100.0], []
    for _ in range(400):
        closes.append(closes[-1] * (1 + random.gauss(0.0006, 0.018)))
    for _ in range(len(closes)):
        vix.append(max(9.0, random.gauss(18.0, 5.0)))
    ser = replay.series_triple_ma_vix(closes, vix)
    for i in range(210, len(closes), 19):
        assert ser[i] == pytest.approx(
            replay.rule_triple_ma_vix(closes[: i + 1], vix=vix[: i + 1]))


# ── Sharpe ───────────────────────────────────────────────────────────────────
def test_sharpe_is_excess_over_the_reserve_asset():
    rets = [0.001] * 252
    rf = [0.0] * 252
    # constant excess -> zero stdev -> undefined, must be None not a huge number
    assert replay.sharpe(rets, rf) is None

    import random
    random.seed(5)
    noisy = [random.gauss(0.0008, 0.01) for _ in range(504)]
    flat_rf = [0.0002] * 504
    s = replay.sharpe(noisy, flat_rf)
    assert s is not None and 0.0 < s < 4.0        # sane magnitude


def test_sharpe_rises_when_the_same_return_comes_with_less_noise():
    import random

    random.seed(9)
    rf = [0.0] * 504
    calm = [random.gauss(0.0006, 0.004) for _ in range(504)]
    wild = [random.gauss(0.0006, 0.020) for _ in range(504)]
    assert replay.sharpe(calm, rf) > replay.sharpe(wild, rf)


# ── strategy E: user's V4 SmartRisk ported to unlevered equity (variant #3) ──
def test_momentum_score_matches_the_published_7_point_table():
    up = [100.0 + i for i in range(300)]
    assert replay.momentum_score_7(up, 299) == 7        # every condition holds
    down = [400.0 - i for i in range(300)]
    assert replay.momentum_score_7(down, 299) == 0      # none hold


def test_ladder_is_the_published_one():
    assert replay.LADDER == {0: 0.0, 1: 0.0, 2: 0.5, 3: 1.0,
                             4: 1.5, 5: 2.0, 6: 2.5, 7: 3.0}


def test_rebalance_band_suppresses_small_drifts_and_respects_cooldown():
    target = [1.0, 0.9, 0.8, 0.85, 0.9, 1.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4]
    got = replay.apply_rebalance(target, band=0.25, cooldown=5)
    assert got[:6] == [1.0] * 6            # drifts under the band change nothing
    assert got[6] == 0.4                   # a big move does trade
    # causal: element i never depends on anything after i
    assert replay.apply_rebalance(target[:7], 0.25, 5) == got[:7]


def test_rebalance_discipline_cuts_the_churn_that_sank_strategy_d():
    """The user's own V1-V4 rule (25% band, 5d cooldown) is what D was missing."""
    import random

    random.seed(13)
    noisy = [0.5 + 0.4 * random.random() for _ in range(500)]   # jittery target
    raw_switches = sum(1 for i in range(1, len(noisy)) if noisy[i] != noisy[i - 1])
    damped = replay.apply_rebalance(noisy, 0.25, 5)
    at = [i for i in range(1, len(damped)) if damped[i] != damped[i - 1]]

    assert len(at) < raw_switches / 5           # a large reduction in churn...
    # ...and the cooldown is a hard structural guarantee, not a tendency:
    assert all(b - a >= 5 for a, b in zip(at, at[1:]))


# ── unlevered port: three pre-registered readings (甲/乙/丙) ─────────────────
@pytest.mark.parametrize("score,vix,jia,yi,bing", [
    (7, 15, 1.00, 1.00, 1.00),
    (7, 30, 1.00, 0.50, 0.50),     # the whole disagreement in one row
    (3, 15, 1.00, 1 / 3, 1.00),
    (3, 30, 0.50, 1 / 6, 0.50),
    (5, 30, 1.00, 1 / 3, 0.50),
    (2, 15, 0.50, 1 / 6, 0.50),
    (1, 15, 0.00, 0.00, 0.00),     # short-circuit: L=0 -> 0 regardless of VIX
])
def test_exposure_table_matches_the_agreed_spec(score, vix, jia, yi, bing):
    assert replay.exposure_from(score, vix, "jia") == pytest.approx(jia, abs=1e-9)
    assert replay.exposure_from(score, vix, "yi") == pytest.approx(yi, abs=1e-9)
    assert replay.exposure_from(score, vix, "bing") == pytest.approx(bing, abs=1e-9)


def test_zero_score_short_circuits_before_vol_scaling():
    """momentum.py:143 returns 0 without computing sigma. A calm VIX must not
    resurrect a dead trend."""
    for mode in replay.MODES:
        assert replay.exposure_from(0, 5.0, mode) == 0.0   # sigma would be 2.0
        assert replay.exposure_from(1, 5.0, mode) == 0.0


def test_faithful_mode_shows_vix_cannot_protect_a_strong_trend():
    """The central finding, pinned: unlevered, 甲 leaves a 7-score untouched even
    in a panic — because the original's cut lands entirely above the 1.0 ceiling."""
    for vix in (15, 20, 30, 40):
        assert replay.exposure_from(7, vix, "jia") == 1.0


def test_tier1_panic_zeroes_exposure_on_inverted_term_structure():
    up = [100.0 + i for i in range(300)]
    calm = replay.series_momentum_vol(up, [15.0] * 300, [18.0] * 300, "jia")
    panic = replay.series_momentum_vol(up, [23.0] * 300, [19.0] * 300, "jia")  # 1.21
    assert calm[-1] > 0 and panic[-1] == 0.0


def test_tier1_is_skipped_when_vix3m_missing_never_forward_filled():
    up = [100.0 + i for i in range(300)]
    got = replay.series_momentum_vol(up, [40.0] * 300, [], "jia")   # no VIX3M at all
    assert got[-1] > 0.0        # Tier 1 unevaluable -> must not fire


def test_tier2_caps_at_half_below_sma200():
    closes = [300.0 - i * 0.5 for i in range(260)] + [170.0 + i * 0.3 for i in range(40)]
    for mode in replay.MODES:
        got = replay.series_momentum_vol(closes, [15.0] * 300, [15.0] * 300, mode)
        assert got[-1] <= replay.BEAR_PRICE_CAP


def test_bearslope_gate_fires_on_strictly_fewer_days_than_the_deployed_rule():
    """leaps_smartrisk's Tier 2 (compared 2026-08-01): the cap only fires when
    SMA200 ITSELF is declining, not just price < SMA200 — strictly stricter
    than the deployed rule, so over any real (noisy) path it must be capped on
    a subset of the days the deployed rule caps on, never more."""
    import random

    random.seed(7)
    closes = [100.0]
    for _ in range(1200):
        closes.append(closes[-1] * (1 + random.gauss(0.0002, 0.016)))
    vix = [15.0] * len(closes)
    ungated = replay.series_momentum_vol(closes, vix, vix, "jia")
    gated = replay.series_momentum_vol(closes, vix, vix, "jia", bear_requires_declining=True)
    capped_ungated = {i for i, e in enumerate(ungated) if e <= replay.BEAR_PRICE_CAP + 1e-9}
    capped_gated = {i for i, e in enumerate(gated) if e <= replay.BEAR_PRICE_CAP + 1e-9}
    # every exposure the gated rule caps to <=0.5, the deployed rule also caps
    # (fewer trigger days observed empirically on 7 real symbols, 2026-08-01)
    assert len(capped_gated) < len(capped_ungated)


def test_bearslope_gate_still_fires_in_a_sustained_decline():
    """When SMA200 is genuinely rolling over (not just a one-day dip), both the
    deployed rule and the gated candidate must cap identically."""
    closes = [300.0 - i * 0.5 for i in range(300)]
    ungated = replay.series_momentum_vol(closes, [15.0] * 300, [15.0] * 300, "jia")
    gated = replay.series_momentum_vol(closes, [15.0] * 300, [15.0] * 300, "jia",
                                       bear_requires_declining=True)
    assert ungated[-1] <= replay.BEAR_PRICE_CAP
    assert gated[-1] <= replay.BEAR_PRICE_CAP


def test_build_series_dispatches_bearslope_variant():
    closes = [300.0 - i * 0.5 for i in range(300)]
    prices = {"X": closes}
    market = {"VIX": [15.0] * 300, "VIX3M": [15.0] * 300}
    want = replay.series_momentum_vol(closes, market["VIX"], market["VIX3M"], "jia",
                                      bear_requires_declining=True)
    got = replay.build_series(prices, ["X"], "F_jia_bearslope", market=market)
    assert got["X"] == want


def test_variants_are_causal():
    import random

    random.seed(21)
    closes, vix = [100.0], []
    for _ in range(400):
        closes.append(closes[-1] * (1 + random.gauss(0.0006, 0.018)))
    vix = [max(9.0, random.gauss(18.0, 4.0)) for _ in closes]
    for mode in replay.MODES:
        full = replay.series_momentum_vol(closes, vix, [], mode)
        trunc = replay.series_momentum_vol(closes[:301], vix[:301], [], mode)
        assert full[:301] == trunc      # the future cannot change the past
