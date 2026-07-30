"""交易台账 — the readable ledger. A row is an INTENT, not a fill.

Of the first 52 order rows, 24 errored and 16 were cancelled: "the order evaporated" is
this system's most common outcome, so a ledger of fills only would hide the single thing
most worth noticing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ats.journal.report import render_ledger
from ats.schemas.journal import JournalEntry

NOW = datetime(2026, 7, 23, 14, 6, tzinfo=timezone.utc)


def _e(**kw):
    base = dict(entry_id="c1:GOOG:trim", cycle_id="c1", as_of=NOW, symbol="GOOG",
                action="trim", setup="pead_event", intended_notional=4000.0,
                intended_qty=13.0, conviction=0.5)
    return JournalEntry(**{**base, **kw})


def test_empty_month_is_stated_not_blank():
    out = render_ledger([], "2026-07")
    assert "本月无交易意图" in out


def test_renders_intent_with_its_plan():
    out = render_ledger([_e(stop_price=300.0, target_price=360.0,
                            planned_horizon_days=10)], "2026-07")
    assert "| GOOG | trim | pead_event |" in out
    assert "止 300 · 标 360 · 10日" in out
    assert "`c1:GOOG:trim`" in out


def test_failed_orders_are_visible():
    """The whole reason a row is an intent rather than a fill."""
    out = render_ledger([_e(terminal_status="error", submit_attempts=5)], "2026-07")
    assert "失败 ×5" in out
    assert "未成交/失败 1" in out


def test_missing_transcript_is_called_out():
    out = render_ledger([_e(ev_score_total=0.72, ev_has_transcript=0,
                            ev_score_latency_h=28.3)], "2026-07")
    assert "**缺纪要**" in out          # the agent-native "traded on a hunch"
    assert "滞后28h" in out


def test_boss_rejection_is_marked():
    from ats.schemas.journal import ApprovalDivergence

    ap = ApprovalDivergence(status="approved", diverged=True, dropped_symbols=["GOOG"])
    out = render_ledger([_e(approval=ap)], "2026-07")
    assert "**被否**" in out
    assert "人审有分歧 1" in out


def test_invalidation_gets_its_own_section():
    """Pre-registered falsification criteria must be checkable later, not buried."""
    out = render_ledger([_e(invalidation="下季 Cloud 增速再降到 25% 以下")], "2026-07")
    assert "## 论点失效条件（预登记）" in out
    assert "下季 Cloud 增速再降到 25% 以下" in out


def test_render_is_deterministic():
    """SQLite is authoritative; the .md is a regenerable view, so same rows = same bytes."""
    rows = [_e(), _e(entry_id="c1:ASML:trim", symbol="ASML")]
    a = render_ledger(rows, "2026-07").split("\n")
    b = render_ledger(rows, "2026-07").split("\n")
    assert [l for l in a if "生成于" not in l] == [l for l in b if "生成于" not in l]
