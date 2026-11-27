"""Read-only queries behind the dashboard. Every function returns plain data; nothing here renders or writes.

Each request opens its own read-only connections and closes them: the dashboard has one user and small
queries, and a cache would only make "refresh shows the latest" untrue.

Times stored by different writers carry different offsets (`+00:00`, `+08:00`) or none, so they are parsed
into aware datetimes in Python before any comparison — never compared as strings.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ...agents.provenance import parse_time

HEALTHY = ("succeeded", "no_change")
FAILING = ("failed", "quarantined")


# --------------------------------------------------------------------------- #
# stores
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DashboardPaths:
    ats: Path
    data: Path
    refresh: Path
    dispatch: Path
    schedules: Path
    workflow_config: Path

    @classmethod
    def from_env(cls) -> "DashboardPaths":
        """The production stores, or another `var/` via ATS_DASHBOARD_VAR (development against real data)."""
        from ...config import REPO_ROOT

        var = os.environ.get("ATS_DASHBOARD_VAR")
        if var:
            v = Path(var)
            return cls(ats=v / "ats.sqlite", data=v / "data.sqlite", refresh=v / "data_refresh.sqlite",
                       dispatch=v / "phase_f_dispatch.sqlite",
                       schedules=REPO_ROOT / "config" / "data" / "schedules.yaml",
                       workflow_config=REPO_ROOT / "config" / "workflow")
        from ...data.runtime.repository import platform_data_db_path
        from ...memory import _db_path
        from ...workflow.schedule_runtime import state_path

        return cls(ats=Path(_db_path()), data=Path(platform_data_db_path()),
                   refresh=REPO_ROOT / "var" / "data_refresh.sqlite", dispatch=Path(state_path()),
                   schedules=REPO_ROOT / "config" / "data" / "schedules.yaml",
                   workflow_config=REPO_ROOT / "config" / "workflow")


@contextmanager
def read_only(path: Path):
    """A read-only connection, or None when the store does not exist (a fresh machine, a test)."""
    if not Path(path).exists():
        yield None
        return
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _rows(conn, sql: str, args=()) -> list[dict]:
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.OperationalError:          # a table this store does not have (yet)
        return []


# --------------------------------------------------------------------------- #
# catalog (Chinese annotations)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Note:
    label: str = ""
    what: str = ""
    source: str = ""
    used_by: str = ""
    if_broken: str = ""

    @property
    def missing(self) -> bool:
        return not self.what


def load_catalog(path: Path | None = None) -> dict[str, dict[str, Note]]:
    import yaml

    from ...config import REPO_ROOT

    path = path or REPO_ROOT / "config" / "ops" / "catalog_zh.yaml"
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    raw = raw or {}
    out: dict[str, dict[str, Note]] = {}
    for section in ("jobs", "workflows"):
        out[section] = {k: Note(**{f: str(v.get(f) or "") for f in Note.__dataclass_fields__})
                        for k, v in (raw.get(section) or {}).items()}
    return out


# --------------------------------------------------------------------------- #
# data sources
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DataTime:
    kind: str = ""            # 发布 / 采集 / 入库 / 数据期 / 产出 / ""
    at: datetime | None = None
    period: str = ""


@dataclass
class SourceRow:
    job_id: str
    group: str
    entity: str
    domain: str
    schedule: str
    slo_days: int
    enabled: bool
    sources: tuple[str, ...]
    last_status: str = ""
    last_reason: str = ""
    last_window: str = ""
    last_at: datetime | None = None
    last_healthy_at: datetime | None = None
    last_new_at: datetime | None = None
    consecutive_failures: int = 0
    consecutive_partial: int = 0     # successful runs in a row that lost at least one route of the source
    failed_routes: str = ""          # the routes the latest of those runs lost
    data_time: DataTime = field(default_factory=DataTime)
    light: str = "grey"        # green / yellow / red / grey
    light_reason: str = ""
    history: list[tuple[str, str]] = field(default_factory=list)   # (window_date, status), oldest first


def assess(enabled: bool, slo_days: int, last_healthy_at: datetime | None, now: datetime,
           consecutive_failures: int = 0, interval_days: float = 0,
           consecutive_partial: int = 0, failed_routes: str = "") -> tuple[str, str]:
    """(light, reason). Freshness against the SLO (Q3) AND what the latest attempts did.

    2026-11-21 (user): three calendar jobs showed 正常 next to a red 失败 because the light only looked at
    the age of the last healthy run (2.4 days on a 5-day SLO). A failure is now never shown as normal:
    - red: past the SLO, never healthy, or 2+ consecutive failures;
    - yellow: the latest attempt failed, or a scheduled run is overdue — older than the job's own interval
      plus one day, and never earlier than half of the SLO (a weekly job on day 6 of a 10-day SLO is on
      schedule, not a warning: 17 weekly jobs sat yellow every week under the bare half-SLO line);
    - green otherwise; a disabled job is grey.

    2026-11-27 (user): a run that succeeded but lost some routes of its source is not normal either (two
    routes failed for five weeks behind a green light): yellow once, red from the second run in a row,
    naming the routes.
    """
    if not enabled:
        return "grey", "已停用"
    if last_healthy_at is None:
        return "red", "从未正常运行"
    age = (now - last_healthy_at).total_seconds() / 86_400
    if age > slo_days:
        return "red", f"已超 SLO：距最近正常运行 {age:.1f} 天 > {slo_days} 天"
    if consecutive_failures >= 2:
        return "red", f"连续失败 {consecutive_failures} 次"
    if consecutive_partial >= 2:
        return "red", f"部分路由连续 {consecutive_partial} 次失败：{failed_routes}"
    if consecutive_failures == 1:
        return "yellow", "最近一次运行失败"
    if consecutive_partial == 1:
        return "yellow", f"部分路由失败：{failed_routes}"
    due = max(slo_days / 2, interval_days + 1) if interval_days else slo_days / 2
    if age > due:
        return "yellow", f"已错过计划运行：距最近正常运行 {age:.1f} 天（SLO {slo_days} 天）"
    return "green", ""


def light_for(enabled: bool, slo_days: int, last_healthy_at: datetime | None, now: datetime,
              consecutive_failures: int = 0) -> str:
    return assess(enabled, slo_days, last_healthy_at, now, consecutive_failures)[0]


_LIGHT_ORDER = {"red": 0, "yellow": 1, "green": 2, "grey": 3}


def worst(lights) -> str:
    lights = list(lights)
    return min(lights, key=_LIGHT_ORDER.get) if lights else "grey"


def _run_time(run: dict) -> datetime | None:
    return parse_time(run.get("completed_at")) or parse_time(run.get("started_at")) \
        or parse_time(run.get("claimed_at"))


# Derived jobs whose sources have a data time of their own; the rest (chain / article evidence extraction)
# produce observations, and their "data" is the last run that produced something.
_EXTRACTION_PREFIXES = ("chain_", "article_evidence_")


def _data_times(data_conn, jobs) -> dict[str, DataTime]:
    """Per source id, the newest data time this store can show for it (see design §3)."""
    sources = {s for j in jobs for s in j.sources}
    out: dict[str, DataTime] = {}
    for r in _rows(data_conn, "SELECT source_id, dataset_id, latest_available_period, checked_at "
                              "FROM structured_source_checks"):
        cur = out.get(r["source_id"])
        at = parse_time(r["checked_at"])
        if r["latest_available_period"] and (cur is None or (at and cur.at and at > cur.at)):
            out[r["source_id"]] = DataTime("数据期", at, str(r["latest_available_period"]))
    accepted: dict[str, datetime] = {}
    for r in _rows(data_conn, "SELECT source_id, max(completed_at) AS at FROM structured_ingestion_runs "
                              "WHERE accepted > 0 GROUP BY source_id"):
        at = parse_time(r["at"])
        if at:
            accepted[r["source_id"]] = at
    for source, at in accepted.items():
        cur = out.get(source)
        out[source] = DataTime("入库", at, cur.period if cur else "")
    for r in _rows(data_conn, "SELECT source, published_at, fetched_at FROM data_documents "
                              "WHERE source IN (%s)" % ",".join("?" * len(sources)) if sources else
                   "SELECT 1 WHERE 0", tuple(sorted(sources))):
        pub, fetched = parse_time(r["published_at"]), parse_time(r["fetched_at"])
        at, kind = (pub, "发布") if pub else (fetched, "采集")
        cur = out.get(r["source"])
        if at and (cur is None or cur.kind not in ("发布", "采集") or (cur.at and at > cur.at)):
            out[r["source"]] = DataTime(kind, at)
    for r in _rows(data_conn, "SELECT source_id, max(finished_at) AS at FROM schedule_calendar_source_runs "
                              "WHERE status IN ('complete','succeeded') GROUP BY source_id"):
        at = parse_time(r["at"])
        if at:
            out[r["source_id"]] = DataTime("采集", at)
    return out


def source_rows(paths: DashboardPaths, now: datetime | None = None, days: int = 30) -> list[SourceRow]:
    from ...data.refresh import PARTIAL_ROUTE_REASON, load_plan

    now = now or datetime.now(timezone.utc)
    plan = load_plan(paths.schedules)
    since = (now - timedelta(days=days)).date().isoformat()
    with read_only(paths.refresh) as rc:
        runs = _rows(rc, "SELECT job_id, window_date, status, reason_code, claimed_at, started_at, completed_at "
                         "FROM refresh_runs WHERE window_date >= ? ORDER BY window_date, rowid", (since,))
        healthy = {r["job_id"]: r for r in _rows(
            rc, "SELECT job_id, max(completed_at) AS completed_at FROM refresh_runs "
                "WHERE status IN ('succeeded','no_change') GROUP BY job_id")}
        new = {r["job_id"]: r for r in _rows(
            rc, "SELECT job_id, max(completed_at) AS completed_at FROM refresh_runs "
                "WHERE status = 'succeeded' GROUP BY job_id")}
    by_job: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_job[r["job_id"]].append(r)
    with read_only(paths.data) as dc:
        times = _data_times(dc, plan.jobs)

    out = []
    for job in plan.jobs:
        group = job.id[: -len(job.entity) - 1] if job.entity else job.id
        row = SourceRow(job_id=job.id, group=group, entity=job.entity, domain=job.domain,
                        schedule=job.schedule, slo_days=job.freshness_slo_days, enabled=job.enabled,
                        sources=job.sources)
        history = by_job.get(job.id, [])
        # one cell per window: the last record of that window wins (a retry replaces the failure)
        per_window: dict[str, str] = {}
        for r in history:
            per_window[r["window_date"]] = r["status"]
        row.history = sorted(per_window.items())
        if history:
            last = history[-1]
            row.last_status, row.last_reason = last["status"], last["reason_code"] or ""
            row.last_window, row.last_at = last["window_date"], _run_time(last)
            for r in reversed(history):
                if r["status"] in FAILING:
                    row.consecutive_failures += 1
                elif r["status"] in HEALTHY:
                    break
            for r in reversed(history):
                if r["status"] not in HEALTHY:
                    continue                 # a failed attempt in between says nothing about the routes
                code = r["reason_code"] or ""
                if not code.startswith(PARTIAL_ROUTE_REASON):
                    break
                row.consecutive_partial += 1
                row.failed_routes = row.failed_routes or code[len(PARTIAL_ROUTE_REASON):].replace(",", "、")
        row.last_healthy_at = parse_time((healthy.get(job.id) or {}).get("completed_at"))
        row.last_new_at = parse_time((new.get(job.id) or {}).get("completed_at"))
        row.data_time = _job_data_time(job, times, row)
        row.light, row.light_reason = assess(job.enabled, job.freshness_slo_days, row.last_healthy_at, now,
                                             row.consecutive_failures, _interval_days(job),
                                             row.consecutive_partial, row.failed_routes)
        out.append(row)
    return out


def _job_data_time(job, times: dict[str, DataTime], row: SourceRow) -> DataTime:
    """The newest data time of the job's sources; extraction jobs show when they last produced something."""
    if job.domain == "derived" and job.id.startswith(_EXTRACTION_PREFIXES):
        return DataTime("产出", row.last_new_at)
    found = [times[s] for s in job.sources if s in times and times[s].at]
    if found:
        return max(found, key=lambda t: t.at)
    if job.domain == "derived":
        return DataTime("产出", row.last_new_at)
    return DataTime()


