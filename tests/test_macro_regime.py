"""Deterministic macro indicator + regime layer (docs/MACRO_ANALYST.md).

Fully hermetic: every series here is synthetic, so no network and no pandas.
That is the point of keeping indicators.py/regime.py free of I/O — the numbers
have to be verifiable before any LLM ever sees them.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ats.agents.macro import indicators as ind, regime
from ats.schemas.macro_strategy import AxisInput, MacroReview

END = date(2026, 7, 30)


def daily(n: int, fn, end: date = END):
    """n daily points ending at `end`; fn(i) -> value, i counted from oldest."""
    return [(end - timedelta(days=n - 1 - i), float(fn(i))) for i in range(n)]


def monthly(n: int, fn, end: date = END):
    return [(end - timedelta(days=30 * (n - 1 - i)), float(fn(i))) for i in range(n)]


def weekly(n: int, fn, end: date = END):
    return [(end - timedelta(days=7 * (n - 1 - i)), float(fn(i))) for i in range(n)]


# --------------------------------------------------------------------------- #
# indicators
# --------------------------------------------------------------------------- #
def test_yield_change_reads_in_basis_points_and_price_in_percent():
    # A yield rising 1.00 -> 2.00 over 1095d moves ~0.0913pp in 30d = ~9bp... but
    # the point under test is the UNIT: pct -> bp, price -> %.
    y = ind.reading("real_10y", daily(400, lambda i: 1.0 + i * 0.001), unit="pct")
    assert y.d_1m == pytest.approx(3.0, abs=0.2)      # 30 * 0.001pp = 3bp

    p = ind.reading("oil_wti", daily(400, lambda i: 100.0 * (1.001 ** i)), unit="price")
    assert p.d_1m == pytest.approx(3.04, abs=0.1)     # 1.001**30 - 1 ≈ 3.04%


def test_zscore_and_percentile_use_the_level_distribution():
    r = ind.reading("x", daily(1095, lambda i: float(i)), unit="index")
    # Latest value is the max of a uniform ramp: z = sqrt(3), percentile = 100.
    assert r.z_3y == pytest.approx(1.73, abs=0.02)
    assert r.pct_10y == 100.0


def test_staleness_is_per_frequency_not_absolute():
    # 83 days old: badly stale for a daily series, entirely normal for a monthly
    # one (FRED dates monthly data to the 1st of the reference month and the
    # print lands ~a month after the month ends).
    old = [(date(2026, 5, 1), 5.0), (date(2026, 5, 8), 5.1)]
    assert ind.reading("w", old, freq="daily", as_of=END).stale is True
    assert ind.reading("m", old, freq="monthly", as_of=END).stale is False
    # But a genuinely abandoned monthly series still trips it.
    ancient = [(date(2025, 1, 1), 5.0)]
    assert ind.reading("m", ancient, freq="monthly", as_of=END).stale is True


def test_missing_series_yields_a_stale_reading_rather_than_disappearing():
    r = ind.reading("gone", None)
    assert r.stale is True and r.level is None


def test_catchup_run_detects_missed_nfp_release_and_unemployment():
    """The event-day trigger is an accelerator, not the only ingestion path."""
    prior = MacroReview(
        name="macro", as_of=datetime(2026, 8, 1, tzinfo=timezone.utc), regime="old",
        indicators=[ind.IndicatorReading(
            key="unemployment", label="失业率", unit="pct", level=4.2,
            as_of=date(2026, 6, 1), source="fred:UNRATE")])
    current = [
        ind.IndicatorReading(key="unemployment", label="失业率", unit="pct", level=4.1,
                             d_1m=-10.0, as_of=date(2026, 7, 1), source="fred:UNRATE"),
        ind.IndicatorReading(key="payrolls", label="非农就业（千人）", unit="level",
                             level=160100.0, d_1m=125.0, as_of=date(2026, 7, 1),
                             source="fred:PAYEMS"),
    ]
    event = SimpleNamespace(date=date(2026, 8, 7), kind="nfp", triggers=["macro"])
    deltas = ind.detect_deltas(current, prior, events=[event], through=date(2026, 8, 8))

    by_key = {d.key: d for d in deltas}
    assert set(by_key) == {"unemployment", "payrolls"}
    assert by_key["unemployment"].release_date == date(2026, 8, 7)
    assert by_key["unemployment"].observation_date == date(2026, 7, 1)
    assert by_key["payrolls"].period_change == 125.0


def test_vintage_snapshot_detects_revisions_to_prior_observations():
    prior = MacroReview(
        name="macro", as_of=datetime(2026, 8, 1, tzinfo=timezone.utc), regime="old",
        indicators=[ind.IndicatorReading(
            key="payrolls", label="非农就业（千人）", unit="level", level=160000,
            as_of=date(2026, 6, 1), recent_observations={"2026-06-01": 160000})])
    current = [ind.IndicatorReading(
        key="payrolls", label="非农就业（千人）", unit="level", level=160120,
        as_of=date(2026, 7, 1),
        recent_observations={"2026-06-01": 159980, "2026-07-01": 160120})]
    deltas = ind.detect_deltas(current, prior, through=date(2026, 8, 8))
    assert any(d.change_kind == "revision" and d.observation_date == date(2026, 6, 1)
               for d in deltas)
    assert any(d.change_kind == "new_release" and d.observation_date == date(2026, 7, 1)
               for d in deltas)


def test_lookback_is_date_based_so_it_works_across_frequencies():
    # Monthly series: a 30-day lookback must find the previous month's print,
    # not "30 rows ago" (which would not exist).
    m = monthly(24, lambda i: 100.0 + i)
    r = ind.reading("core_pce", m, unit="index")
    # index unit -> percent change: 123/122 - 1 = 0.82%
    assert r.d_1m == pytest.approx(0.82, abs=0.02)


def test_monthly_three_month_change_spans_three_months_not_four():
    """FRED dates monthly data to the 1st of the reference month.

    A 90-day lookback from 1 May resolves to 31 Jan; "last at or before" then
    picks the 1 Jan print, reporting a FOUR-month change as three. Live run
    2026-07-31: core PCE showed d_3m +1.3% (≈5.3% annualised) while the axis's
    own 3m-annualised figure was 2.89% — the same series disagreeing with itself
    in one report.
    """
    pts = [(date(2026, m, 1), 100.0 + m) for m in range(1, 6)]     # +1 per month
    r = ind.reading("core_pce", pts, unit="index", freq="monthly")
    # May(105) vs Feb(102) = 3 months = +2.94%; the bug gave May vs Jan = +3.96%.
    assert r.d_3m == pytest.approx(2.94, abs=0.05)


def test_value_near_and_value_asof_have_different_jobs():
    pts = [(date(2026, 1, 1), 1.0), (date(2026, 2, 1), 2.0)]
    # as-of never looks ahead of the target...
    assert ind.value_asof(pts, date(2026, 1, 31)) == 1.0
    # ...but a Δ window wants whichever observation is actually closest.
    assert ind.value_near(pts, date(2026, 1, 31)) == 2.0


def test_change_z_separates_a_shock_from_a_trend():
    # Flat series with one abrupt jump at the end -> large 1m change z.
    pts = daily(400, lambda i: 100.0 + (30.0 if i >= 395 else 0.0))
    assert abs(ind.change_z(pts, 30)) > 2.0
    # Steady drift (with a little jitter, as any real series has) -> the latest
    # 1m change is unremarkable against its own history.
    drift = daily(400, lambda i: 100.0 + i * 0.5 + (0.3 if i % 3 else -0.3))
    assert abs(ind.change_z(drift, 30)) < 1.0


def test_change_z_is_none_when_the_change_has_no_variance():
    # A perfectly linear series has an identical change every window: "is this
    # move unusual?" is genuinely undefined, so we return None rather than 0.0,
    # which shock_vs_trend then skips instead of reporting a fake trend.
    perfect = daily(400, lambda i: 100.0 + i * 0.5)
    assert ind.change_z(perfect, 30) is None


# --------------------------------------------------------------------------- #
# axes
# --------------------------------------------------------------------------- #
def test_sahm_rule_drives_the_growth_axis_negative():
    # Unemployment flat at 3.8 then rising to 4.5 -> Sahm gap well above 0.5pp.
    un = monthly(18, lambda i: 3.8 if i < 12 else 3.8 + (i - 11) * 0.12)
    score, inputs = regime.growth_axis({"unemployment": un}, regime._cfg(None))
    sahm = next(i for i in inputs if i.key == "sahm")
    assert sahm.value >= 0.5 and sahm.score == -1.0
    assert score < 0


def test_three_month_payroll_average_feeds_growth_axis():
    pays = monthly(6, lambda i: 100000 + i * 225)
    score, inputs = regime.growth_axis({"payrolls": pays}, regime._cfg(None))
    nfp = next(i for i in inputs if i.key == "nfp_3m_avg")
    assert nfp.value == 225.0 and nfp.score > 0 and score > 0


def test_core_pce_momentum_drives_the_inflation_axis():
    # Index accelerating in the last quarter -> 3m annualised above 12m YoY.
    pce = monthly(15, lambda i: 100.0 * (1.002 ** i) * (1.004 ** max(0, i - 11)))
    score, inputs = regime.inflation_axis({"core_pce": pce}, {}, regime._cfg(None))
    mom = next(i for i in inputs if i.key == "pce_momentum")
    assert mom.value > 0 and score > 0


# --------------------------------------------------------------------------- #
# quadrant + hysteresis
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("growth,inflation,expected", [
    (0.5, -0.5, "goldilocks"),
    (0.5, 0.5, "reflation"),
    (-0.5, 0.5, "stagflation"),
    (-0.5, -0.5, "deflation"),
])
def test_quadrant_grid_matches_the_documented_2x2(growth, inflation, expected):
    assert regime._quadrant_from_axes(growth, inflation, 0.25) == expected


def _inputs(*scores):
    return [AxisInput(key=f"k{n}", score=s) for n, s in enumerate(scores)]


def test_weak_axis_falls_into_transition_not_a_forced_choice():
    cfg = regime._cfg(None)
    q, state, _, reason = regime.classify(0.10, 0.55, _inputs(-0.1, 0.1),
                                          _inputs(0.6, 0.5), cfg)
    assert q == "transition" and state == "insufficient"
    # "complete data that genuinely says nothing" must not read as "no data".
    assert "信号中性" in reason


def test_contradictory_inputs_force_transition_even_when_the_mean_looks_clean():
    # -0.9 and +0.9 average to 0.0; reporting that as "neutral" would hide a real
    # disagreement between two inputs of the same axis.
    cfg = regime._cfg(None)
    q, state, _, reason = regime.classify(0.0, 0.55, _inputs(-0.9, 0.9),
                                          _inputs(0.6, 0.5), cfg)
    assert q == "transition" and state == "insufficient"
    assert "矛盾" in reason


def test_too_few_live_inputs_is_insufficient_rather_than_a_guess():
    cfg = regime._cfg(None)
    q, state, _, reason = regime.classify(-0.55, 0.55, _inputs(-0.6),
                                          _inputs(0.6, 0.5), cfg)
    assert q == "transition" and state == "insufficient"
    assert "输入不足" in reason


def test_a_flip_is_provisional_until_a_second_review_agrees():
    cfg = regime._cfg(None)
    g, f = _inputs(-0.6, -0.5), _inputs(0.6, 0.5)
    q1, s1, w1, _ = regime.classify(-0.55, 0.55, g, f, cfg)
    assert (q1, s1, w1) == ("stagflation", "provisional", 1)
    q2, s2, w2, _ = regime.classify(-0.55, 0.55, g, f, cfg, prior_quadrant=q1,
                                    prior_state=s1, prior_weeks=w1)
    assert (q2, s2, w2) == ("stagflation", "confirmed", 2)


def test_hysteresis_holds_a_confirmed_call_through_the_entry_band():
    """Entering needs |axis|>0.25; staying only needs >0.15.

    Without this the post-forward-guidance noise in the front end would flip the
    quadrant every other week (docs/MACRO_ANALYST.md §4.3).
    """
    cfg = regime._cfg(None)
    g, f = _inputs(-0.6, -0.5), _inputs(0.6, 0.5)
    held = regime.classify(-0.20, 0.55, g, f, cfg, prior_quadrant="stagflation",
                           prior_state="confirmed", prior_weeks=3)
    assert held[:3] == ("stagflation", "confirmed", 4)
    assert "迟滞" in held[3]
    # A merely provisional prior gets no such protection.
    dropped = regime.classify(-0.20, 0.55, g, f, cfg, prior_quadrant="stagflation",
                              prior_state="provisional", prior_weeks=1)
    assert dropped[0] == "transition"


# --------------------------------------------------------------------------- #
# decomposition
# --------------------------------------------------------------------------- #
def _reading(key, d_1m, z=None, label=""):
    return ind.IndicatorReading(key=key, label=label, unit="pct", level=2.0,
                                d_1m=d_1m, z_3y=z)


@pytest.mark.parametrize("d_real,d_be,marker", [
    (25.0, 20.0, "最差组合"),
    (25.0, -20.0, "实际利率驱动"),
    (-25.0, 20.0, "通胀预期驱动"),
    (-25.0, -20.0, "通缩"),
])
def test_rate_decomposition_classifies_all_four_combinations(d_real, d_be, marker):
    by_key = {"real_10y": _reading("real_10y", d_real),
              "breakeven_10y": _reading("breakeven_10y", d_be),
              "ust_10y": _reading("ust_10y", d_real + d_be)}
    dec = regime.decompose(by_key, 0.0, 0.0, regime._cfg(None))
    assert marker in dec.classification


def test_decomposition_has_a_dead_band_so_noise_is_not_read_as_direction():
    """A +4bp breakeven is noise, not "rising inflation expectations".

    Real data caught this: Δreal +23bp with Δbreakeven +4bp was being classified
    "最差组合" purely because the 4-way table had no flat zone.
    """
    cfg = regime._cfg(None)
    by_key = {"real_10y": _reading("real_10y", 23.0),
              "breakeven_10y": _reading("breakeven_10y", 4.0),
              "ust_10y": _reading("ust_10y", 27.0)}
    dec = regime.decompose(by_key, 0.0, 0.0, cfg)
    assert "通胀补偿持平" in dec.classification and "最差组合" not in dec.classification

    # Both legs flat -> say so, rather than picking a corner of the 2x2.
    quiet = {"real_10y": _reading("real_10y", 1.0),
             "breakeven_10y": _reading("breakeven_10y", -2.0),
             "ust_10y": _reading("ust_10y", -1.0)}
    assert "基本持平" in regime.decompose(quiet, 0.0, 0.0, cfg).classification


def test_sahm_is_one_sided_and_never_reports_good_news():
    """Sahm = current minus trailing min, so it cannot go below zero.

    A two-sided map scored an ordinary 0.17 gap as +0.33 "growth improving",
    biasing the axis upward every normal month. It detects deterioration only.
    """
    cfg = regime._cfg(None)
    calm = monthly(18, lambda i: 4.0 + (0.17 if i >= 15 else 0.0))
    _score, inputs = regime.growth_axis({"unemployment": calm}, cfg)
    sahm = next(i for i in inputs if i.key == "sahm")
    assert sahm.value == pytest.approx(0.17, abs=0.02)
    assert -0.5 < sahm.score <= 0.0        # negative-or-zero, never positive


def test_zero_centred_series_use_absolute_change_not_percent():
    """CFNAI sits at -0.02 and flips sign; a percent change on it is division
    noise (real data produced a "-89.5%" monthly move)."""
    pts = monthly(24, lambda i: -0.30 + i * 0.02)
    r = ind.reading("cfnai", pts, unit="level", freq="monthly")
    assert r.d_1m == pytest.approx(0.02, abs=0.005)


def test_falling_real_yield_distinguishes_benign_disinflation_from_recession():
    by_key = {"real_10y": _reading("real_10y", -25.0),
              "breakeven_10y": _reading("breakeven_10y", -20.0),
              "ust_10y": _reading("ust_10y", -45.0)}
    cfg = regime._cfg(None)
    benign = regime.decompose(by_key, growth=0.4, inflation=-0.5, cfg=cfg)
    assert "良性反通胀" in benign.real_yield_cause
    recession = regime.decompose(by_key, growth=-0.6, inflation=-0.5, cfg=cfg)
    assert "衰退驱动" in recession.real_yield_cause
    unclear = regime.decompose(by_key, growth=-0.05, inflation=0.5, cfg=cfg)
    assert "成因未定" in unclear.real_yield_cause


# --------------------------------------------------------------------------- #
# alerts
# --------------------------------------------------------------------------- #
def test_credit_equity_divergence_fires_only_when_equities_are_still_near_highs():
    cfg = regime._cfg(None)
    by_key = {"hy_oas": _reading("hy_oas", 80.0)}
    near_high = {"spx": daily(120, lambda i: 5000.0 + i * 0.1)}     # at its high
    assert any("信用-股票背离" in a for a in regime.alerts(near_high, by_key, cfg))
    # Same credit widening, but equities already sold off -> no divergence to flag.
    sold_off = {"spx": daily(120, lambda i: 5000.0 - i * 5.0)}
    assert not any("信用-股票背离" in a for a in regime.alerts(sold_off, by_key, cfg))


def test_worst_combo_alert_needs_both_legs():
    cfg = regime._cfg(None)
    both = {"real_10y": _reading("real_10y", 25.0),
            "breakeven_10y": _reading("breakeven_10y", 20.0)}
    assert any("最差组合" in a for a in regime.alerts({}, both, cfg))
    one_leg = {"real_10y": _reading("real_10y", 25.0),
               "breakeven_10y": _reading("breakeven_10y", 5.0)}
    assert not any("最差组合" in a for a in regime.alerts({}, one_leg, cfg))


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #
def _stagflation_series():
    return {
        "unemployment": monthly(18, lambda i: 3.8 if i < 12 else 3.8 + (i - 11) * 0.12),
        "initial_claims": weekly(30, lambda i: 200_000 + i * 3_000),
        "continuing_claims": weekly(30, lambda i: 1_800_000 + i * 20_000),
        "cfnai": monthly(12, lambda i: -0.5),
        "core_pce": monthly(15, lambda i: 100.0 * (1.002 ** i) * (1.004 ** max(0, i - 11))),
        "gasoline": weekly(20, lambda i: 3.0 * (1.02 ** i)),
        "real_10y": daily(400, lambda i: 1.0 + i * 0.002),
        # Slope chosen so the 1m move clears the 5bp dead band — otherwise the
        # decomposition (correctly) calls the leg flat rather than rising.
        "breakeven_10y": daily(400, lambda i: 2.0 + i * 0.003),
        "ust_10y": daily(400, lambda i: 3.0 + i * 0.003),
        "hy_oas": daily(400, lambda i: 3.0 + i * 0.004),
        "oil_wti": daily(400, lambda i: 70.0 * (1.001 ** i)),
        "dxy": daily(400, lambda i: 100.0 * (1.0005 ** i)),
        "spx": daily(400, lambda i: 5000.0 + i * 0.5),
    }


def test_assess_end_to_end_produces_a_coherent_stagflation_call():
    series = _stagflation_series()
    spec = {k: (k.upper(), k, "pct" if k.endswith(("_10y", "oas")) else "index", "daily")
            for k in series}
    readings = ind.build_readings(series, spec)
    out = regime.assess(series, readings)

    assert out["growth_axis"] < 0 and out["inflation_axis"] > 0
    assert out["quadrant"] == "stagflation"
    assert out["quadrant_state"] == "provisional"      # first observation
    assert out["decomposition"] is not None
    assert "最差组合" in out["decomposition"].classification
    assert out["focus_keys"] == regime.FOCUS_BY_QUADRANT["stagflation"]
    # Every axis vote is retained so a human can audit the call.
    assert {i.key for i in out["axis_inputs"]} >= {"sahm", "initial_claims", "cfnai"}


def test_assess_degrades_to_transition_when_series_are_missing():
    out = regime.assess({}, [])
    assert out["quadrant"] == "transition" and out["quadrant_state"] == "insufficient"
    assert out["decomposition"] is None


def test_review_payload_written_before_this_layer_still_revalidates():
    old = ('{"name":"macro","as_of":"2026-07-01T00:00:00Z",'
           '"regime":"risk-on","summary":"s"}')
    r = MacroReview.model_validate_json(old)
    assert r.quadrant == "transition" and r.quadrant_state == "insufficient"
    assert r.falsifier == "" and r.indicators == []


# --------------------------------------------------------------------------- #
# review wiring
# --------------------------------------------------------------------------- #
def _patch_series(monkeypatch, series):
    """Make data.macro look like it returned `series`, with no network.

    Also stubs assemble.build: with live_data=True it would otherwise fetch the
    macro dashboard, download the FactSet PDF and run a Tavily search per theme.
    Same stubbing pattern the rest of tests/test_macro_strategy.py uses.
    """
    from ats.agents.macro import assemble
    from ats.data import macro as macro_data

    monkeypatch.setattr(macro_data, "fetch_series", lambda years=11: series)
    monkeypatch.setattr(
        macro_data, "series_spec",
        lambda: {k: (k.upper(), k, "pct" if k.endswith(("_10y", "oas")) else "index",
                     "daily") for k in series})
    monkeypatch.setattr(assemble, "build",
                        lambda cfg, live_data=True: assemble.MacroContext(cfg=cfg))


def test_no_llm_run_still_computes_and_persists_the_quadrant(monkeypatch):
    """`--no-llm` has to carry the deterministic layer — verifying the numbers
    before a model ever sees them is the entire point of that flag."""
    from ats.agents.macro import review as macro_review
    from ats.memory import get_store

    _patch_series(monkeypatch, _stagflation_series())
    r = macro_review.run("macro", use_llm=False, live_data=True)

    assert r.regime == "(no-llm)"          # stub guard still blocks injection
    assert r.quadrant == "stagflation"
    assert r.axis_inputs and r.indicators
    assert get_store().latest_macro_review("macro").quadrant == "stagflation"


def test_same_day_rerun_does_not_fake_a_second_confirmation_week(monkeypatch):
    from ats.agents.macro import review as macro_review

    _patch_series(monkeypatch, _stagflation_series())
    first = macro_review.run("macro", use_llm=False, live_data=True)
    assert (first.quadrant_state, first.quadrant_weeks) == ("provisional", 1)
    second = macro_review.run("macro", use_llm=False, live_data=True)
    assert (second.quadrant_state, second.quadrant_weeks) == ("provisional", 1)


def test_hysteresis_state_carries_across_distinct_review_dates(monkeypatch):
    from ats.agents.macro import review as macro_review

    _patch_series(monkeypatch, _stagflation_series())
    times = iter([
        datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        datetime(2026, 8, 8, 8, tzinfo=timezone.utc),
    ])
    monkeypatch.setattr(macro_review, "_now", lambda: next(times))
    first = macro_review.run("macro", use_llm=False, live_data=True)
    second = macro_review.run("macro", use_llm=False, live_data=True)
    assert (first.quadrant_state, first.quadrant_weeks) == ("provisional", 1)
    assert (second.quadrant_state, second.quadrant_weeks) == ("confirmed", 2)


def test_offline_run_skips_the_indicator_layer_without_failing(monkeypatch):
    from ats.agents.macro import assemble
    from ats.agents.macro import review as macro_review

    monkeypatch.setattr(assemble, "build",
                        lambda cfg, live_data=True: assemble.MacroContext(cfg=cfg))
    r = macro_review.run("macro", use_llm=False, live_data=False)
    assert r.quadrant == "transition" and r.indicators == []


def test_det_block_tells_the_model_the_numbers_are_not_its_to_rewrite():
    from ats.agents.macro import review as macro_review

    series = _stagflation_series()
    spec = {k: (k.upper(), k, "pct" if k.endswith(("_10y", "oas")) else "index", "daily")
            for k in series}
    det = regime.assess(series, ind.build_readings(series, spec))
    block = macro_review._det_block(det)

    assert "不得改写或重算" in block
    assert "stagflation" in block
    assert "名义利率分解" in block and "象限判定的逐项依据" in block


def _saved_review(**kw):
    """Persist a review carrying a real deterministic layer, and return it."""
    from ats.memory import get_store

    series = _stagflation_series()
    spec = {k: (k.upper(), k, "pct" if k.endswith(("_10y", "oas")) else "index", "daily")
            for k in series}
    det = regime.assess(series, ind.build_readings(series, spec))
    r = MacroReview(name="macro", as_of=datetime(2026, 7, 30, tzinfo=timezone.utc),
                    regime="risk-off，滞胀初期", rate_path="维持不动",
                    asset_implications="股承压", **{**det, **kw})
    get_store().save_macro_review(r)
    return r


def test_quadrant_reaches_all_five_downstream_injection_points():
    """The deterministic call must survive into every consumer of the review.

    These five are the only paths by which macro work reaches a trade decision;
    a field that stops at the report helps nobody.
    """
    from ats.agents.chief import assemble as chief_assemble
    from ats.agents.macro import context as macro_context

    _saved_review()

    # 1-2. PEAD prep and monitor.
    assert "stagflation" in macro_context.prep_block("NVDA", "macro")
    assert "stagflation" in macro_context.monitor_hint("macro")
    # 3. Sector review (the industry analyst rotates layers on this).
    assert "stagflation" in macro_context.sector_block("macro")
    # 4. Chief.
    assert "stagflation" in chief_assemble._macro_block()
    # 5. Risk officer memo — a widening spread or "worst combination" read is a
    #    risk input, not colour, so the quadrant has to reach this prompt too.
    from datetime import datetime as _dt

    from ats.agents.risk_officer import review as ro
    from ats.schemas.risk import RiskReview

    prompt = ro._context(RiskReview(as_of=_dt(2026, 7, 30, tzinfo=timezone.utc)))
    assert "stagflation" in prompt


def test_monitor_hint_stays_within_its_character_budget():
    # 280 chars is the materiality-calibration budget; the brief form drops the
    # alerts precisely so a long one cannot crowd out the regime itself.
    from ats.agents.macro import context as macro_context

    _saved_review()
    hint = macro_context.monitor_hint("macro", max_chars=280)
    assert len(hint) <= 280 and "象限" in hint


def test_stub_reviews_are_still_never_injected_downstream():
    """The pre-existing guard must survive the new fields.

    A `(no-llm)` run now carries a real quadrant, which makes it *more* tempting
    to inject — but its narrative is empty, so downstream would read a regime
    line that says nothing.
    """
    from ats.agents.macro import context as macro_context
    from ats.memory import get_store

    get_store().save_macro_review(MacroReview(
        name="macro", as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
        regime="(no-llm)", quadrant="stagflation", quadrant_state="confirmed"))
    assert macro_context.prep_block("NVDA", "macro") == ""
    assert macro_context.monitor_hint("macro") == ""
    assert macro_context.sector_block("macro") == ""


def test_a_review_without_the_deterministic_layer_injects_no_quadrant_noise():
    """Offline/legacy reviews must not emit an empty "象限 transition" line."""
    from ats.agents.macro import context as macro_context
    from ats.memory import get_store

    get_store().save_macro_review(MacroReview(
        name="macro", as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
        regime="risk-on", rate_path="持"))
    assert "象限" not in macro_context.monitor_hint("macro")
    assert "象限" not in macro_context.sector_block("macro")


def test_report_separates_computed_facts_from_model_narrative():
    from ats.agents.macro import report
    from ats.schemas.macro_strategy import MacroConfig

    r = _saved_review(summary="总评", falsifier="初请连续两周高于 26 万",
                      top_risks=["能源二次上涨"])
    out = report.render(r, MacroConfig(name="macro", label="宏观"))

    assert "📐 确定性读数（代码算出，非模型判断）" in out
    assert "象限判定的逐项依据" in out and "名义利率分解" in out
    assert "附录：指标读数" in out
    assert "证伪条件" in out and "初请连续两周高于 26 万" in out


def test_report_puts_data_and_conclusion_delta_before_current_state():
    from ats.agents.macro import report
    from ats.schemas.macro_strategy import MacroConfig, MacroDataDelta

    r = MacroReview(
        name="macro", as_of=datetime(2026, 8, 8, tzinfo=timezone.utc), regime="new",
        comparison_as_of=datetime(2026, 8, 1, tzinfo=timezone.utc),
        conclusion_delta="结论由偏谨慎转为中性。",
        data_deltas=[MacroDataDelta(
            key="payrolls", label="非农就业（千人）", unit="level",
            release_date=date(2026, 8, 7), observation_date=date(2026, 7, 1),
            current_level=160100, period_change=125)],
    )
    out = report.render(r, MacroConfig(name="macro", label="宏观"))
    assert "本期最重要：宏观数据与结论 Delta" in out
    assert "2026-08-07" in out and "2026-07-01" in out
    assert out.index("宏观数据与结论 Delta") < out.index("行业状态")


def test_report_omits_deterministic_sections_when_there_is_no_data():
    from ats.agents.macro import report
    from ats.schemas.macro_strategy import MacroConfig

    r = MacroReview(name="macro", as_of=datetime(2026, 7, 30, tzinfo=timezone.utc),
                    regime="risk-on", summary="s")
    out = report.render(r, MacroConfig(name="macro", label="宏观"))
    assert "📐 确定性读数" not in out and "附录：指标读数" not in out
    assert "## 行业状态（regime）" in out          # narrative still renders


def test_skill_states_the_numbers_are_not_the_models_to_rewrite():
    """The SKILL is the model's contract; these clauses are load-bearing."""
    from pathlib import Path

    text = Path("src/ats/skills/macro-strategist/SKILL.md").read_text(encoding="utf-8")
    assert "不得改写、重算" in text                  # deterministic layer is read-only
    assert "象限判定不可推翻" in text
    assert "禁止对单个公司的盈利" in text            # role boundary vs 基本面分析师
    assert "falsifier" in text and "可观测" in text
    assert "FactSet 不能只复述数字" in text
    assert "增长质量" in text and "集中" in text and "GAAP/Non-GAAP" in text
    assert "区分数据事实与模型解释" in text
    assert "## Security" in text


