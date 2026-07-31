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
