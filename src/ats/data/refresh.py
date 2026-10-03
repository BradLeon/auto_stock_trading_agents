"""Fail-closed, launchd-woken refresh controller for governed data sources.

The controller owns *when* an existing ``ats data`` command runs, not how a
provider is fetched or admitted. A successful command is not a publication:
the durable record keeps ingestion/admission references separately.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import calendar
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import dotenv_values

from ..config import REPO_ROOT
from .catalog.structured import StructuredCatalog
from .rollout_modes import source_mode
from .runtime.repository import platform_data_db_path

DEFAULT_CONFIG = REPO_ROOT / "config" / "data" / "schedules.yaml"
_WEEKDAYS = {name: index for index, name in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
_SAFE_ACTIONS = {"ingest", "release-check", "frontier-capability-audit"}
_SAFE_UNSTRUCTURED_ACTIONS = {"article-ingest", "document-ingest", "fixed-ingest"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RefreshJob:
    id: str
    domain: str
    sources: tuple[str, ...]
    datasets: tuple[str, ...]
    schedule: str
    at: time
    timezone_name: str
    command: tuple[str, ...]
    weekday: int | None
    interval_days: int
    anchor_date: date | None
    day_of_month: int | None
    max_attempts: int
    backoff_seconds: tuple[int, ...]
    freshness_slo_days: int
    enabled: bool
    min_interval_hours: int
    required_env: tuple[str, ...]
    approval_env: tuple[str, ...]
    retry_discovery_only: bool = False

    def window(self, now: datetime) -> tuple[str, datetime]:
        """Latest scheduled local window and its UTC due instant."""
        local = now.astimezone(ZoneInfo(self.timezone_name))
        if self.schedule == "daily":
            day = local.date()
            due = datetime.combine(day, self.at, tzinfo=local.tzinfo)
            if due > local:
                day -= timedelta(days=1)
                due = datetime.combine(day, self.at, tzinfo=local.tzinfo)
        elif self.weekday is not None:
            day = local.date() - timedelta(days=(local.weekday() - self.weekday) % 7)
            due = datetime.combine(day, self.at, tzinfo=local.tzinfo)
            if due > local:
                day -= timedelta(days=7)
                due = datetime.combine(day, self.at, tzinfo=local.tzinfo)
        elif self.schedule == "monthly":
            assert self.day_of_month is not None
            month_day = min(self.day_of_month,
                            calendar.monthrange(local.year, local.month)[1])
            day = local.date().replace(day=month_day)
            due = datetime.combine(day, self.at, tzinfo=local.tzinfo)
            if due > local:
                year, month = ((local.year - 1, 12) if local.month == 1
                               else (local.year, local.month - 1))
                month_day = min(self.day_of_month, calendar.monthrange(year, month)[1])
                day = date(year, month, month_day)
                due = datetime.combine(day, self.at, tzinfo=local.tzinfo)
        else:
            assert self.anchor_date is not None and self.interval_days > 0
            elapsed = (local.date() - self.anchor_date).days
            cycles = elapsed // self.interval_days
            day = self.anchor_date + timedelta(days=cycles * self.interval_days)
            due = datetime.combine(day, self.at, tzinfo=local.tzinfo)
            if due > local:
                day -= timedelta(days=self.interval_days)
                due = datetime.combine(day, self.at, tzinfo=local.tzinfo)
        return day.isoformat(), due.astimezone(timezone.utc)


@dataclass(frozen=True)
class RefreshPlan:
    jobs: tuple[RefreshJob, ...]
    owner: str
    ledger_path: Path
    misfire_grace_hours: int
    max_run_seconds: int


def load_plan(path: str | Path = DEFAULT_CONFIG) -> RefreshPlan:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if raw.get("version") != 1:
        raise ValueError("unsupported refresh schedule version")
    controller = raw.get("controller") or {}
    if controller.get("owner") != "launchd:com.ats.data-refresh":
        raise ValueError("refresh owner must be launchd:com.ats.data-refresh")
    jobs: list[RefreshJob] = []
    catalog = StructuredCatalog.load()
    sources = {source.id: source for source in catalog.sources()}
    datasets = {dataset.id: dataset for dataset in catalog.datasets()}
    from .catalog import DataCatalog
    unstructured_catalog = DataCatalog.load()
    unstructured_sources = {source.id: source for source in
                            unstructured_catalog.target_unstructured_sources()
                            if source.domain == "unstructured"}
    sections = [("structured", raw.get("structured") or {}),
                ("unstructured", raw.get("unstructured") or {})]
    for domain, section in sections:
      for job_id, body in (section.get("jobs") or {}).items():
        body = body or {}
        source_ids = tuple(body.get("sources") or ([body["source"]] if body.get("source") else []))
        dataset_ids = tuple(body.get("datasets") or [])
        if not source_ids or (domain == "structured" and not dataset_ids):
            raise ValueError(f"{job_id}: sources and structured datasets are required")
        if domain == "structured":
            for source_id in source_ids:
                if source_id not in sources:
                    raise ValueError(f"{job_id}: unknown structured source {source_id}")
                if sources[source_id].persistence.value != "persistent":
                    raise ValueError(f"{job_id}: runtime source cannot be refreshed")
                if not sources[source_id].constraints.get("internal_request_budget"):
                    raise ValueError(f"{job_id}: source {source_id} lacks request budget")
            for dataset_id in dataset_ids:
                if dataset_id not in datasets:
                    raise ValueError(f"{job_id}: unknown dataset {dataset_id}")
                if not any(dataset_id in sources[source_id].datasets for source_id in source_ids):
                    raise ValueError(f"{job_id}: dataset {dataset_id} has no listed source")
        else:
            for source_id in source_ids:
                if source_id not in unstructured_sources:
                    raise ValueError(f"{job_id}: unknown unstructured source {source_id}")
            if not dataset_ids or any(
                    dataset_id not in unstructured_sources[source_id].datasets
                    for source_id in source_ids for dataset_id in dataset_ids):
                raise ValueError(f"{job_id}: unstructured source/dataset mismatch")
        command = tuple(shlex.split(str(body.get("command") or "")))
        allowed_actions = _SAFE_ACTIONS if domain == "structured" else _SAFE_UNSTRUCTURED_ACTIONS
        if len(command) < 3 or command[:2] != ("ats", "data") or command[2] not in allowed_actions:
            raise ValueError(f"{job_id}: command must be an allowed ats data action")
        if any(token in command for token in ("--apply", "--confirm", "--force")):
            raise ValueError(f"{job_id}: refresh cannot publish, purge or force ingestion")
        at = time.fromisoformat(str(body.get("at") or ""))
        tz = str(body.get("timezone") or (raw.get("structured") or {}).get("default_timezone") or "")
        ZoneInfo(tz)
        cadence = str(body.get("schedule") or "")
        weekday = None
        interval_days = 0
        anchor_date = None
        day_of_month = None
        if cadence == "daily":
            pass
        elif cadence in {"weekly", "saturday"}:
            weekday_name = str(body.get("weekday") or ("saturday" if cadence == "saturday" else ""))
            if weekday_name not in _WEEKDAYS:
                raise ValueError(f"{job_id}: weekly schedule needs weekday")
            weekday = _WEEKDAYS[weekday_name]
        elif cadence.startswith("every_") and cadence.endswith("_days"):
            interval_days = int(body.get("interval_days") or 0)
            if interval_days <= 0 or cadence != f"every_{interval_days}_days":
                raise ValueError(f"{job_id}: inconsistent interval_days")
            anchor = body.get("anchor_date")
            anchor_date = anchor if isinstance(anchor, date) else date.fromisoformat(str(anchor))
        elif cadence == "monthly":
            day_of_month = int(body.get("day_of_month") or 0)
            if not 1 <= day_of_month <= 31:
                raise ValueError(f"{job_id}: monthly schedule needs day_of_month 1..31")
        else:
            raise ValueError(f"{job_id}: unsupported schedule {cadence!r}")
        retry = body.get("retry") or {}
        scope = body.get("scope") or {}
        required_env = [str(name) for name in (scope.get("required_env") or [])]
        if scope.get("api_key_env"):
            required_env.append(str(scope["api_key_env"]))
        attempts = int(retry.get("attempts") or 1)
        backoff = tuple(int(value) for value in (retry.get("backoff_seconds") or []))
        if attempts < 1 or any(value < 0 for value in backoff):
            raise ValueError(f"{job_id}: invalid retry policy")
        freshness = int(body.get("freshness_slo_days") or 0)
        if freshness <= 0:
            raise ValueError(f"{job_id}: freshness_slo_days is required")
        jobs.append(RefreshJob(
            id=job_id, domain=domain, sources=source_ids, datasets=dataset_ids, schedule=cadence,
            at=at, timezone_name=tz, command=command, weekday=weekday,
            interval_days=interval_days, anchor_date=anchor_date,
            day_of_month=day_of_month,
            max_attempts=attempts, backoff_seconds=backoff,
            freshness_slo_days=freshness,
            enabled=bool(body.get("enabled", controller.get("default_enabled", False))),
            min_interval_hours=int(body.get("min_interval_hours") or 0),
            required_env=tuple(dict.fromkeys(required_env)),
            approval_env=tuple(str((body.get("scope") or {})[key]) for key in
                               ("public_terms_approval_env", "public_structured_page_policy_env")
                               if (body.get("scope") or {}).get(key)),
            retry_discovery_only=bool(body.get("retry_discovery_only", False)),
        ))
    ledger_value = Path(str(controller.get("ledger") or "var/data_refresh.sqlite"))
    ledger = ledger_value if ledger_value.is_absolute() else REPO_ROOT / ledger_value
    return RefreshPlan(
        jobs=tuple(jobs), owner=str(controller["owner"]), ledger_path=ledger,
        misfire_grace_hours=int(controller.get("misfire_grace_hours") or 24),
        max_run_seconds=int(controller.get("max_run_seconds") or 1800),
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS refresh_runs (
    trigger_key TEXT PRIMARY KEY, job_id TEXT NOT NULL, window_date TEXT NOT NULL,
    source_ids_json TEXT NOT NULL, dataset_ids_json TEXT NOT NULL,
    command_json TEXT NOT NULL, status TEXT NOT NULL, reason_code TEXT NOT NULL DEFAULT '',
    claimed_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT NOT NULL DEFAULT '', exit_code INTEGER,
    stdout_sha256 TEXT NOT NULL DEFAULT '', ingestion_run_ids_json TEXT NOT NULL DEFAULT '[]',
    source_check_ids_json TEXT NOT NULL DEFAULT '[]',
    raw_asset_ids_json TEXT NOT NULL DEFAULT '[]', admission_ids_json TEXT NOT NULL DEFAULT '[]',
    published_ids_json TEXT NOT NULL DEFAULT '[]', queue_task_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS refresh_attempts (
    trigger_key TEXT NOT NULL, attempt_no INTEGER NOT NULL, started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL, status TEXT NOT NULL, exit_code INTEGER,
    stdout_sha256 TEXT NOT NULL DEFAULT '', reason_code TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(trigger_key, attempt_no)
);
CREATE TABLE IF NOT EXISTS refresh_controller_ticks (
    tick_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, owner TEXT NOT NULL,
    due_jobs INTEGER NOT NULL, attempted_jobs INTEGER NOT NULL,
    invocation_kind TEXT NOT NULL DEFAULT 'legacy_unknown'
);
CREATE TABLE IF NOT EXISTS refresh_skip_events (
    event_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, window_date TEXT NOT NULL,
    observed_at TEXT NOT NULL, status TEXT NOT NULL, reason_code TEXT NOT NULL
);
"""


class RefreshLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(refresh_runs)")}
            if "source_check_ids_json" not in columns:
                conn.execute("ALTER TABLE refresh_runs ADD COLUMN source_check_ids_json "
                             "TEXT NOT NULL DEFAULT '[]'")
            if "queue_task_id" not in columns:
                conn.execute("ALTER TABLE refresh_runs ADD COLUMN queue_task_id "
                             "TEXT NOT NULL DEFAULT ''")
            tick_columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(refresh_controller_ticks)")}
            if "invocation_kind" not in tick_columns:
                conn.execute("ALTER TABLE refresh_controller_ticks ADD COLUMN "
                             "invocation_kind TEXT NOT NULL DEFAULT 'legacy_unknown'")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def claim(self, job: RefreshJob, window: str, now: datetime,
              *, max_run_seconds: int) -> tuple[str, int] | None:
        key = hashlib.sha256(f"{job.id}|{window}".encode()).hexdigest()[:32]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM refresh_runs WHERE trigger_key=?", (key,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO refresh_runs(trigger_key,job_id,window_date,source_ids_json,"
                    "dataset_ids_json,command_json,status,claimed_at,started_at,attempts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,1)",
                    (key, job.id, window, json.dumps(job.sources), json.dumps(job.datasets),
                     json.dumps(job.command), "running", _iso(now), _iso(now)))
                return key, 1
            terminal = {"succeeded", "no_change", "skipped", "quarantined"}
            if not job.retry_discovery_only:
                terminal.add("discovery_only")
            if row["status"] in terminal:
                return None
            if row["attempts"] >= job.max_attempts:
                return None
            if row["status"] == "running" and row["started_at"]:
                age = now - datetime.fromisoformat(row["started_at"])
                if age.total_seconds() <= max_run_seconds:
                    return None
            if row["next_retry_at"] and now < datetime.fromisoformat(row["next_retry_at"]):
                return None
            attempt = int(row["attempts"]) + 1
            conn.execute("UPDATE refresh_runs SET status='running',started_at=?,attempts=?,"
                         "next_retry_at='',reason_code='' WHERE trigger_key=?",
                         (_iso(now), attempt, key))
            return key, attempt

    def finish(self, key: str, attempt: int, started: datetime, ended: datetime,
               *, status: str, exit_code: int | None, reason: str, stdout_hash: str,
               refs: dict[str, list[str]], job: RefreshJob) -> None:
        delay = job.backoff_seconds[min(attempt - 1, len(job.backoff_seconds) - 1)] \
            if status == "failed" and job.backoff_seconds else 0
        next_retry = _iso(ended + timedelta(seconds=delay)) if status == "failed" else ""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO refresh_attempts VALUES(?,?,?,?,?,?,?,?)",
                (key, attempt, _iso(started), _iso(ended), status, exit_code,
                 stdout_hash, reason))
            conn.execute(
                "UPDATE refresh_runs SET status=?,reason_code=?,completed_at=?,"
                "next_retry_at=?,exit_code=?,stdout_sha256=?,ingestion_run_ids_json=?,"
                "source_check_ids_json=?,raw_asset_ids_json=?,admission_ids_json=?,published_ids_json=? "
                "WHERE trigger_key=? AND attempts=?",
                (status, reason, _iso(ended), next_retry, exit_code, stdout_hash,
                 json.dumps(refs.get("runs", [])), json.dumps(refs.get("checks", [])),
                 json.dumps(refs.get("raw", [])),
                 json.dumps(refs.get("admission", [])), json.dumps(refs.get("published", [])),
                 key, attempt))

    def latest_success(self, job_id: str) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute("SELECT completed_at FROM refresh_runs WHERE job_id=? "
                               "AND status IN ('succeeded','no_change') "
                               "ORDER BY completed_at DESC LIMIT 1", (job_id,)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def record_queue_task(self, key: str, task_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE refresh_runs SET queue_task_id=? WHERE trigger_key=?",
                         (task_id, key))

    def rows(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM refresh_runs ORDER BY claimed_at DESC").fetchall()]

    def record_skip(self, job_id: str, window: str, now: datetime,
                    status: str, reason: str) -> None:
        identity = f"{job_id}|{window}|{status}|{reason}|{now:%Y-%m-%dT%H}"
        event_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO refresh_skip_events VALUES(?,?,?,?,?,?)",
                         (event_id, job_id, window, _iso(now), status, reason))


def _refresh_environment() -> dict[str, str]:
    """Give launchd children the same local secret file as interactive commands.

    Never put these values in the schedule, ledger, command line or result log.
    An explicitly supplied process environment takes precedence over ``.env``.
    """
    env_file = Path(os.environ.get("ATS_ENV_FILE") or REPO_ROOT / ".env")
    values = dotenv_values(env_file) if env_file.is_file() else {}
    return {**{key: value for key, value in values.items() if value is not None},
            **os.environ}


def _safe_sources(job: RefreshJob, env: dict[str, str]) -> str:
    if job.domain == "structured":
        catalog = {source.id: source for source in StructuredCatalog.load().sources()}
        for source_id in job.sources:
            source = catalog[source_id]
            if source.catalog_status.value in {"planned", "deferred", "runtime_excluded"}:
                return "source_not_active"
            if source_mode(source_id) not in {"shadow", "platform", "fallback"}:
                return "source_disabled"
            budget = source.constraints.get("internal_request_budget") or {}
            if int(budget.get("requests_per_run") or 0) < 1:
                return "source_budget_missing"
    else:
        from .catalog import DataCatalog
        unstructured = {source.id: source for source in
                        DataCatalog.load().target_unstructured_sources()
                        if source.domain == "unstructured"}
        for source_id in job.sources:
            source = unstructured[source_id]
            if source.status in {"planned", "deferred", "disabled", "runtime_excluded"}:
                return "source_not_active"
            if not any(int(value or 0) > 0 for value in source.request_budget.values()):
                return "source_budget_missing"
    if any(not env.get(name, "").strip() for name in job.required_env):
        return "credential_missing"
    if any(env.get(name, "").strip().lower() not in {"1", "true", "yes"}
           for name in job.approval_env):
        return "source_approval_missing"
    return ""


def _data_max_rowid(db_path: Path, table: str) -> int:
    if table not in {"structured_ingestion_runs", "structured_source_checks"}:
        raise ValueError("unsupported refresh reference table")
    if not db_path.exists():
        return 0
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        try:
            row = conn.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()
            return int(row[0] or 0)
        except sqlite3.OperationalError:
            return 0


def _references(db_path: Path, after_rowid: int, after_check_rowid: int,
                job: RefreshJob) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {"runs": [], "checks": [], "raw": [],
                                  "admission": [], "published": []}
    if not db_path.exists():
        return refs
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT run_id,source_id,dataset_id,status FROM "
                                "structured_ingestion_runs WHERE rowid>?", (after_rowid,)).fetchall()
        except sqlite3.OperationalError:
            return refs
        relevant = [row for row in rows if row["source_id"] in job.sources and
                    row["dataset_id"] in job.datasets]
        refs["runs"] = [row["run_id"] for row in relevant]
        for row in conn.execute("SELECT check_id,source_id,dataset_id FROM "
                                "structured_source_checks WHERE rowid>?", (after_check_rowid,)):
            if row["source_id"] in job.sources and row["dataset_id"] in job.datasets:
                refs["checks"].append(row["check_id"])
        for row in relevant:
            for candidate in conn.execute(
                "SELECT candidate_id,artifact_id,status FROM structured_candidates WHERE run_id=?",
                (row["run_id"],)):
                refs["admission"].append(candidate["candidate_id"])
                if candidate["artifact_id"]:
                    refs["raw"].append(candidate["artifact_id"])
            if row["status"] in {"succeeded", "no_change"}:
                for obs in conn.execute(
                    "SELECT observation_id FROM structured_observations WHERE artifact_id IN "
                    "(SELECT DISTINCT artifact_id FROM structured_candidates WHERE run_id=?)",
                    (row["run_id"],)):
                    refs["published"].append(obs["observation_id"])
    return {key: sorted(set(value)) for key, value in refs.items()}