def _interval_days(job) -> float:
    """How often the job is scheduled to run, in days (0 = unknown: fall back to the half-SLO line)."""
    schedule = str(getattr(job, "schedule", "") or "")
    if schedule == "daily":
        return 1
    if schedule == "weekly":
        return 7
    if schedule == "monthly":
        return 31
    if schedule.startswith("every_"):
        return float(getattr(job, "interval_days", 0) or 0)
    return 0


@dataclass
class SourceGroup:
    group: str
    domain: str
    rows: list[SourceRow]

    @property
    def light(self) -> str:
        return worst(r.light for r in self.rows)

    @property
    def expanded(self) -> bool:
        return len(self.rows) > 1


def source_groups(rows: list[SourceRow]) -> list[SourceGroup]:
    groups: dict[str, SourceGroup] = {}
    for r in rows:
        groups.setdefault(r.group, SourceGroup(r.group, r.domain, [])).rows.append(r)
    order = {"structured": 0, "unstructured": 1, "derived": 2}
    return sorted(groups.values(), key=lambda g: (order.get(g.domain, 9), g.group))


def source_attempts(paths: DashboardPaths, job_id: str, limit: int = 60) -> list[dict]:
    """One job's runs and their attempts, newest first, for the detail page."""
    with read_only(paths.refresh) as rc:
        runs = _rows(rc, "SELECT trigger_key, window_date, status, reason_code, attempts, started_at, completed_at, "
                         "exit_code FROM refresh_runs WHERE job_id = ? ORDER BY window_date DESC, rowid DESC "
                         "LIMIT ?", (job_id, limit))
        for r in runs:
            r["attempt_rows"] = _rows(rc, "SELECT attempt_no, status, reason_code, started_at, completed_at, "
                                          "exit_code FROM refresh_attempts WHERE trigger_key = ? "
                                          "ORDER BY attempt_no", (r["trigger_key"],))
    return runs


