"""Feishu alerts for data collection: state changes as they happen, plus one daily digest.

2026-11-24 (user): collection failures went unnoticed for days (ibkr_news 11-17..19, the BEA/BLS/FOMC calendars),
because the only collection-triggered push was the consumer-level data-product health check, which a single
failing job rarely flips. This module runs after every refresh-controller tick:

- **State changes, immediately.** Each job's light is the dashboard's (`dashboard.queries.assess`): normal,
  注意 (latest run failed / a scheduled run is overdue), 异常 (2+ consecutive failures, past the SLO, never
  healthy). A push goes out only when a job's light CHANGES — failing for days sends "注意" then "异常",
  never one message an hour. All changes of one tick go out as one message, grouped.
- **Successes, once a day.** About a hundred runs succeed a day; pushing each would bury the failures, so the
  first tick after 08:00 Asia/Taipei sends a digest: the last 24 hours' outcomes and what needs attention now.

The last notified light per job and the last digest day live in the refresh ledger (`collection_alert_state`,
`collection_alert_digest`). The first run seeds the state silently. Alerting never breaks the tick.

It cannot report its own absence: if the refresh controller is not running, nothing is sent (see the
dispatcher liveness work for that).
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger("ats.runtime.collection_alerts")

TAIPEI = ZoneInfo("Asia/Taipei")
DIGEST_HOUR = 8
DASHBOARD_URL = "http://127.0.0.1:8000/dashboard/sources"
LIGHT_CN = {"green": "正常", "yellow": "注意", "red": "异常", "grey": "停用"}
_RANK = {"green": 0, "grey": 0, "yellow": 1, "red": 2}
_LIST_LIMIT = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_alert_state (
  job_id TEXT PRIMARY KEY, light TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', notified_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collection_alert_digest (day TEXT PRIMARY KEY, sent_at TEXT NOT NULL);
"""

Push = Callable[[str, str], None]


def _default_push(title: str, body: str) -> None:
    from ..workflow.consumer_health import feishu_notify

    feishu_notify(title, body)