def test_macro_view_coerces_a_stringified_themes_array():
    """Reproduces the live 2026-07-31 failure.

    sonnet returned `themes` as a JSON *string*; list validation rejected the
    whole review, run() fell back to the prior week, and five downstream agents
    silently consumed stale macro background. Coerce rather than discard.
    """
    import json

    from ats.agents.macro.outputs import MacroReviewLLMView

    themes = json.dumps([{"key": "fed_policy", "direction": "偏紧",
                          "transmission": "实际利率↑→估值压缩", "signal": "risk-off"}])
    tilts = json.dumps([{"sector": "半导体", "stance": "低配", "rationale": "久期风险"}])
    v = MacroReviewLLMView(regime="risk-off", themes=themes, sector_tilts=tilts,
                           top_risks='["能源二次上涨", "信用利差走阔"]')
    assert len(v.themes) == 1 and v.themes[0].key == "fed_policy"
    assert len(v.sector_tilts) == 1 and v.sector_tilts[0].stance == "低配"
    assert v.top_risks == ["能源二次上涨", "信用利差走阔"]

    # Real arrays must still pass through untouched.
    plain = MacroReviewLLMView(regime="r", themes=[{"key": "growth"}], top_risks=["a"])
    assert plain.themes[0].key == "growth" and plain.top_risks == ["a"]