# --------------------------------------------------------------------------- #
# workflows
# --------------------------------------------------------------------------- #
@dataclass
class Cell:
    day: date
    state: str = "none"        # complete / incomplete / failed / running / missed / paused / none / future
    count: int = 0
    run_ids: list[str] = field(default_factory=list)
    note: str = ""             # e.g. the Chief cycle status
    cycle_id: str = ""         # chief-daily: the decision cycle of that day


@dataclass
class WorkflowRow:
    workflow_id: str
    kind: str                  # research / trade / event
    schedule: str              # human: "周一至五 08:30 纽约"
    cells: list[Cell]
    llm_cost: dict[date, float] = field(default_factory=dict)


_STATE_RANK = {"failed": 0, "missed": 1, "incomplete": 2, "running": 3, "planned": 3,
               "complete": 4, "cancelled": 4}
_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DOW_CN = "一二三四五六日"
_TZ_CN = {"America/New_York": "纽约", "Asia/Taipei": "台北", "UTC": "UTC"}


def _weekdays(spec: str) -> set[int]:
    spec = str(spec).lower()
    if "-" in spec:
        a, b = spec.split("-", 1)
        return set(range(_DOW[a], _DOW[b] + 1))
    return {_DOW[d.strip()] for d in spec.split(",") if d.strip() in _DOW}