def _connect(ledger: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(ledger, timeout=30)
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------------------- #
# state changes
# --------------------------------------------------------------------------- #
def transitions(rows, previous: dict[str, str]) -> dict[str, list]:
    """Jobs whose light changed since the last notification, grouped as 异常 / 注意 / 恢复."""
    out: dict[str, list] = {"red": [], "yellow": [], "recovered": []}
    for r in rows:
        if not r.enabled or r.light == "grey":
            continue
        before = previous.get(r.job_id, "green")
        if r.light == before:
            continue
        if r.light == "green":
            out["recovered"].append(r)
        elif r.light == "red":
            out["red"].append(r)
        elif r.light == "yellow" and _RANK[before] < _RANK["yellow"]:
            out["yellow"].append(r)
        # red -> yellow (one good run after a streak, still failing since): no push; it is in the digest
    return out


def _line(r) -> str:
    """`- job：reason（最近一次 11-20 23:20 succeeded/…）` — the time matters: a job that last succeeded days ago
    and has not run since is overdue, and "最近：succeeded" alone reads like it just worked."""
    reason = str(r.last_reason or "")
    reason = reason[:40] + "…" if len(reason) > 40 else reason
    status = (r.last_status or "无记录") + (f"/{reason}" if reason else "")
    at = getattr(r, "last_at", None)
    when = f"{at.astimezone(TAIPEI):%m-%d %H:%M} " if at else ""
    return f"- {r.job_id}：{r.light_reason or LIGHT_CN[r.light]}（最近一次 {when}{status}）"


def change_message(changes: dict[str, list]) -> tuple[str, str] | None:
    red, yellow, recovered = changes["red"], changes["yellow"], changes["recovered"]
    if not (red or yellow or recovered):
        return None
    parts = []
    for label, items in (("异常", red), ("注意", yellow), ("已恢复", recovered)):
        if not items:
            continue
        lines = [_line(r) if label != "已恢复" else f"- {r.job_id}" for r in items[:_LIST_LIMIT]]
        if len(items) > _LIST_LIMIT:
            lines.append(f"- …另 {len(items) - _LIST_LIMIT} 个")
        parts.append(f"【{label} {len(items)}】\n" + "\n".join(lines))
    head = []
    if red:
        head.append(f"{len(red)} 个异常")
    if yellow:
        head.append(f"{len(yellow)} 个需注意")
    if recovered:
        head.append(f"{len(recovered)} 个已恢复")
    title = "数据采集：" + "，".join(head)
    return title, "\n\n".join(parts) + f"\n\n看板：{DASHBOARD_URL}"


# --------------------------------------------------------------------------- #
# daily digest
# --------------------------------------------------------------------------- #
def _outcomes_24h(ledger: Path, now: datetime) -> dict[str, int]:
    since = (now - timedelta(hours=24)).isoformat()
    with closing(sqlite3.connect(f"file:{ledger}?mode=ro", uri=True, timeout=30)) as conn:
        try:
            rows = conn.execute("SELECT status, count(*) FROM refresh_runs WHERE completed_at >= ? GROUP BY status",
                                (since,)).fetchall()
        except sqlite3.OperationalError:
            return {}
    return {status: n for status, n in rows}


def digest_message(rows, outcomes: dict[str, int], now: datetime) -> tuple[str, str]:
    problems = sorted((r for r in rows if r.enabled and r.light in ("red", "yellow")),
                      key=lambda r: (-_RANK[r.light], -r.consecutive_failures, r.job_id))
    red = sum(1 for r in problems if r.light == "red")
    ok = outcomes.get("succeeded", 0) + outcomes.get("no_change", 0)
    bad = outcomes.get("failed", 0) + outcomes.get("quarantined", 0)
    title = (f"数据采集日报 {now.astimezone(TAIPEI):%m-%d}：" +
             (f"{red} 个异常、{len(problems) - red} 个需注意" if problems else "全部正常"))
    lines = [f"过去 24 小时运行：成功 {outcomes.get('succeeded', 0)} · 无新数据 {outcomes.get('no_change', 0)}"
             f" · 失败 {outcomes.get('failed', 0)} · 隔离 {outcomes.get('quarantined', 0)}"
             f"（正常合计 {ok}，异常合计 {bad}）",
             f"采集点共 {len(rows)} 个，正常 {sum(1 for r in rows if r.light == 'green')}。"]
    if problems:
        lines.append("\n需要处理：")
        lines += [_line(r).replace("- ", f"- [{LIGHT_CN[r.light]}] ", 1) for r in problems[:_LIST_LIMIT]]
        if len(problems) > _LIST_LIMIT:
            lines.append(f"- …另 {len(problems) - _LIST_LIMIT} 个")
    lines.append(f"\n看板：{DASHBOARD_URL}")
    return title, "\n".join(lines)


def _digest_due(conn: sqlite3.Connection, now: datetime) -> str | None:
    local = now.astimezone(TAIPEI)
    if local.hour < DIGEST_HOUR:
        return None
    day = local.date().isoformat()
    if conn.execute("SELECT 1 FROM collection_alert_digest WHERE day=?", (day,)).fetchone():
        return None
    return day


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def after_tick(*, ledger: Path | None = None, rows=None, now: datetime | None = None,
               push: Push | None = None) -> dict:
    """Push the light changes since the last tick and, once a day, the digest. Never raises."""
    now = now or datetime.now(timezone.utc)
    push = push or _default_push
    result = {"changes": 0, "digest": False, "seeded": False}
    try:
        if rows is None or ledger is None:
            from .dashboard.queries import DashboardPaths, source_rows

            paths = DashboardPaths.from_env()
            ledger = ledger or paths.refresh
            rows = source_rows(paths, now) if rows is None else rows
        with closing(_connect(Path(ledger))) as conn:
            previous = {job: light for job, light in conn.execute("SELECT job_id, light FROM collection_alert_state")}
            keep: set[str] = set()
            if not previous:
                result["seeded"] = True          # first run: remember the current state, announce nothing
            else:
                changes = transitions(rows, previous)
                message = change_message(changes)
                if message:
                    changed = {r.job_id for group in changes.values() for r in group}
                    if _safe_push(push, *message):
                        result["changes"] = len(changed)
                    else:
                        keep = changed           # undelivered: leave their old state so the next tick retries
            stamp = now.isoformat()
            rows_to_store = [r for r in rows if r.job_id not in keep]
            conn.executemany(
                "INSERT INTO collection_alert_state (job_id, light, reason, notified_at) VALUES (?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET light=excluded.light, reason=excluded.reason, "
                "notified_at=CASE WHEN collection_alert_state.light != excluded.light THEN excluded.notified_at "
                "ELSE collection_alert_state.notified_at END",
                [(r.job_id, r.light, r.light_reason, stamp) for r in rows_to_store])
            day = _digest_due(conn, now)
            if day and _safe_push(push, *digest_message(rows, _outcomes_24h(Path(ledger), now), now)):
                conn.execute("INSERT INTO collection_alert_digest (day, sent_at) VALUES (?,?)", (day, stamp))
                result["digest"] = True
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - alerting must never fail or alter the refresh tick
        log.warning("collection alerts skipped: %s", exc)
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _safe_push(push: Push, title: str, body: str) -> bool:
    try:
        push(title, body[:3500])
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("collection alert not delivered (%s): %s", title, exc)
        return False