def _unstructured_references(db_path: Path, packet: dict[str, Any],
                             job: RefreshJob) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {"runs": [], "checks": [], "raw": [],
                                  "admission": [], "published": []}
    if not db_path.exists():
        return refs
    run_id = str(packet.get("run_id") or "")
    if not run_id:
        return refs
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            run = conn.execute("SELECT source_id,kind FROM data_ingestion_runs WHERE run_id=?",
                               (run_id,)).fetchone()
            if not run or run["source_id"] not in job.sources or \
                    run["kind"] not in {"article_refresh", "fixed_source_refresh"}:
                return refs
            refs["runs"].append(run_id)
            fixed_row_ids = [str(value) for value in packet.get("row_ids", [])]
            if fixed_row_ids and run["kind"] == "fixed_source_refresh":
                placeholders = ",".join("?" for _ in fixed_row_ids)
                fixed = conn.execute(
                    f"SELECT row_id,status FROM data_fixed_source_rows "
                    f"WHERE row_id IN ({placeholders}) AND source_id=?",
                    [*fixed_row_ids, run["source_id"]]).fetchall()
                refs["raw"].extend(row["row_id"] for row in fixed)
                refs["admission"].extend(row["row_id"] for row in fixed
                                         if row["status"] in {"accepted", "quarantined"})
            candidate_ids = [str(value) for value in packet.get("candidate_ids", [])]
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                candidates = conn.execute(
                    f"SELECT candidate_id,raw_path FROM data_document_candidates "
                    f"WHERE candidate_id IN ({placeholders})", candidate_ids).fetchall()
                refs["admission"] = [row["candidate_id"] for row in candidates]
                refs["raw"] = [row["raw_path"] for row in candidates if row["raw_path"]]
            refs["published"] = [str(value) for value in
                                 packet.get("published_version_ids", [])]
    except sqlite3.OperationalError:
        return refs
    return {key: sorted(set(value)) for key, value in refs.items()}