def _schedule_text(cron: dict) -> str:
    days = sorted(_weekdays(cron.get("day_of_week", "mon-sun")))
    d = (f"周{_DOW_CN[days[0]]}至{_DOW_CN[days[-1]]}" if len(days) > 1 and days == list(range(days[0], days[-1] + 1))
         else "、".join(f"周{_DOW_CN[i]}" for i in days))
    return f"{d} {int(cron.get('hour', 0)):02d}:{int(cron.get('minute', 0)):02d} " \
           f"{_TZ_CN.get(cron.get('timezone', 'UTC'), cron.get('timezone', ''))}"


def _workflow_specs(paths: DashboardPaths) -> list[tuple[str, str, dict]]:
    import yaml

    out = []
    for name, kind in (("research_schedules.yaml", "research"), ("trade_schedules.yaml", "trade")):
        raw = yaml.safe_load((paths.workflow_config / name).read_text(encoding="utf-8")) or {}
        for wid, body in (raw.get("workflows") or {}).items():
            if body.get("enabled"):
                out.append((wid, kind, body.get("cron") or {}))
    return out


def _is_trading_day(day: date, cache: dict) -> bool:
    if day not in cache:
        try:
            from ...workflow.trade_schedules import is_trading_day

            cache[day] = is_trading_day(day)
        except Exception:  # noqa: BLE001 - no calendar: weekdays only
            cache[day] = day.weekday() < 5
    return cache[day]


