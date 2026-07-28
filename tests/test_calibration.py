"""Stage D — the calibration report. Every block is pure arithmetic over
predictions/outcomes or closed episodes; the one property worth checking per
block is the plan's own verification bar: hand-recompute one number and match it,
and confirm "n 不足" renders instead of a bare (noise-driven) figure.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from ats.journal import calibration as cal
from ats.memory import get_store
from ats.schemas.journal import ApprovalDivergence, JournalEntry, Prediction, PredictionOutcome, TradeEpisode

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


@pytest.fixture
def store():
    return get_store()


def _pred(**kw):
    base = dict(prediction_id="p1", made_at=NOW, symbol="GOOG", source="pead_score",
               ref_key="GOOG:Q2 2026", kind="drift_direction")
    return Prediction(**{**base, **kw})


def _outcome(**kw):
    base = dict(prediction_id="p1", horizon_days=1)
    return PredictionOutcome(**{**base, **kw})


# --------------------------------------------------------------------------- #
# 1. band calibration — hand-verified numbers
# --------------------------------------------------------------------------- #
def test_band_calibration_matches_hand_computed_average(store):
    store.save_prediction(_pred(prediction_id="p1", symbol="TSM", predicted_value=1.25,
                                predicted_band="达到做多门槛"))
    store.save_prediction_outcome(_outcome(prediction_id="p1", horizon_days=1,
                                           excess_vs_sector_pct=0.57))
    store.save_prediction(_pred(prediction_id="p2", symbol="COHR", predicted_value=0.0,
                                predicted_band="中性观望"))
    store.save_prediction_outcome(_outcome(prediction_id="p2", horizon_days=1,
                                           excess_vs_sector_pct=-2.64))
    block = cal._band_calibration(store)
    assert block.n_closed == 2 and block.n_open == 0
    by_band = {row["band"]: row for row in block.table}
    assert by_band["达到做多门槛"]["T+1均值超额%"] == pytest.approx(0.57)
    assert by_band["中性观望"]["T+1均值超额%"] == pytest.approx(-2.64)
    # monotonic ordering: the higher band sorts first
    assert block.table[0]["band"] == "达到做多门槛"


def test_band_calibration_counterexample_when_sign_disagrees(store):
    store.save_prediction(_pred(prediction_id="p1", predicted_value=1.5, predicted_band="达到做多门槛"))
    store.save_prediction_outcome(_outcome(prediction_id="p1", horizon_days=1, excess_vs_sector_pct=-5.0))
    block = cal._band_calibration(store)
    assert block.counterexamples and "p1" in block.counterexamples[0]


def test_band_calibration_unelapsed_prediction_counts_as_open_not_zero(store):
    store.save_prediction(_pred(prediction_id="p1"))   # no outcome saved -> still pending
    block = cal._band_calibration(store)
    assert block.n_open == 1 and block.n_closed == 0
    assert not block.sufficient


# --------------------------------------------------------------------------- #
# 2. threshold sweep
# --------------------------------------------------------------------------- #
def test_threshold_sweep_counts_who_would_clear_each_bar(store):
    store.save_prediction(_pred(prediction_id="p1", predicted_value=0.9))
    store.save_prediction(_pred(prediction_id="p2", predicted_value=1.3))
    store.save_prediction_outcome(_outcome(prediction_id="p1", horizon_days=1, excess_vs_sector_pct=1.0))
    store.save_prediction_outcome(_outcome(prediction_id="p2", horizon_days=1, excess_vs_sector_pct=2.0))
    block = cal._threshold_sweep(store)
    by_thr = {row["long_threshold"]: row for row in block.table}
    assert by_thr[0.8]["会放行次数"] == 2      # both >= 0.8
    assert by_thr[1.2]["会放行次数"] == 1      # only p2 >= 1.2
    assert by_thr[1.2]["T+1均值超额%"] == pytest.approx(2.0)
    assert by_thr[1.5]["会放行次数"] == 0


# --------------------------------------------------------------------------- #
# 3. expected_move calibration
# --------------------------------------------------------------------------- #
def test_expected_move_ratio(store):
    store.save_prediction(_pred(prediction_id="p1", source="expected_move", predicted_value=4.0))
    store.save_prediction_outcome(_outcome(prediction_id="p1", horizon_days=1, realized_pct=-6.0))
    block = cal._expected_move_calibration(store)
    row = block.table[0]
    assert row["预测隐含波幅均值%"] == pytest.approx(4.0)
    assert row["实现|T+1|均值%"] == pytest.approx(6.0)   # abs()
    assert row["实现/预测比值"] == pytest.approx(1.5)
    assert block.n_closed == 1


# --------------------------------------------------------------------------- #
# 4. consensus PT calibration
# --------------------------------------------------------------------------- #
def test_consensus_pt_capture_ratio(store):
    from ats.config import get_config

    horizon = max(get_config().app.journal.horizons)
    store.save_prediction(_pred(prediction_id="p1", source="consensus_pt", predicted_value=20.0))
    store.save_prediction_outcome(_outcome(prediction_id="p1", horizon_days=horizon, realized_pct=10.0))
    block = cal._consensus_pt_calibration(store)
    row = block.table[0]
    assert row["共识PT隐含涨幅均值%"] == pytest.approx(20.0)
    assert row[f"T+{horizon}实现涨幅均值%"] == pytest.approx(10.0)
    assert row["捕获率(实现/隐含)"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# 5. setup expectancy
# --------------------------------------------------------------------------- #
def _episode(**kw):
    base = dict(episode_id="e1", symbol="GOOG", direction="long", status="closed",
               origin="system", basis_source="observed_fills",
               opened_at=NOW, closed_at=NOW, primary_entry_id="c1:GOOG:open",
               setup="pead_event")
    return TradeEpisode(**{**base, **kw})


def test_setup_expectancy_groups_and_computes_mfe_mae_ratio(store):
    store.save_episode(_episode(episode_id="e1", realized_pnl=1000.0, mfe_pct=20.0, mae_pct=-5.0))
    store.save_episode(_episode(episode_id="e2", symbol="TSM", primary_entry_id="c1:TSM:open",
                               realized_pnl=-200.0, mfe_pct=8.0, mae_pct=-10.0))
    block = cal._setup_expectancy(store)
    row = block.table[0]
    assert row["setup"] == "pead_event" and row["n"] == 2
    assert row["win_rate"] == pytest.approx(0.5)
    assert row["均值盈亏$"] == pytest.approx(400.0)
    assert row["MFE:|MAE|"] == pytest.approx(round((20.0 + 8.0) / (5.0 + 10.0), 2))


def test_setup_expectancy_excludes_non_gradeable_episodes(store):
    store.save_episode(_episode(primary_entry_id="", origin="manual"))
    block = cal._setup_expectancy(store)
    assert block.n_closed == 0 and block.table == []


# --------------------------------------------------------------------------- #
# 6. risk gate audit
# --------------------------------------------------------------------------- #
def _entry(**kw):
    base = dict(entry_id="c1:GOOG:open", cycle_id="c1", as_of=NOW, symbol="GOOG", action="buy")
    return JournalEntry(**{**base, **kw})


def test_risk_gate_audit_splits_by_risk_notes_presence(store):
    store.save_journal_entry(_entry(entry_id="c1:GOOG:open", risk_notes=["GOOG: clipped to L1 cap"]))
    store.save_episode(_episode(episode_id="e1", primary_entry_id="c1:GOOG:open", realized_pnl=300.0))
    store.save_journal_entry(_entry(entry_id="c2:TSM:open", symbol="TSM"))
    store.save_episode(_episode(episode_id="e2", symbol="TSM", primary_entry_id="c2:TSM:open",
                               realized_pnl=-100.0))
    block = cal._risk_gate_audit(store)
    by_group = {row["分组"]: row for row in block.table}
    assert by_group["风控介入过（削减/预警）"]["n"] == 1
    assert by_group["风控介入过（削减/预警）"]["均值盈亏$"] == pytest.approx(300.0)
    assert by_group["风控未介入"]["均值盈亏$"] == pytest.approx(-100.0)


# --------------------------------------------------------------------------- #
# 7. human gate audit
# --------------------------------------------------------------------------- #
def _flat_bars(start: date, n: int, base: float, final: float | None = None):
    from ats.schemas.market import OHLCV

    bars = []
    for i in range(n):
        c = final if (final is not None and i == n - 1) else base
        bars.append(OHLCV(date=start + timedelta(days=i), open=c, high=c, low=c, close=c, volume=1.0))
    return bars


def test_human_gate_audit_computes_counterfactual_forward_return(store, monkeypatch):
    from ats.config import get_config
    from ats.journal import prices

    h = max(get_config().app.journal.horizons)
    start = date(2026, 7, 1)
    monkeypatch.setattr(prices, "bars", lambda s: _flat_bars(start, h + 1, 100.0, final=110.0))
    div = ApprovalDivergence(status="rejected", diverged=True, dropped_symbols=["GOOG"])
    store.save_journal_entry(_entry(as_of=datetime(2026, 7, 1, tzinfo=timezone.utc),
                                    action="buy", approval=div))
    block = cal._human_gate_audit(store)
    assert block.n_closed == 1
    row = block.table[0]
    assert row["n"] == 1
    assert row[f"若按提议方向持有T+{h}均值涨跌%"] == pytest.approx(10.0)
    assert row["若按提议方向持有会赢的比例"] == pytest.approx(1.0)
    assert block.counterexamples and "GOOG" in block.counterexamples[0]


def test_human_gate_audit_ignores_entries_the_boss_approved(store, monkeypatch):
    from ats.journal import prices

    monkeypatch.setattr(prices, "bars", lambda s: [])
    div = ApprovalDivergence(status="approved", diverged=False)
    store.save_journal_entry(_entry(approval=div))
    block = cal._human_gate_audit(store)
    assert block.n_closed == 0


# --------------------------------------------------------------------------- #
# render_calibration: the "样本不足" gate
# --------------------------------------------------------------------------- #
def test_render_flags_insufficient_samples():
    from ats.schemas.journal import EvidenceBlock

    block = EvidenceBlock(question="q", table=[{"a": 1}], n_closed=2, n_open=1, n_min=10)
    out = cal.render_calibration([block], "2026-07")
    assert "样本不足" in out and "n_closed=2" in out


def test_render_omits_insufficient_flag_when_sample_is_enough():
    from ats.schemas.journal import EvidenceBlock

    block = EvidenceBlock(question="q", table=[{"a": 1}], n_closed=12, n_open=0, n_min=10)
    out = cal.render_calibration([block], "2026-07")
    assert "样本不足" not in out


def test_period_label_monthly_vs_quarterly():
    assert cal._period_label(date(2026, 7, 23), quarterly=False) == "2026-07"
    assert cal._period_label(date(2026, 7, 23), quarterly=True) == "2026Q3"


def test_run_is_safe_with_no_data(store):
    assert cal.run() == 0
