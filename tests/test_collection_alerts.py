"""Collection alerts: one push per light change, a daily digest, never a failed tick (2026-11-24)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from ats.runtime import collection_alerts as ca

# 2026-11-24 07:30 Asia/Taipei (before the digest hour) and 08:30 (after)
EARLY = datetime(2026, 11, 23, 23, 30, tzinfo=timezone.utc)
LATE = datetime(2026, 11, 24, 0, 30, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Row:
    job_id: str
    light: str = "green"
    light_reason: str = ""
    enabled: bool = True
    last_status: str = "succeeded"
    last_reason: str = ""
    consecutive_failures: int = 0
    last_at: datetime | None = None


def _ledger(tmp_path):
    path = tmp_path / "refresh.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE refresh_runs (job_id TEXT, status TEXT, completed_at TEXT)")
        conn.executemany("INSERT INTO refresh_runs VALUES (?,?,?)", [
            ("a", "succeeded", (LATE - timedelta(hours=2)).isoformat()),
            ("b", "no_change", (LATE - timedelta(hours=3)).isoformat()),
            ("c", "failed", (LATE - timedelta(hours=1)).isoformat()),
            ("old", "failed", (LATE - timedelta(days=3)).isoformat())])
    return path


def _run(tmp_path, rows, now=EARLY, fail=False):
    sent = []

    def push(title, body):
        if fail:
            raise RuntimeError("feishu down")
        sent.append((title, body))

    result = ca.after_tick(ledger=_ledger(tmp_path) if not (tmp_path / "refresh.sqlite").exists()
                           else tmp_path / "refresh.sqlite", rows=rows, now=now, push=push)
    return result, sent


def test_first_run_seeds_silently_then_only_changes_are_pushed(tmp_path):
    ok = [Row("calendar_bea_daily"), Row("ibkr_news_daily")]
    result, sent = _run(tmp_path, ok)
    assert result["seeded"] and sent == []

    failing = [Row("calendar_bea_daily", "yellow", "最近一次运行失败", last_status="failed",
                   last_reason="command_or_result_failed", consecutive_failures=1), Row("ibkr_news_daily")]
    result, sent = _run(tmp_path, failing)
    assert result["changes"] == 1 and len(sent) == 1
    title, body = sent[0]
    assert title == "数据采集：1 个需注意"
    assert "calendar_bea_daily：最近一次运行失败（最近一次 failed/command_or_result_failed）" in body

    # the same state an hour later: nothing
    assert _run(tmp_path, failing)[1] == []

    worse = [replace(failing[0], light="red", light_reason="连续失败 2 次", consecutive_failures=2), ok[1]]
    title, body = _run(tmp_path, worse)[1][0]
    assert title == "数据采集：1 个异常" and "连续失败 2 次" in body

    title, body = _run(tmp_path, ok)[1][0]
    assert title == "数据采集：1 个已恢复" and "calendar_bea_daily" in body


def test_changes_of_one_tick_go_out_as_one_grouped_message(tmp_path):
    _run(tmp_path, [Row(f"j{i}") for i in range(20)])
    rows = ([Row(f"j{i}", "red", "连续失败 2 次", consecutive_failures=2) for i in range(15)]
            + [Row(f"j{i}", "yellow", "最近一次运行失败", consecutive_failures=1) for i in range(15, 20)])
    sent = _run(tmp_path, rows)[1]
    assert len(sent) == 1
    title, body = sent[0]
    assert title == "数据采集：15 个异常，5 个需注意"
    assert body.index("【异常 15】") < body.index("【注意 5】") and "…另 3 个" in body


def test_undelivered_change_is_retried_next_tick(tmp_path):
    _run(tmp_path, [Row("a")])
    bad = [Row("a", "yellow", "最近一次运行失败", consecutive_failures=1)]
    assert _run(tmp_path, bad, fail=True)[0]["changes"] == 0
    result, sent = _run(tmp_path, bad)
    assert result["changes"] == 1 and len(sent) == 1


def test_disabled_jobs_and_red_to_yellow_do_not_push(tmp_path):
    _run(tmp_path, [Row("a", "red", "连续失败 3 次"), Row("b")])
    rows = [Row("a", "yellow", "最近一次运行失败"), Row("b", "grey", "已停用", enabled=False)]
    assert _run(tmp_path, rows)[1] == []


def test_daily_digest_once_after_eight_taipei(tmp_path):
    rows = [Row("a"), Row("calendar_fomc_daily", "red", "连续失败 2 次", last_status="failed", consecutive_failures=2)]
    _run(tmp_path, rows, now=EARLY)                                   # 07:30 Taipei: seed, no digest
    result, sent = _run(tmp_path, rows, now=LATE)                     # 08:30 Taipei: digest
    assert result["digest"] and len(sent) == 1
    title, body = sent[0]
    assert title == "数据采集日报 11-24：1 个异常、0 个需注意"
    assert "成功 1 · 无新数据 1 · 失败 1 · 隔离 0" in body                # only the last 24 hours
    assert "[异常] calendar_fomc_daily：连续失败 2 次" in body
    assert _run(tmp_path, rows, now=LATE + timedelta(hours=1))[1] == []  # once a day


def test_alerting_never_raises(tmp_path):
    result = ca.after_tick(ledger=tmp_path / "missing-dir" / "x.sqlite", rows=[Row("a")], now=LATE,
                           push=lambda *_: None)
    assert "error" in result



def test_line_carries_the_time_of_the_last_run():
    row = Row("calendar_finalize_morning", "red", "已超 SLO：距最近正常运行 3.1 天 > 3 天",
              last_at=datetime(2026, 11, 20, 22, 10, tzinfo=timezone.utc),
              last_reason="partial_budget_deferred: 1 relevant articles left for the next run (request cap 20)")
    line = ca._line(row)
    assert "最近一次 11-21 06:10 succeeded/partial_budget_deferred: 1 relevant arti…" in line