def _pause_spans(paths: DashboardPaths) -> dict[str, list]:
    """workflow -> [(start, end|None)]: the same rule as schedule_runtime._pause_windows (a `rollback` — what
    `ats dispatch pause` logs — opens a window, the next `switch` closes it), read from the given store."""
    windows: dict[str, list] = {}
    with read_only(paths.dispatch) as dc:
        log = _rows(dc, "SELECT workflow, action, at FROM schedule_owner_log ORDER BY seq")
    for row in log:
        at = parse_time(row["at"])
        if at is None:
            continue
        spans = windows.setdefault(row["workflow"], [])
        if row["action"] == "rollback" and not (spans and spans[-1][1] is None):
            spans.append((at, None))
        elif row["action"] == "switch" and spans and spans[-1][1] is None:
            spans[-1] = (spans[-1][0], at)
    return windows


def workflow_grid(paths: DashboardPaths, now: datetime | None = None, days: int = 14):
    """(days, rows): Airflow-style grid with "missed" cells for scheduled days that have no run."""
    now = now or datetime.now(timezone.utc)
    specs = _workflow_specs(paths)
    since = now - timedelta(days=days + 1)
    with read_only(paths.ats) as ac:
        runs = _rows(ac, "SELECT run_id, request_json, status, started_at, created_at FROM workflow_runs "
                         "WHERE coalesce(started_at, created_at) >= ?", (since.isoformat(),))
        cycles = _rows(ac, "SELECT cycle_id, status, created_at FROM decision_cycles WHERE created_at >= ?",
                       (since.isoformat(),))
        costs = _rows(ac, "SELECT workflow_run_id, sum(cost_usd) AS cost FROM llm_calls "
                          "WHERE workflow_run_id != '' GROUP BY workflow_run_id")
    with read_only(paths.dispatch) as dc:
        claims = _rows(dc, "SELECT workflow, status, identity, updated_at FROM schedule_runtime_claims "
                           "WHERE workflow IN ('chief-daily','clerk-daily')")
    cost_by_run = {c["workflow_run_id"]: c["cost"] for c in costs if c["cost"] is not None}

    tz_of = {wid: ZoneInfo(cron.get("timezone", "UTC")) for wid, _k, cron in specs}
    research: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        try:
            wid = (json.loads(r["request_json"] or "{}").get("requested_tasks") or [""])[0]
        except (json.JSONDecodeError, AttributeError):
            wid = ""
        if wid:
            research[wid].append(r)
    for wid in research:
        tz_of.setdefault(wid, ZoneInfo("America/New_York"))

    today = {wid: now.astimezone(tz).date() for wid, tz in tz_of.items()}
    ny_today = now.astimezone(ZoneInfo("America/New_York")).date()
    day_list = [ny_today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    pauses, trading = _pause_spans(paths), {}

    def cells_for(wid: str, cron: dict | None, events: list[tuple]):
        """events: (local day, state, run id, note[, cycle id])."""
        by_day: dict[date, Cell] = {d: Cell(d) for d in day_list}
        for day, state, run_id, note, *rest in events:
            cell = by_day.get(day)
            if cell is None:
                continue
            cell.count += 1
            if run_id:
                cell.run_ids.append(run_id)
            if rest and rest[0]:
                cell.cycle_id = rest[0]
            if cell.state == "none" or _STATE_RANK.get(state, 5) < _STATE_RANK.get(cell.state, 5):
                cell.state = state
            if note:
                cell.note = note
        if cron:
            wd, tz = _weekdays(cron.get("day_of_week", "mon-sun")), tz_of[wid]
            due_at = (int(cron.get("hour", 0)), int(cron.get("minute", 0)))
            for d, cell in by_day.items():
                if cell.state != "none" or d.weekday() not in wd:
                    continue
                moment = datetime(d.year, d.month, d.day, *due_at, tzinfo=tz)
                if moment > now:
                    cell.state = "future"
                elif any(start <= moment and (end is None or moment < end) for start, end in pauses.get(wid, [])):
                    cell.state = "paused"
                elif wd <= set(range(5)) and not _is_trading_day(d, trading):
                    cell.state = "holiday"
                else:
                    cell.state = "missed"
        return [by_day[d] for d in day_list]

    rows = []
    seen = set()
    for wid, kind, cron in specs:
        seen.add(wid)
        tz = tz_of[wid]
        if kind == "research":
            events = [(parse_time(r["started_at"] or r["created_at"]).astimezone(tz).date(), r["status"],
                       r["run_id"], "") for r in research.get(wid, []) if parse_time(r["started_at"] or r["created_at"])]
        else:
            events = []
            for c in claims:
                if c["workflow"] != wid:
                    continue
                try:
                    sched = parse_time(json.loads(c["identity"] or "{}").get("scheduled_for"))
                except json.JSONDecodeError:
                    sched = None
                if sched:
                    note, cycle_id = "", ""
                    if wid == "chief-daily":
                        key = sched.astimezone(timezone.utc).strftime("chief-%Y%m%d-%H%M")
                        found = next((x for x in cycles if x["cycle_id"].startswith(key)), None)
                        note, cycle_id = (found["status"], found["cycle_id"]) if found else ("", "")
                    events.append((sched.astimezone(tz).date(), c["status"], "", note, cycle_id))
        row = WorkflowRow(wid, kind, _schedule_text(cron), cells_for(wid, cron, events))
        rows.append(row)
    for wid in sorted(set(research) - seen):
        tz = tz_of[wid]
        events = [(parse_time(r["started_at"] or r["created_at"]).astimezone(tz).date(), r["status"], r["run_id"], "")
                  for r in research[wid] if parse_time(r["started_at"] or r["created_at"])]
        rows.append(WorkflowRow(wid, "event", "事件触发", cells_for(wid, None, events)))
    for row in rows:
        for cell in row.cells:
            spent = [cost_by_run[r] for r in cell.run_ids if r in cost_by_run]
            if spent:
                row.llm_cost[cell.day] = sum(spent)
    return day_list, rows


def run_detail(paths: DashboardPaths, run_id: str) -> dict | None:
    with read_only(paths.ats) as ac:
        found = _rows(ac, "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,))
        if not found:
            return None
        run = found[0]
        tasks = _rows(ac, "SELECT task_instance_key, task_id, scope_json, attempt_no, status, error_code, "
                          "error_detail, started_at, ended_at, projection_refs_json FROM agent_runs WHERE run_id = ? "
                          "ORDER BY started_at, attempt_no", (run_id,))
        _attach_outputs(ac, tasks)
        history = _rows(ac, "SELECT * FROM workflow_run_status_history WHERE run_id = ? ORDER BY rowid", (run_id,))
        calls = _rows(ac, "SELECT role, model, count(*) AS n, sum(input_tokens) AS input_tokens, "
                          "sum(output_tokens) AS output_tokens, sum(reasoning_tokens) AS reasoning_tokens, "
                          "sum(cost_usd) AS cost, sum(cost_usd IS NULL) AS unknown, "
                          "group_concat(DISTINCT cost_source) AS sources "
                          "FROM llm_calls WHERE workflow_run_id = ? GROUP BY role, model", (run_id,))
    for t in tasks:
        start, end = parse_time(t["started_at"]), parse_time(t["ended_at"])
        t["seconds"] = (end - start).total_seconds() if start and end else None
    try:
        result = json.loads(run.get("result_json") or "{}")
    except json.JSONDecodeError:
        result = {}
    try:
        request = json.loads(run.get("request_json") or "{}")
    except json.JSONDecodeError:
        request = {}
    return {"run": run, "request": request, "tasks": tasks, "history": history, "llm": calls,
            "missing": result.get("missing_requirements") or []}


def _attach_outputs(conn, tasks: list[dict]) -> None:
    """Each task's projections (what it produced), oldest first, with vintages described and reuse marked.

    A projection created before the task started was carried over, not produced by this run: the Dispatcher
    reuses an unchanged projection instead of re-running the analyst, and the page must not present it as new.
    """
    from ...agents.provenance import describe_vintage_ref

    for t in tasks:
        refs = _json(t.pop("projection_refs_json", None)) or []
        refs = [r for r in refs if isinstance(r, str)]
        t["outputs"] = []
        if not refs:
            continue
        marks = ",".join("?" * len(refs))
        rows = _rows(conn, f"SELECT projection_id, agent_role, scope_id, as_of, created_at, status, payload, "
                           f"data_vintage_refs FROM task_projection_envelopes WHERE projection_id IN ({marks})",
                     tuple(refs))
        by_id = {r["projection_id"]: r for r in rows}
        started = parse_time(t.get("started_at"))
        for pid in refs:
            r = by_id.get(pid)
            if r is None:
                t["outputs"].append({"projection_id": pid, "missing": True})
                continue
            created = parse_time(r["created_at"])
            vintages = [x for x in (describe_vintage_ref(v) for v in _json(r["data_vintage_refs"]) or []
                                    if isinstance(v, str)) if x]
            t["outputs"].append({
                "projection_id": pid, "role": r["agent_role"], "scope_id": r["scope_id"],
                "as_of": parse_time(r["as_of"]), "created_at": created, "status": r["status"],
                "payload": _json(r["payload"]) or {}, "vintages": vintages,
                "reused": bool(started and created and created < started - timedelta(seconds=1))})


def llm_daily(paths: DashboardPaths, days: int = 14, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    with read_only(paths.ats) as ac:
        return _rows(ac, "SELECT substr(at, 1, 10) AS day, count(*) AS n, sum(input_tokens) AS input_tokens, "
                         "sum(output_tokens) AS output_tokens, sum(cost_usd) AS cost, "
                         "sum(cost_usd IS NULL) AS unknown FROM llm_calls WHERE at >= ? GROUP BY day ORDER BY day",
                     (since,))


# --------------------------------------------------------------------------- #
# decision lineage
# --------------------------------------------------------------------------- #
def decision_cycles(paths: DashboardPaths, limit: int = 30) -> list[dict]:
    with read_only(paths.ats) as ac:
        return _rows(ac, "SELECT cycle_id, status, created_at FROM decision_cycles "
                         "ORDER BY created_at DESC LIMIT ?", (limit,))


@dataclass
class LineageNode:
    label: str
    ref: str = ""
    children: list["LineageNode"] = field(default_factory=list)
    unknown: bool = False


def lineage(paths: DashboardPaths, cycle_id: str, depth: int = 2) -> dict | None:
    """Decision -> cited projections -> their data vintages (and, one level down, projections they consumed).

    Uses the same resolver as the Chief report's 依据清单, so the two always agree.
    """
    from ...agents.provenance import cite_projection, describe_vintage_ref

    with read_only(paths.ats) as ac:
        cycles = _rows(ac, "SELECT * FROM decision_cycles WHERE cycle_id = ?", (cycle_id,))
        if not cycles:
            return None
        rev = (_rows(ac, "SELECT * FROM decision_revisions WHERE cycle_id = ? ORDER BY revision_no DESC LIMIT 1",
                     (cycle_id,)) or [{}])[0]
        snap = json.loads(cycles[0].get("research_snapshot") or "{}")
        items = {i["projection_id"]: i for i in snap.get("items") or [] if i.get("projection_id")}
        rationale = _json(rev.get("rationale"))
        orders = _json(rev.get("orders_json")) or []
        decided_at = parse_time(snap.get("built_at")) or parse_time(rev.get("created_at")) \
            or parse_time(cycles[0].get("created_at")) or datetime.now(timezone.utc)

        def envelope(pid):
            return (_rows(ac, "SELECT projection_id, agent_role, scope_id, as_of, input_refs, data_vintage_refs "
                              "FROM task_projection_envelopes WHERE projection_id = ?", (pid,)) or [None])[0]

        def projection_node(pid, level, item=None) -> LineageNode:
            env = envelope(pid)
            item = item or (env and {"agent_role": env["agent_role"], "scope_id": env["scope_id"],
                                     "as_of": env["as_of"]})
            if not item:
                return LineageNode(f"❓ 未知引用 {pid[:8]}", pid, unknown=True)
            node = LineageNode(cite_projection(item, decided_at), pid)
            if env:
                node.children += [LineageNode(t) for t in
                                  (describe_vintage_ref(r) for r in _json(env["data_vintage_refs"]) or []) if t]
                if level < depth:
                    for ref in _json(env["input_refs"]) or []:
                        if isinstance(ref, str) and len(ref) == 32 and all(c in "0123456789abcdef" for c in ref):
                            node.children.append(projection_node(ref, level + 1))
            return node

        reasoning = (rationale or {}).get("reasoning") if isinstance(rationale, dict) else {}
        reasoning = reasoning or {}
        decisions = []
        for o in orders:
            n = LineageNode(f"{o.get('action')} {o.get('symbol')}" + (f" {o.get('qty'):g} 股" if o.get("qty") else ""))
            n.children = [projection_node(r, 1, items.get(r)) for r in o.get("references") or []]
            decisions.append(n)
        weighing = []
        for e in reasoning.get("evidence_weighing") or []:
            n = LineageNode(f"{e.get('source')}（{e.get('use')}）")
            n.children = [projection_node(r, 1, items.get(r)) for r in e.get("references") or []]
            weighing.append(n)
        audit = (rationale or {}).get("reference_audit") if isinstance(rationale, dict) else None
    return {"cycle": cycles[0], "decided_at": decided_at, "summary": (rationale or {}).get("summary", "")
            if isinstance(rationale, dict) else str(rationale or ""),
            "decisions": decisions, "weighing": weighing, "audit": audit or {}}


def _json(value):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "null")
    except (json.JSONDecodeError, TypeError):
        return value


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #
def overview(paths: DashboardPaths, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = source_rows(paths, now)
    counts = defaultdict(int)
    for r in rows:
        counts[r.light] += 1
    attention = sorted((r for r in rows if r.enabled and (r.light in ("red", "yellow") or r.last_status in FAILING)),
                       key=lambda r: (_LIGHT_ORDER[r.light], -r.consecutive_failures, r.job_id))
    days, wf_rows = workflow_grid(paths, now, days=1)
    today = [(w, w.cells[-1]) for w in wf_rows if w.kind != "event" or w.cells[-1].count]
    latest = decision_cycles(paths, limit=1)
    chief = None
    if latest:
        chief = latest[0]
        with read_only(paths.ats) as ac:
            snap = (_rows(ac, "SELECT research_snapshot FROM decision_cycles WHERE cycle_id = ?",
                          (chief["cycle_id"],)) or [{}])[0].get("research_snapshot")
        chief["freshness"] = _category_freshness(_json(snap) or {})
    return {"now": now, "counts": dict(counts), "attention": attention, "today": today, "chief": chief,
            "llm_today": llm_daily(paths, days=1, now=now)}


def _category_freshness(snap: dict) -> list[dict]:
    """Per research category, the newest projection's age at decision time and its freshness window."""
    from ...agents.provenance import ROLE_CN, age_text, freshness_seconds

    decided_at = parse_time(snap.get("built_at")) or datetime.now(timezone.utc)
    best: dict[str, dict] = {}
    for item in snap.get("items") or []:
        role = item.get("agent_role") or ""
        at = parse_time(item.get("as_of"))
        if role and at and (role not in best or at > best[role]["at"]):
            best[role] = {"role": role, "label": ROLE_CN.get(role, role), "at": at}
    out = []
    for b in best.values():
        window = freshness_seconds(b["role"])
        age = (decided_at - b["at"]).total_seconds()
        ratio = age / window if window else None
        out.append({**b, "age": age_text(b["at"], decided_at), "window": window,
                    "light": "red" if ratio and ratio > 1 else "yellow" if ratio and ratio > 0.5 else "green"})
    return sorted(out, key=lambda x: x["at"], reverse=True)


def runs_on_day(paths: DashboardPaths, workflow_id: str, day: date, now: datetime | None = None) -> list[dict]:
    """Every run of one research workflow on one (workflow-local) day, oldest first, with task counts."""
    _days, rows = workflow_grid(paths, now, days=max(1, ((now or datetime.now(timezone.utc)).date() - day).days + 2))
    cell = next((c for w in rows if w.workflow_id == workflow_id for c in w.cells if c.day == day), None)
    if cell is None or not cell.run_ids:
        return []
    marks = ",".join("?" * len(cell.run_ids))
    with read_only(paths.ats) as ac:
        runs = _rows(ac, f"SELECT run_id, status, started_at, ended_at, request_json, result_json FROM workflow_runs "
                         f"WHERE run_id IN ({marks}) ORDER BY started_at", tuple(cell.run_ids))
        tasks = _rows(ac, f"SELECT run_id, status, count(*) AS n FROM agent_runs WHERE run_id IN ({marks}) "
                          f"GROUP BY run_id, status", tuple(cell.run_ids))
    by_run: dict[str, dict[str, int]] = defaultdict(dict)
    for t in tasks:
        by_run[t["run_id"]][t["status"]] = t["n"]
    for r in runs:
        r["task_counts"] = by_run.get(r["run_id"], {})
        result = _json(r.pop("result_json", None)) or {}
        missing = result.get("missing_requirements") or [] if isinstance(result, dict) else []
        r["first_missing"] = str(missing[0].get("detail", ""))[:200] if missing and isinstance(missing[0], dict) else ""
        r["scope"] = (_json(r.pop("request_json", None)) or {}).get("scope") or {}
    return runs
