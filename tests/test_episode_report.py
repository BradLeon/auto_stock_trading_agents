"""Stage C — the per-episode review card + weekly summary. Fully deterministic
(no LLM), so these tests just check the renderer produces the right facts in the
right sections, and that writing/pushing is idempotent per episode.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ats.journal import episode_report as er
from ats.journal import prices
from ats.memory import get_store
from ats.schemas.journal import ApprovalDivergence, EpisodeCard, JournalEntry, TradeEpisode

NOW = datetime(2026, 7, 23, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    return get_store()


@pytest.fixture(autouse=True)
def _no_network_prices(monkeypatch):
    monkeypatch.setattr(prices, "bars", lambda symbol: [])


def _entry(**kw):
    base = dict(entry_id="c1:GOOG:open", cycle_id="c1", as_of=NOW, symbol="GOOG",
               action="buy", setup="pead_event")
    return JournalEntry(**{**base, **kw})


def _episode(**kw):
    base = dict(episode_id="e1", symbol="GOOG", direction="long", status="closed",
               origin="system", basis_source="observed_fills",
               opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
               closed_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
               avg_entry=100.0, avg_exit=122.0, realized_pnl=2200.0,
               primary_entry_id="c1:GOOG:open")
    return TradeEpisode(**{**base, **kw})


# --------------------------------------------------------------------------- #
# render_card: no plan (manual / pre_tracking)
# --------------------------------------------------------------------------- #
def test_card_without_a_plan_does_not_crash_and_says_so():
    ep = _episode(primary_entry_id="", origin="manual")
    card = EpisodeCard(episode=ep, plan=None, legs=[])
    out = er.render_card(card)
    assert "无预登记计划" in out
    assert "（暂无数据）" in out   # evidence/approval sections fall back cleanly


# --------------------------------------------------------------------------- #
# render_card: full plan present
# --------------------------------------------------------------------------- #
def test_card_with_full_plan_surfaces_key_facts():
    plan = _entry(rationale="PEAD 打分卡达标", invalidation="若管理层撤回全年指引",
                 stop_price=90.0, target_price=120.0, planned_horizon_days=20,
                 intended_notional=5000, ev_score_total=1.4, ev_score_band="达到做多门槛",
                 ev_has_transcript=True,
                 approval=ApprovalDivergence(status="modified", diverged=True))
    ep = _episode(exit_reason="target_hit", exit_as_planned=True, r_multiple=2.2,
                 risk_unit_source="expected_move", mae_pct=-3.0, mfe_pct=25.0,
                 holding_days=14)
    card = EpisodeCard(episode=ep, plan=plan, legs=[plan])
    out = er.render_card(card)
    assert "PEAD 打分卡达标" in out
    assert "若管理层撤回全年指引" in out
    assert "+2.20R" in out
    assert "止盈（按计划）" in out
    assert "达到做多门槛" in out
    assert "modified" in out
    assert "[[首席决策-2026-07-23-1400]]" in out


def test_exit_reason_labels_and_planned_flag():
    for reason, planned, expect in [
        ("stop_hit", True, "止损"), ("drift", False, "漂移"),
        ("risk_forced", False, "风控强制减仓"), ("boss_override", False, "人工干预"),
    ]:
        ep = _episode(exit_reason=reason, exit_as_planned=planned)
        out = er._exit_reason(ep)
        assert expect in out


def test_open_episode_with_no_exit_reason_says_not_yet():
    ep = _episode(status="open", closed_at=None, exit_reason=None)
    assert "未平仓" in er._exit_reason(ep)


# --------------------------------------------------------------------------- #
# predictions section + fiscal-label backlink
# --------------------------------------------------------------------------- #
def test_predictions_section_and_fundamental_backlink(store):
    store.save_journal_entry(_entry())
    from ats.journal import predictions as jp
    from ats.schemas.pead import Scorecard

    sc = Scorecard(symbol="GOOG", fiscal_label="Q2 FY2026", as_of=NOW, lines=[],
                   total=1.5, threshold=1.2, band="达到做多门槛")
    jp.record_pead_prediction(store=store, symbol="GOOG", fiscal_label="Q2 FY2026",
                              scorecard=sc, scored_at=NOW, entry_id="c1:GOOG:open")
    from ats.journal.card import build_card

    card = build_card(store, _episode(), with_predictions=True)
    out = er.render_card(card)
    assert "打分卡（漂移方向/强度）" in out
    assert "尚无到期周期" in out
    assert "[[基本面分析-GOOG-2026Q2]]" in out


def test_no_predictions_says_so():
    ep = _episode()
    card = EpisodeCard(episode=ep, plan=None, legs=[])
    assert "无关联预测" in er._predictions(card)


# --------------------------------------------------------------------------- #
# write_card: file path + idempotent push
# --------------------------------------------------------------------------- #
def test_write_card_creates_the_expected_filename(store, tmp_path):
    store.save_journal_entry(_entry())
    store.save_episode(_episode())
    ep = store.get_episode("e1")
    path = er.write_card(store, ep)
    assert path is not None
    assert path.name == "交易复盘-GOOG-20260701.md"
    assert path.read_text(encoding="utf-8").startswith("# 交易复盘 — GOOG 20260701")


def test_push_once_is_idempotent(store, monkeypatch):
    """_push_once imports `_push` locally each call, so patching the digest module's
    attribute (not episode_report's) is what actually takes effect — same pattern
    test_digest.py uses."""
    import ats.runtime.digest as digest_mod

    pushed = []
    monkeypatch.setattr(digest_mod, "_push", lambda *a: pushed.append(a))
    store.save_episode(_episode())
    ep = store.get_episode("e1")
    er._push_once(store, ep)
    er._push_once(store, ep)
    assert len(pushed) == 1


# --------------------------------------------------------------------------- #
# weekly summary
# --------------------------------------------------------------------------- #
def test_week_bounds_are_monday_to_sunday():
    # 2026-07-23 is a Thursday
    start, end = er._week_bounds(date(2026, 7, 23))
    assert start == date(2026, 7, 20) and end == date(2026, 7, 26)


def test_weekly_lists_closures_within_window_and_excludes_outside(store):
    store.save_episode(_episode(episode_id="in", closed_at=datetime(2026, 7, 22, tzinfo=timezone.utc)))
    store.save_episode(_episode(episode_id="out", symbol="TSM",
                               closed_at=datetime(2026, 8, 1, tzinfo=timezone.utc)))
    path = er.write_weekly(store=store, as_of=date(2026, 7, 23))
    text = path.read_text(encoding="utf-8")
    assert "GOOG" in text and "TSM" not in text
    assert "2026-W30" in text


def test_weekly_flags_invalidation_triggered_and_overdue(store):
    store.save_episode(_episode(episode_id="ok", status="open", closed_at=None,
                               invalidation_triggered=False))
    store.save_episode(_episode(episode_id="bad", symbol="TSM", status="open",
                               closed_at=None, invalidation_triggered=True))
    store.save_episode(_episode(episode_id="overdue", symbol="ASML", status="open",
                               closed_at=None, horizon_overdue_days=5))
    path = er.write_weekly(store=store, as_of=date(2026, 7, 23))
    text = path.read_text(encoding="utf-8")
    assert "TSM" in text and "论点失效" in text
    assert "ASML" in text and "超期 5 天" in text
    assert "| GOOG |" not in text   # closed episode, not open — not on the flagged list


def test_run_writes_cards_and_weekly_and_is_safe_with_no_data(store):
    assert er.run() == 0