def run_tick(plan: RefreshPlan, *, now: datetime | None = None, dry_run: bool = False,
             only_job: str = "", data_db: Path | None = None) -> list[dict[str, Any]]:
    now = now or _utc_now()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    ledger = None if dry_run else RefreshLedger(plan.ledger_path)
    data_db = data_db or platform_data_db_path()
    child_env = _refresh_environment()
    results: list[dict[str, Any]] = []
    for job in plan.jobs:
        if only_job and job.id != only_job:
            continue
        window, due = job.window(now)
        age_hours = (now - due).total_seconds() / 3600
        if not job.enabled or age_hours < 0:
            status = "disabled" if not job.enabled else "not_due"
            results.append({"job": job.id, "window": window, "status": status})
            if ledger:
                ledger.record_skip(job.id, window, now, status, status)
            continue
        if age_hours > plan.misfire_grace_hours:
            results.append({"job": job.id, "window": window, "status": "misfire"})
            if ledger:
                ledger.record_skip(job.id, window, now, "misfire", "misfire_grace_exceeded")
            continue
        reason = _safe_sources(job, child_env)
        latest = ledger.latest_success(job.id) if ledger else None
        if latest and job.min_interval_hours and (now - latest).total_seconds() < job.min_interval_hours * 3600:
            reason = "minimum_interval"
        if reason:
            results.append({"job": job.id, "window": window, "status": "deferred", "reason": reason})
            if ledger:
                ledger.record_skip(job.id, window, now, "deferred", reason)
            continue
        if dry_run:
            results.append({"job": job.id, "window": window, "status": "due",
                            "command": list(job.command)})
            continue
        assert ledger is not None
        claim = ledger.claim(job, window, now, max_run_seconds=plan.max_run_seconds)
        if claim is None:
            results.append({"job": job.id, "window": window, "status": "already_claimed"})
            continue
        key, attempt = claim
        started = _utc_now()
        before_rowid = _data_max_rowid(data_db, "structured_ingestion_runs")
        before_check_rowid = _data_max_rowid(data_db, "structured_source_checks")
        from .persistent_queue import PersistentIngestionQueue

        try:
            queue = PersistentIngestionQueue(plan.ledger_path.parent / "persistent_ingestion.sqlite")
            policy = hashlib.sha256(json.dumps(
                {"job": job.id, "sources": job.sources, "datasets": job.datasets,
                 "command": job.command, "max_attempts": job.max_attempts,
                 "freshness_slo_days": job.freshness_slo_days},
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            task_id, _created = queue.enqueue(
                source_id="+".join(job.sources),
                scope={"job_id": job.id, "window": window, "datasets": list(job.datasets),
                       "sources": list(job.sources)},
                trigger_kind="scheduled", trigger_ref=f"{job.id}:{window}",
                command=job.command, policy_fingerprint=policy,
                requested_as_of=_iso(now), priority=30,
                max_attempts=job.max_attempts,
                backoff_seconds=job.backoff_seconds, parent_run_id=key, now=now)
            worker = queue.run_one(task_id=task_id, timeout_seconds=plan.max_run_seconds)
            if worker is None:
                raise RuntimeError("queue_task_not_claimable")
            packet = worker.get("stdout") or {}
            packet_status = str(worker.get("reported_status") or "")
            code = worker.get("exit_code")
            output_hash = str((worker.get("result") or {}).get("stdout_sha256") or "")
            refs = (_unstructured_references(data_db, packet, job)
                    if job.domain == "unstructured" else
                    _references(data_db, before_rowid, before_check_rowid, job))
            queue.record_lineage(task_id, refs)
            ledger.record_queue_task(key, task_id)
            statuses = []
            if refs["runs"] and job.domain == "structured":
                with sqlite3.connect(f"file:{data_db}?mode=ro", uri=True) as conn:
                    statuses = [r[0] for r in conn.execute(
                        "SELECT status FROM structured_ingestion_runs WHERE run_id IN (" +
                        ",".join("?" for _ in refs["runs"]) + ")", refs["runs"]).fetchall()]
            action = job.command[2]
            publication_expected = action == "ingest" or \
                (action == "release-check" and "--ingest-new" in job.command) or \
                action == "frontier-capability-audit" or action in {"article-ingest", "document-ingest", "fixed-ingest"}
            accepted_packet_statuses = {"succeeded", "no_change"}
            if action in {"calendar-refresh", "calendar-finalize"}:
                accepted_packet_statuses.add("partial")
            if action in {"article-ingest", "document-ingest", "fixed-ingest"}:
                accepted_packet_statuses |= {"quarantined", "pending_human_review",
                                             "partial", "unreachable"}
            if code != 0 or not packet_status or packet_status not in accepted_packet_statuses:
                status, reason = "failed", "command_or_result_failed"
            elif action in {"calendar-refresh", "calendar-finalize"}:
                status, reason = packet_status, ""
            elif any(s not in {"succeeded", "no_change"} for s in statuses):
                status, reason = "failed", "ingestion_failed"
            elif action == "ingest" and not refs["runs"]:
                status, reason = "failed", "ingestion_run_missing"
            elif publication_expected and packet_status == "succeeded" and not refs["runs"]:
                status, reason = "failed", "discovered_release_without_ingestion"
            elif action in {"release-check", "frontier-capability-audit"} and \
                    not refs["runs"] and not refs["checks"]:
                status, reason = "failed", "source_check_missing"
            elif action in {"article-ingest", "document-ingest", "fixed-ingest"} and not refs["runs"]:
                status, reason = "failed", "article_ingestion_run_missing"
            elif action in {"article-ingest", "document-ingest", "fixed-ingest"} and packet_status == "quarantined":
                status, reason = "quarantined", "article_admission_rejected"
            elif action == "article-ingest" and packet_status == "pending_human_review":
                status, reason = "discovery_only", "human_review_required"
            elif action == "article-ingest" and packet_status == "unreachable":
                status, reason = "failed", "article_source_unreachable"
            elif action in {"article-ingest", "document-ingest", "fixed-ingest"} and packet_status == "partial":
                status, reason = "failed", "article_source_partial"
            elif action in {"article-ingest", "document-ingest", "fixed-ingest"} and packet_status == "no_change":
                status, reason = "no_change", ""
            elif action in {"article-ingest", "document-ingest", "fixed-ingest"} and packet_status == "succeeded" and refs["published"]:
                status, reason = "succeeded", ""
            elif action in {"document-ingest", "fixed-ingest"} and packet_status == "succeeded" and refs["runs"] and not refs["published"] and \
                    job.sources == ("defeatbeta_sec_filing_index",):
                status, reason = "succeeded", "index_metadata_only"
            elif action in {"article-ingest", "document-ingest", "fixed-ingest"} and packet_status == "succeeded":
                status, reason = "quarantined", "article_publication_reference_missing"
            elif refs["runs"] and all(s == "no_change" for s in statuses):
                status, reason = "no_change", ""
            elif refs["runs"] and not refs["published"]:
                status, reason = "quarantined", "no_accepted_publication"
            elif action == "release-check" and "--ingest-new" not in job.command and \
                    packet_status == "succeeded":
                status, reason = "discovery_only", "not_published"
            elif not refs["runs"]:
                status, reason = "no_change", "upstream_no_change"
            else:
                status, reason = "succeeded", ""
        except subprocess.TimeoutExpired:
            code, output_hash, refs = None, "", {"runs": [], "checks": [], "raw": [], "admission": [], "published": []}
            status, reason = "failed", "command_timeout"
        except Exception as exc:  # source process/SQLite errors must close the claim
            code, output_hash, refs = None, "", {"runs": [], "checks": [], "raw": [], "admission": [], "published": []}
            status, reason = "failed", f"controller_{type(exc).__name__}"
        ended = _utc_now()
        ledger.finish(key, attempt, started, ended, status=status, exit_code=code,
                      reason=reason, stdout_hash=output_hash, refs=refs, job=job)
        results.append({"job": job.id, "window": window, "status": status,
                        "reason": reason, "trigger_key": key, "attempt": attempt,
                        "ingestion_runs": len(refs["runs"]), "source_checks": len(refs["checks"]),
                        "published": len(refs["published"])})
    if ledger:
        tick_at = _iso(now)
        launchd_label = "com.ats.data-refresh"
        tick_owner = (plan.owner if os.environ.get("XPC_SERVICE_NAME") == launchd_label
                      else f"manual:{plan.owner}")
        invocation_kind = "launchd" if os.environ.get("XPC_SERVICE_NAME") == launchd_label else "manual"
        tick_id = hashlib.sha256(f"{tick_owner}|{invocation_kind}|{tick_at}".encode()).hexdigest()[:24]
        with ledger._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO refresh_controller_ticks "
                         "(tick_id,started_at,owner,due_jobs,attempted_jobs,invocation_kind) "
                         "VALUES(?,?,?,?,?,?)",
                         (tick_id, tick_at, tick_owner, sum(x["status"] in {"due", "already_claimed", "succeeded", "no_change", "failed", "quarantined"} for x in results),
                          sum(x["status"] in {"succeeded", "no_change", "failed", "quarantined"} for x in results),
                          invocation_kind))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governed data refresh controller")
    parser.add_argument("action", choices=("validate", "tick", "status"))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--ledger", default="")
    parser.add_argument("--job", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    plan = load_plan(args.config)
    if args.ledger:
        plan = RefreshPlan(plan.jobs, plan.owner, Path(args.ledger),
                           plan.misfire_grace_hours, plan.max_run_seconds)
    if args.action == "validate":
        print(json.dumps({"valid": True, "owner": plan.owner,
                          "jobs": [job.id for job in plan.jobs]}))
        return 0
    if args.action == "status":
        if not plan.ledger_path.exists():
            print(json.dumps({"binding": "unbound", "reason": "no_controller_run_ledger"}))
            return 0
        refresh_ledger = RefreshLedger(plan.ledger_path)
        with sqlite3.connect(f"file:{plan.ledger_path}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT started_at FROM refresh_controller_ticks "
                               "WHERE owner=? AND invocation_kind='launchd' "
                               "ORDER BY started_at DESC LIMIT 1", (plan.owner,)).fetchone()
        last_tick = row[0] if row else ""
        loaded = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.ats.data-refresh"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False).returncode == 0 if sys.platform == "darwin" else False
        fresh = bool(last_tick and (_utc_now() - datetime.fromisoformat(last_tick)).total_seconds()
                     <= 2 * 3600)
        print(json.dumps({"binding": "active" if loaded and fresh else "unbound",
                          "launchd_loaded": loaded, "last_tick": last_tick,
                          "runs": refresh_ledger.rows()}, default=str))
        return 0
    # Operators can safely request a single launchd-bound validation without
    # causing the hourly controller to run every other currently-due source.
    # The override is intentionally one-shot via `launchctl setenv`/unsetenv;
    # normal unattended ticks remain unchanged when it is absent.
    only_job = args.job or os.environ.get("ATS_DATA_REFRESH_ONLY_JOB", "").strip()
    results = run_tick(plan, dry_run=args.dry_run, only_job=only_job)
    print(json.dumps(results, default=str))
    return 1 if any(row["status"] in {"failed", "quarantined"} for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
