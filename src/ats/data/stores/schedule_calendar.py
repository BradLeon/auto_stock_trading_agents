"""Versioned persistence for schedule-calendar candidates and event facts.

This database stores publication timing and provenance only; it is not a financial
results store and it never overwrites a previously visible event version.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..runtime.repository import platform_data_db_path

EventStatus = Literal["planned", "released", "cancelled"]
TimePrecision = Literal["date", "minute", "unknown"]
MarketSession = Literal["bmo", "amc", "intraday", "unknown"]


def earnings_identity(entity: str, fiscal_year: int | None, fiscal_quarter: int | None,
                      subevent: str = "release") -> str | None:
    if not entity or fiscal_year is None or fiscal_quarter not in {1, 2, 3, 4}:
        return None
    return f"earnings:{entity.upper()}:FY{int(fiscal_year)}Q{int(fiscal_quarter)}:{subevent}"


def macro_identity(series: str, reference_period: str, release_phase: str) -> str | None:
    if not series or not reference_period or not release_phase:
        return None
    return f"macro:{series.upper()}:{reference_period}:{release_phase.lower()}"


def fomc_identity(meeting_year: int, meeting_month: int, subevent: str = "statement") -> str:
    if not 1 <= int(meeting_month) <= 12:
        raise ValueError("meeting_month must be between 1 and 12")
    return f"fomc:{int(meeting_year):04d}-{int(meeting_month):02d}:{subevent}"


class ScheduleEventCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    source_url: str = ""
    source_event_ref: str = ""
    event_type: str = Field(min_length=1)
    stable_identity: str = ""
    label: str = ""
    reference_period: str = ""
    event_date: date | None = None
    local_time: str = ""
    timezone: str = ""
    utc_at: str = ""
    time_precision: TimePrecision = "unknown"
    market_session: MarketSession = "unknown"
    status: EventStatus = "planned"
    announced_at: str = ""
    fetched_at: str = ""
    confidence: float = Field(default=1.0, ge=0, le=1)
    license_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_date", mode="before")
    @classmethod
    def _parse_date(cls, value):
        if value in (None, ""):
            return None
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    @field_validator("local_time")
    @classmethod
    def _validate_time(cls, value):
        if value and not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("local_time must use HH:MM")
        return value

    @model_validator(mode="after")
    def _validate_time_contract(self):
        if self.time_precision == "date" and (self.local_time or self.utc_at):
            raise ValueError("date-precision calendar events must not invent a clock time")
        if self.time_precision == "minute":
            if not (self.event_date and self.local_time and self.timezone):
                raise ValueError("minute precision requires date, local_time and IANA timezone")
            try:
                zone = ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"unknown IANA timezone {self.timezone!r}") from exc
            local = datetime.fromisoformat(f"{self.event_date.isoformat()}T{self.local_time}")
            computed = local.replace(tzinfo=zone).astimezone(timezone.utc).isoformat()
            if self.utc_at and datetime.fromisoformat(self.utc_at.replace("Z", "+00:00")) \
                    != datetime.fromisoformat(computed):
                raise ValueError("utc_at does not match local date/time and timezone")
            object.__setattr__(self, "utc_at", computed)
        if self.time_precision == "date" and self.event_date is None:
            raise ValueError("date precision requires event_date")
        return self

    @property
    def event_id(self) -> str:
        if not self.stable_identity:
            return ""
        if self.stable_identity.lower().startswith(self.event_type.lower() + ":"):
            return self.stable_identity
        return f"{self.event_type.lower()}:{self.stable_identity}"

    def trigger_signature(self) -> tuple[str, ...]:
        return (self.event_type.lower(), self.event_date.isoformat() if self.event_date else "",
                self.local_time, self.timezone, self.utc_at, self.time_precision,
                self.market_session, self.status)

    def normalized(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["event_id"] = self.event_id
        return payload

    @property
    def payload_hash(self) -> str:
        payload = self.normalized()
        payload.pop("fetched_at", None)
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @property
    def candidate_id(self) -> str:
        identity = f"{self.source_id}|{self.source_event_ref}|{self.payload_hash}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_event_candidates (
    candidate_id TEXT PRIMARY KEY, event_id TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '', source_event_ref TEXT NOT NULL DEFAULT '',
    payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL, signature_json TEXT NOT NULL,
    review_status TEXT NOT NULL, review_reason TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL, UNIQUE(source_id,source_event_ref,payload_hash)
);
CREATE INDEX IF NOT EXISTS idx_schedule_candidates_event
    ON schedule_event_candidates(event_id,review_status,fetched_at);
CREATE TABLE IF NOT EXISTS schedule_events (
    event_id TEXT NOT NULL, event_version INTEGER NOT NULL, event_type TEXT NOT NULL,
    reference_period TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',
    event_date TEXT NOT NULL DEFAULT '', local_time TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT '', utc_at TEXT NOT NULL DEFAULT '',
    time_precision TEXT NOT NULL, market_session TEXT NOT NULL,
    status TEXT NOT NULL, announced_at TEXT NOT NULL DEFAULT '', valid_from TEXT NOT NULL,
    valid_until TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL,
    PRIMARY KEY(event_id,event_version)
);
CREATE INDEX IF NOT EXISTS idx_schedule_events_validity
    ON schedule_events(event_id,valid_from,valid_until);
CREATE INDEX IF NOT EXISTS idx_schedule_events_date
    ON schedule_events(event_date,event_type,status);
CREATE TABLE IF NOT EXISTS schedule_event_sources (
    event_id TEXT NOT NULL, event_version INTEGER NOT NULL, candidate_id TEXT NOT NULL,
    source_id TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '', linked_at TEXT NOT NULL,
    PRIMARY KEY(event_id,event_version,candidate_id),
    FOREIGN KEY(event_id,event_version) REFERENCES schedule_events(event_id,event_version),
    FOREIGN KEY(candidate_id) REFERENCES schedule_event_candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS schedule_event_overrides (
    override_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, actor TEXT NOT NULL,
    reason TEXT NOT NULL, evidence_json TEXT NOT NULL, action TEXT NOT NULL,
    base_snapshot_json TEXT NOT NULL, event_version INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedule_override_active
    ON schedule_event_overrides(event_id,active,created_at);
CREATE TABLE IF NOT EXISTS schedule_event_release_confirmations (
    event_id TEXT NOT NULL, event_version INTEGER NOT NULL, source_id TEXT NOT NULL,
    materials_json TEXT NOT NULL, confirmed_at TEXT NOT NULL,
    PRIMARY KEY(event_id,event_version,source_id),
    FOREIGN KEY(event_id,event_version) REFERENCES schedule_events(event_id,event_version)
);
CREATE TABLE IF NOT EXISTS schedule_calendar_source_runs (
    source_run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, discovered INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 0, conflicts INTEGER NOT NULL DEFAULT 0,
    quarantined INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '',
    error_detail TEXT NOT NULL DEFAULT '', provenance_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_schedule_source_runs
    ON schedule_calendar_source_runs(source_id,started_at);
"""