def test_a_failed_llm_run_is_reported_and_writes_no_report(monkeypatch, tmp_path):
    """A fallback to the prior review must not masquerade as a fresh run.

    run() returns the stored prior review on LLM failure; writing a report for it
    would rewrite that older day's file under its own date and read as success.
    """
    from ats.agents.macro import assemble
    from ats.agents.macro import report as macro_report
    from ats.agents.macro import review as macro_review
    from ats.memory import get_store
    from ats.runtime import cli
    from ats.schemas.macro_strategy import SectorTilt

    get_store().save_macro_review(MacroReview(
        name="macro", as_of=datetime(2026, 7, 30, tzinfo=timezone.utc),
        regime="PRIOR", sector_tilts=[SectorTilt(sector="半导体", stance="低配")]))
    monkeypatch.setattr(assemble, "build",
                        lambda cfg, live_data=True: assemble.MacroContext(cfg=cfg))

    def boom(*a, **k):
        raise RuntimeError("validation error")

    monkeypatch.setattr(macro_review, "run_structured", boom)
    wrote: list = []
    monkeypatch.setattr(macro_report, "write", lambda r, c: wrote.append(r) or tmp_path)

    out = cli.run_macro_review("macro", use_llm=True, live_data=False)
    assert out.regime == "PRIOR"
    assert wrote == []          # the stale review must not overwrite 07-30's report


def test_deterministic_fields_survive_the_llm_path(monkeypatch):
    """The LLM view must not be able to clobber code-owned fields."""
    from ats.agents.macro import review as macro_review
    from ats.agents.macro.outputs import MacroReviewLLMView, SectorTiltView
    from ats.config import load_macro_config

    _patch_series(monkeypatch, _stagflation_series())
    view = MacroReviewLLMView(regime="risk-off", sector_tilts=[
        SectorTiltView(sector="半导体", stance="低配", rationale="估值承压")],
        falsifier="初请 4 周均值连续两周低于 21 万")
    monkeypatch.setattr(macro_review, "run_structured", lambda *a, **k: view)

    r = macro_review.run("macro", use_llm=True, live_data=True)
    assert r.regime == "risk-off"                    # narrative from the model
    assert r.quadrant == "stagflation"               # facts from the code
    assert r.falsifier.startswith("初请")
    assert load_macro_config("macro").regime         # thresholds came from yaml