def _stamp(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str)


class ScheduleCalendarStore:
    """Per-operation SQLite store; it shares the data DB file but owns only its tables."""

    def __init__(self, path: str | Path | None = None, *, busy_timeout_ms: int = 30_000):
        self.path = str(path or platform_data_db_path())
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.bootstrap()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000,
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def bootstrap(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    def _tx(self):
        from contextlib import contextmanager

        @contextmanager
        def transaction():
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return transaction()

    def record_source_run(self, source_id: str, *, at: datetime | None = None,
                          provenance: Mapping[str, Any] | None = None) -> str:
        source_run_id = hashlib.sha256(f"{source_id}|{_stamp(at)}".encode()).hexdigest()[:32]
        with self._tx() as conn:
            conn.execute("INSERT INTO schedule_calendar_source_runs(source_run_id,source_id,"
                         "started_at,status,provenance_json) VALUES(?,?,?,'running',?)",
                         (source_run_id, source_id, _stamp(at), _json(provenance or {})))
        return source_run_id

    def finish_source_run(self, source_run_id: str, *, status: str, discovered: int = 0,
                          published: int = 0, conflicts: int = 0, quarantined: int = 0,
                          error_code: str = "", error_detail: str = "",
                          at: datetime | None = None) -> None:
        if status not in {"complete", "partial", "failed"}:
            raise ValueError("invalid calendar source run status")
        with self._tx() as conn:
            cur = conn.execute("UPDATE schedule_calendar_source_runs SET status=?,finished_at=?,"
                               "discovered=?,published=?,conflicts=?,quarantined=?,error_code=?,"
                               "error_detail=? WHERE source_run_id=? AND status='running'",
                               (status, _stamp(at), discovered, published, conflicts, quarantined,
                                error_code, error_detail[:1000], source_run_id))
            if cur.rowcount != 1:
                raise ValueError(f"calendar source run {source_run_id!r} is not running")

    def submit_candidate(self, candidate: ScheduleEventCandidate, *,
                         at: datetime | None = None) -> dict[str, Any]:
        candidate_id, event_id = candidate.candidate_id, candidate.event_id
        fetched = candidate.fetched_at or _stamp(at)
        review_status, reason = (("pending_identity", "stable_identity_missing")
                                 if not event_id else ("candidate", ""))
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM schedule_event_candidates WHERE candidate_id=?",
                               (candidate_id,)).fetchone()
            if row:
                return self._candidate_dict(row, duplicate=True)
            if event_id:
                current = conn.execute("SELECT payload_json,event_version FROM schedule_events "
                                       "WHERE event_id=? AND valid_until='' "
                                       "ORDER BY event_version DESC LIMIT 1", (event_id,)).fetchone()
                if current:
                    old = json.loads(current["payload_json"])
                    old_sources = {source[0] for source in conn.execute(
                        "SELECT DISTINCT source_id FROM schedule_event_sources WHERE event_id=? "
                        "AND event_version=?", (event_id, current["event_version"])).fetchall()}
                    if (tuple(old.get("signature", ())) != candidate.trigger_signature()
                            and candidate.source_id not in old_sources):
                        review_status, reason = "conflict", "cross_source_trigger_fields_disagree"
            conn.execute("INSERT INTO schedule_event_candidates(candidate_id,event_id,source_id,"
                         "source_url,source_event_ref,payload_hash,payload_json,signature_json,"
                         "review_status,review_reason,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (candidate_id, event_id, candidate.source_id, candidate.source_url,
                          candidate.source_event_ref, candidate.payload_hash,
                          _json(candidate.normalized()), _json(candidate.trigger_signature()),
                          review_status, reason, fetched))
            row = conn.execute("SELECT * FROM schedule_event_candidates WHERE candidate_id=?",
                               (candidate_id,)).fetchone()
            return self._candidate_dict(row, duplicate=False)

    @staticmethod
    def _candidate_dict(row, *, duplicate: bool = False) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result["signature"] = json.loads(result.pop("signature_json"))
        result["duplicate"] = duplicate
        return result

    def publish_candidate(self, candidate_id: str, *, actor: str = "calendar-source",
                          reason: str = "automatic_non_conflicting_candidate",
                          allow_conflict: bool = False,
                          at: datetime | None = None) -> dict[str, Any]:
        now = _stamp(at)
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM schedule_event_candidates WHERE candidate_id=?",
                               (candidate_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown schedule candidate {candidate_id!r}")
            if row["review_status"] == "pending_identity":
                raise ValueError("candidate identity is insufficient and cannot be published")
            if row["review_status"] == "conflict" and not allow_conflict:
                raise ValueError("candidate has unresolved cross-source trigger conflict")
            candidate = json.loads(row["payload_json"])
            event_id = row["event_id"]
            current = conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND valid_until='' "
                                   "ORDER BY event_version DESC LIMIT 1", (event_id,)).fetchone()
            sig = json.loads(row["signature_json"])
            new_version = False
            if current:
                old_payload = json.loads(current["payload_json"])
                if tuple(sig) != tuple(old_payload.get("signature", ())):
                    conn.execute("UPDATE schedule_events SET valid_until=? WHERE event_id=? "
                                 "AND event_version=? AND valid_until=''",
                                 (now, event_id, current["event_version"]))
                    event_version, new_version = int(current["event_version"]) + 1, True
                else:
                    event_version = int(current["event_version"])
            else:
                event_version, new_version = 1, True
            if new_version:
                candidate["signature"] = list(sig)
                conn.execute("INSERT INTO schedule_events(event_id,event_version,event_type,"
                             "reference_period,label,event_date,local_time,timezone,utc_at,"
                             "time_precision,market_session,status,announced_at,valid_from,"
                             "payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (event_id, event_version, candidate["event_type"],
                              candidate.get("reference_period", ""), candidate.get("label", ""),
                              candidate.get("event_date") or "", candidate.get("local_time", ""),
                              candidate.get("timezone", ""), candidate.get("utc_at", ""),
                              candidate["time_precision"], candidate["market_session"],
                              candidate["status"], candidate.get("announced_at", ""), now,
                              _json(candidate)))
            conn.execute("INSERT OR IGNORE INTO schedule_event_sources(event_id,event_version,"
                         "candidate_id,source_id,source_url,linked_at) VALUES(?,?,?,?,?,?)",
                         (event_id, event_version, candidate_id, row["source_id"],
                          row["source_url"], now))
            conn.execute("UPDATE schedule_event_candidates SET review_status='admitted',"
                         "review_reason=? WHERE candidate_id=?",
                         ("operator_override" if allow_conflict else reason, candidate_id))
            if allow_conflict:
                conn.execute("UPDATE schedule_event_candidates SET review_status='admitted',"
                             "review_reason='resolved_by_operator' WHERE event_id=? AND "
                             "review_status='conflict'", (event_id,))
            event = conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND event_version=?",
                                 (event_id, event_version)).fetchone()
            return self._event_dict(conn, event)

    def _publish_override_snapshot(self, conn, event_id: str, payload: dict[str, Any],
                                   *, at: datetime) -> dict[str, Any]:
        now = _stamp(at)
        current = conn.execute("SELECT MAX(event_version) FROM schedule_events WHERE event_id=? "
                               "AND valid_until=''", (event_id,)).fetchone()[0]
        next_version = int(current or 0) + 1
        inherited_sources = []
        if current:
            inherited_sources = conn.execute(
                "SELECT candidate_id,source_id,source_url FROM schedule_event_sources "
                "WHERE event_id=? AND event_version=?", (event_id, current)).fetchall()
            conn.execute("UPDATE schedule_events SET valid_until=? WHERE event_id=? "
                         "AND event_version=? AND valid_until=''", (now, event_id, current))
        payload["event_id"] = event_id
        payload["signature"] = [str(payload.get(k, "")) for k in (
            "event_type", "event_date", "local_time", "timezone", "utc_at",
            "time_precision", "market_session", "status")]
        conn.execute("INSERT INTO schedule_events(event_id,event_version,event_type,"
                     "reference_period,label,event_date,local_time,timezone,utc_at,time_precision,"
                     "market_session,status,announced_at,valid_from,payload_json) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (event_id, next_version, payload["event_type"], payload.get("reference_period", ""),
                      payload.get("label", ""), payload.get("event_date", ""),
                      payload.get("local_time", ""), payload.get("timezone", ""),
                      payload.get("utc_at", ""), payload["time_precision"],
                      payload["market_session"], payload["status"],
                     payload.get("announced_at", ""), now, _json(payload)))
        for source in inherited_sources:
            conn.execute("INSERT OR IGNORE INTO schedule_event_sources(event_id,event_version,"
                         "candidate_id,source_id,source_url,linked_at) VALUES(?,?,?,?,?,?)",
                         (event_id, next_version, source["candidate_id"], source["source_id"],
                          source["source_url"], now))
        event = conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND event_version=?",
                             (event_id, next_version)).fetchone()
        return self._event_dict(conn, event)

    def confirm_release(self, event_id: str, *, expected_version: int | str,
                        admitted_materials: list[Mapping[str, Any]], source_id: str,
                        at: datetime | None = None) -> dict[str, Any]:
        """Append a released state only after at least one admitted material is linked."""
        refs = []
        for item in admitted_materials:
            ref = str(item.get("ref") or item.get("reference") or item.get("document_id") or "")
            kind = str(item.get("kind") or item.get("material_kind") or "")
            if item.get("status") == "admitted" and ref and kind:
                refs.append({"ref": ref, "kind": kind})
        if not refs:
            raise ValueError("release confirmation requires an admitted material reference")
        if not source_id.strip():
            raise ValueError("release confirmation requires a source_id")
        now = at or datetime.now(timezone.utc)
        with self._tx() as conn:
            current = conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND valid_until='' "
                                   "ORDER BY event_version DESC LIMIT 1", (event_id,)).fetchone()
            if current is None:
                raise KeyError(f"unknown calendar event {event_id!r}")
            if int(current["event_version"]) != int(expected_version):
                raise ValueError("calendar event version changed before release confirmation")
            if current["status"] == "released":
                prior_rows = conn.execute(
                    "SELECT materials_json FROM schedule_event_release_confirmations "
                    "WHERE event_id=? AND event_version=?", (event_id, expected_version)).fetchall()
                prior_refs = {item["ref"]: item for row in prior_rows
                              for item in json.loads(row["materials_json"])}
                merged = {**prior_refs, **{item["ref"]: item for item in refs}}
                if merged == prior_refs:
                    return self._event_dict(conn, current)
                refs = list(merged.values())
            elif current["status"] != "planned":
                raise ValueError(f"cannot confirm event in {current['status']!r} state")
            payload = json.loads(current["payload_json"])
            prior_metadata = dict(payload.get("metadata", {}))
            prior_metadata["admitted_release_materials"] = refs
            payload["metadata"] = prior_metadata
            payload["status"] = "released"
            event = self._publish_override_snapshot(conn, event_id, payload, at=now)
            conn.execute("INSERT INTO schedule_event_release_confirmations(event_id,event_version,"
                         "source_id,materials_json,confirmed_at) VALUES(?,?,?,?,?)",
                         (event_id, event["event_version"], source_id, _json(refs), _stamp(now)))
            row = conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND event_version=?",
                               (event_id, event["event_version"])).fetchone()
            return self._event_dict(conn, row)

    def override_event(self, event_id: str, *, patch: Mapping[str, Any], actor: str,
                       reason: str, evidence: Any, at: datetime | None = None,
                       source_candidate_id: str = "") -> dict[str, Any]:
        if not actor.strip() or not reason.strip():
            raise ValueError("manual overrides require actor and reason")
        now = at or datetime.now(timezone.utc)
        with self._tx() as conn:
            current = conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND valid_until='' "
                                   "ORDER BY event_version DESC LIMIT 1", (event_id,)).fetchone()
            if current is None:
                raise KeyError(f"unknown calendar event {event_id!r}")
            original = json.loads(current["payload_json"])
            updated = {**original, **dict(patch)}
            updated.pop("signature", None)
            updated.pop("event_id", None)
            for key in ("candidate_id",):
                updated.pop(key, None)
            if (set(patch) & {"event_date", "local_time", "timezone"}) and "utc_at" not in patch:
                updated["utc_at"] = ""
            checked = ScheduleEventCandidate.model_validate({
                **{k: v for k, v in updated.items() if k not in {"event_id", "signature"}},
                "source_id": "manual-override", "source_event_ref": event_id,
                "fetched_at": _stamp(now)})
            event = self._publish_override_snapshot(conn, event_id, checked.normalized(), at=now)
            if source_candidate_id:
                source = conn.execute("SELECT source_id,source_url FROM schedule_event_candidates "
                                      "WHERE candidate_id=? AND event_id=?",
                                      (source_candidate_id, event_id)).fetchone()
                if source is None:
                    raise KeyError(f"unknown overlay candidate {source_candidate_id!r}")
                conn.execute("UPDATE schedule_event_candidates SET review_status='admitted',"
                             "review_reason='operator_override' WHERE candidate_id=?",
                             (source_candidate_id,))
                conn.execute("UPDATE schedule_event_candidates SET review_status='admitted',"
                             "review_reason='resolved_by_operator_override' WHERE event_id=? "
                             "AND review_status='conflict'", (event_id,))
                conn.execute("INSERT INTO schedule_event_sources(event_id,event_version,"
                             "candidate_id,source_id,source_url,linked_at) VALUES(?,?,?,?,?,?)",
                             (event_id, event["event_version"], source_candidate_id,
                              source["source_id"], source["source_url"], _stamp(now)))
            # An explicit, reasoned override is an adjudication of conflicts for
            # this event; retain candidate rows but mark their review outcome.
            conn.execute("UPDATE schedule_event_candidates SET review_status='admitted',"
                         "review_reason='resolved_by_operator_override' WHERE event_id=? "
                         "AND review_status='conflict'", (event_id,))
            override_id = hashlib.sha256(
                f"{event_id}|{event['event_version']}|{actor}".encode()).hexdigest()[:32]
            conn.execute("INSERT INTO schedule_event_overrides(override_id,event_id,actor,reason,"
                         "evidence_json,action,base_snapshot_json,event_version,created_at) "
                         "VALUES(?,?,?,?,?,'apply',?,?,?)",
                         (override_id, event_id, actor, reason, _json(evidence), _json(original),
                          event["event_version"], _stamp(now)))
            row = conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND event_version=?",
                               (event_id, event["event_version"])).fetchone()
            return self._event_dict(conn, row)

    def withdraw_override(self, event_id: str, *, actor: str, reason: str,
                          evidence: Any, at: datetime | None = None) -> dict[str, Any]:
        if not actor.strip() or not reason.strip():
            raise ValueError("override withdrawal requires actor and reason")
        now = at or datetime.now(timezone.utc)
        with self._tx() as conn:
            override = conn.execute("SELECT * FROM schedule_event_overrides WHERE event_id=? "
                                    "AND active=1 AND action='apply' ORDER BY created_at DESC LIMIT 1",
                                    (event_id,)).fetchone()
            if override is None:
                raise KeyError(f"no active override exists for {event_id!r}")
            conn.execute("UPDATE schedule_event_overrides SET active=0 WHERE override_id=?",
                         (override["override_id"],))
            base = json.loads(override["base_snapshot_json"])
            event = self._publish_override_snapshot(conn, event_id, base, at=now)
            conn.execute("UPDATE schedule_event_candidates SET review_status='conflict',"
                         "review_reason='operator_adjudication_withdrawn' WHERE event_id=? "
                         "AND review_reason='resolved_by_operator_override'", (event_id,))
            override_id = hashlib.sha256(
                f"{event_id}|withdraw|{event['event_version']}|{actor}".encode()).hexdigest()[:32]
            conn.execute("INSERT INTO schedule_event_overrides(override_id,event_id,actor,reason,"
                         "evidence_json,action,base_snapshot_json,event_version,active,created_at) "
                         "VALUES(?,?,?,?,?,'withdraw',?,?,0,?)",
                         (override_id, event_id, actor, reason, _json(evidence),
                          _json(base), event["event_version"], _stamp(now)))
            row = conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND event_version=?",
                               (event_id, event["event_version"])).fetchone()
            return self._event_dict(conn, row)

    @staticmethod
    def _event_dict(conn, row) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result["sources"] = [dict(source) for source in conn.execute(
            "SELECT s.source_id,s.source_url,s.candidate_id,c.fetched_at FROM schedule_event_sources s "
            "JOIN schedule_event_candidates c ON c.candidate_id=s.candidate_id "
            "WHERE s.event_id=? AND s.event_version=? ORDER BY s.linked_at",
            (result["event_id"], result["event_version"]))]
        result["overrides"] = [dict(item) for item in conn.execute(
            "SELECT actor,reason,evidence_json,action,active,created_at FROM schedule_event_overrides "
            "WHERE event_id=? AND event_version=? ORDER BY created_at",
            (result["event_id"], result["event_version"]))]
        for item in result["overrides"]:
            item["evidence"] = json.loads(item.pop("evidence_json"))
        result["release_confirmations"] = [dict(item) for item in conn.execute(
            "SELECT source_id,materials_json,confirmed_at FROM schedule_event_release_confirmations "
            "WHERE event_id=? AND event_version=?", (result["event_id"], result["event_version"]))]
        for item in result["release_confirmations"]:
            item["materials"] = json.loads(item.pop("materials_json"))
        result["quality_status"] = "conflict" if conn.execute(
            "SELECT 1 FROM schedule_event_candidates WHERE event_id=? AND review_status='conflict' "
            "LIMIT 1", (result["event_id"],)).fetchone() else "ok"
        return result

    def events(self, *, as_of: datetime | None = None, start: date | None = None,
               end: date | None = None, event_types: tuple[str, ...] = (),
               include_cancelled: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        point = _stamp(as_of)
        where = ["valid_from<=?", "(valid_until='' OR valid_until>?)"]
        params: list[Any] = [point, point]
        if start:
            where.append("event_date>=?")
            params.append(start.isoformat())
        if end:
            where.append("event_date<=?")
            params.append(end.isoformat())
        if event_types:
            where.append("event_type IN (" + ",".join("?" for _ in event_types) + ")")
            params.extend(event_types)
        if not include_cancelled:
            where.append("status!='cancelled'")
        params.append(max(1, int(limit)))
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM schedule_events WHERE " + " AND ".join(where)
                                + " ORDER BY event_date,event_type,event_id LIMIT ?", params).fetchall()
            return [self._event_dict(conn, row) for row in rows]
        finally:
            conn.close()

    def latest_events(self, *, limit: int = 500, include_cancelled: bool = False):
        conn = self._connect()
        try:
            where = "" if include_cancelled else "AND e.status!='cancelled'"
            rows = conn.execute(
                "SELECT e.* FROM schedule_events e JOIN (SELECT event_id,MAX(event_version) v "
                "FROM schedule_events WHERE valid_until='' GROUP BY event_id) x "
                "ON x.event_id=e.event_id AND x.v=e.event_version WHERE 1=1 " + where +
                " ORDER BY e.event_date,e.event_type,e.event_id LIMIT ?", (limit,)).fetchall()
            return [self._event_dict(conn, row) for row in rows]
        finally:
            conn.close()

    def candidates(self, *, event_id: str = "", review_status: str = "",
                   limit: int = 200) -> list[dict[str, Any]]:
        where, params = [], []
        if event_id:
            where.append("event_id=?")
            params.append(event_id)
        if review_status:
            where.append("review_status=?")
            params.append(review_status)
        sql = "SELECT * FROM schedule_event_candidates"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY fetched_at DESC LIMIT ?"
        params.append(limit)
        conn = self._connect()
        try:
            return [self._candidate_dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def source_runs(self, *, source_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if source_id:
                rows = conn.execute("SELECT * FROM schedule_calendar_source_runs WHERE source_id=? "
                                    "ORDER BY started_at DESC LIMIT ?", (source_id, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM schedule_calendar_source_runs "
                                    "ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


__all__ = ["MarketSession", "ScheduleCalendarStore", "ScheduleEventCandidate", "TimePrecision",
           "earnings_identity", "fomc_identity", "macro_identity"]
