"""Versioned persistence for schedule-calendar candidates and event facts.

This database stores publication timing and provenance only; it is not a financial
results store and it never overwrites a previously visible event version.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence, Any, Literal, Mapping
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


# A source can give a print's date without saying which fiscal quarter it reports (a newly listed issuer). Such a
# date is published under one placeholder per issuer: visible in the calendar and usable for filing discovery, but
# never confirmed as released and never routed to an analysis until a fiscal label exists.
NEARBY_PRINT_DAYS = 45      # half a quarter: closer than this, a date without a label belongs to a labelled print


def unlabeled_earnings_identity(entity: str) -> str:
    return f"earnings:{entity.upper()}:unlabeled:release"


def fiscal_label_status(payload: Mapping[str, Any] | None) -> str:
    """`missing` (placeholder), `manual` (an operator supplied it) or empty (the source stated it)."""
    return str((((payload or {}).get("metadata") or {}).get("fiscal_label_status")) or "")


def _earnings_entity(payload: Mapping[str, Any], event_id: str) -> str:
    entity = str((payload.get("metadata") or {}).get("entity") or "")
    parts = event_id.split(":")
    if not entity and len(parts) > 2 and parts[0] == "earnings":
        entity = parts[1]
    return entity.upper()


def _days_apart(first: object, second: object) -> int | None:
    try:
        return abs((date.fromisoformat(str(first)[:10]) - date.fromisoformat(str(second)[:10])).days)
    except ValueError:
        return None


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
CREATE TABLE IF NOT EXISTS schedule_candidate_review_history (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL,
    review_status TEXT NOT NULL, review_reason TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_schedule_review_history
    ON schedule_candidate_review_history(candidate_id,recorded_at,review_id);
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
CREATE TABLE IF NOT EXISTS schedule_event_candidate_observations (
    source_run_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
    raw_artifact_id TEXT NOT NULL DEFAULT '', observed_at TEXT NOT NULL,
    PRIMARY KEY(source_run_id,candidate_id),
    FOREIGN KEY(source_run_id) REFERENCES schedule_calendar_source_runs(source_run_id),
    FOREIGN KEY(candidate_id) REFERENCES schedule_event_candidates(candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_schedule_candidate_observations_candidate
    ON schedule_event_candidate_observations(candidate_id,observed_at);
"""


def _stamp(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="microseconds")


# Trigger signature field order (ScheduleEventCandidate.trigger_signature): type, date, local time, timezone,
# UTC instant, time precision, market session, status.
_SIGNATURE_PRECISION = 5


def _populated(value: object) -> bool:
    return str(value) not in {"", "unknown", "None"}


def signatures_conflict(old: Sequence[object], new: Sequence[object]) -> bool:
    """Two sources disagree only when a field both of them state differs.

    An empty field means "not stated", not "different". Finnhub publishes the date and session of a print;
    Yahoo adds the clock time. They agree on everything they both state, so they do not conflict (2026-10-19:
    every earnings event was blocked as `conflict` for exactly this reason). `time_precision` is derived from
    the time fields and is not compared on its own.
    """
    for index, (a, b) in enumerate(zip(old, new)):
        if index != _SIGNATURE_PRECISION and _populated(a) and _populated(b) and str(a) != str(b):
            return True
    return False


def signature_refines(old: Sequence[object], new: Sequence[object]) -> bool:
    """`new` agrees with `old` and states at least one field `old` leaves empty."""
    return (not signatures_conflict(old, new)) and any(
        index != _SIGNATURE_PRECISION and not _populated(a) and _populated(b)
        for index, (a, b) in enumerate(zip(old, new)))


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

    def update_source_run_provenance(self, source_run_id: str,
                                    provenance: Mapping[str, Any]) -> None:
        """Append fetch lineage to a running calendar source run."""
        with self._tx() as conn:
            row = conn.execute("SELECT provenance_json,status FROM schedule_calendar_source_runs "
                               "WHERE source_run_id=?", (source_run_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown calendar source run {source_run_id!r}")
            if row["status"] != "running":
                raise ValueError(f"calendar source run {source_run_id!r} is not running")
            current = json.loads(row["provenance_json"] or "{}")
            current.update(dict(provenance))
            conn.execute("UPDATE schedule_calendar_source_runs SET provenance_json=? "
                         "WHERE source_run_id=?", (_json(current), source_run_id))

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
                    if (signatures_conflict(tuple(old.get("signature", ())), candidate.trigger_signature())
                            and candidate.source_id not in old_sources
                            and not (old.get("metadata") or {}).get("superseded_by")):
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
            self._record_reviews(conn, event_id, fetched, candidate_id=candidate_id)
            return self._candidate_dict(row, duplicate=False)

    def rereview_compatible_conflicts(self, *, at: datetime | None = None) -> list[str]:
        """Release candidates that were parked as conflicts but only add detail to the current version.

        Returns the published `event_id@version` references. Candidates that truly disagree stay parked for an
        operator (`ats events override`).
        """
        now = _stamp(at)
        released: list[tuple[str, str]] = []
        with self._tx() as conn:
            rows = conn.execute("SELECT candidate_id,event_id,signature_json FROM schedule_event_candidates "
                                "WHERE review_status='conflict' AND review_reason="
                                "'cross_source_trigger_fields_disagree'").fetchall()
            for row in rows:
                current = conn.execute("SELECT payload_json FROM schedule_events WHERE event_id=? "
                                       "AND valid_until='' ORDER BY event_version DESC LIMIT 1",
                                       (row["event_id"],)).fetchone()
                if current is None:
                    continue
                old_sig = tuple(json.loads(current["payload_json"]).get("signature", ()))
                if signatures_conflict(old_sig, json.loads(row["signature_json"])):
                    continue
                conn.execute("UPDATE schedule_event_candidates SET review_status='candidate',"
                             "review_reason='compatible_refinement' WHERE candidate_id=?",
                             (row["candidate_id"],))
                self._record_reviews(conn, row["event_id"], now, candidate_id=row["candidate_id"])
                released.append((row["candidate_id"], row["event_id"]))
        refs = []
        for candidate_id, _event_id in released:
            event = self.publish_candidate(candidate_id, reason="compatible_refinement", at=at)
            refs.append(f"{event['event_id']}@{event['event_version']}")
        return refs

    @staticmethod
    def _record_reviews(conn, event_id: str, at: str, *, candidate_id: str = "") -> None:
        sql = "SELECT * FROM schedule_event_candidates WHERE "
        sql += "candidate_id=?" if candidate_id else "event_id=?"
        for row in conn.execute(sql, (candidate_id or event_id,)).fetchall():
            previous = conn.execute(
                "SELECT review_status,review_reason FROM schedule_candidate_review_history "
                "WHERE candidate_id=? ORDER BY review_id DESC LIMIT 1", (row["candidate_id"],)).fetchone()
            state = (row["review_status"], row["review_reason"])
            if previous is not None and tuple(previous) == state:
                continue
            conn.execute("INSERT INTO schedule_candidate_review_history "
                         "(candidate_id,review_status,review_reason,recorded_at) VALUES (?,?,?,?)",
                         (row["candidate_id"], *state, at))

    def record_candidate_observation(self, *, source_run_id: str, candidate_id: str,
                                    raw_artifact_id: str = "",
                                    observed_at: datetime | None = None) -> None:
        with self._tx() as conn:
            conn.execute("INSERT OR IGNORE INTO schedule_event_candidate_observations "
                         "(source_run_id,candidate_id,raw_artifact_id,observed_at) "
                         "VALUES(?,?,?,?)", (source_run_id, candidate_id, raw_artifact_id,
                                             _stamp(observed_at)))

    def candidate_observations(self, *, candidate_id: str = "",
                               source_run_id: str = "") -> list[dict[str, Any]]:
        clauses, values = [], []
        if candidate_id:
            clauses.append("candidate_id=?")
            values.append(candidate_id)
        if source_run_id:
            clauses.append("source_run_id=?")
            values.append(source_run_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM schedule_event_candidate_observations" + where +
                                " ORDER BY observed_at", values).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _candidate_dict(row, *, duplicate: bool = False) -> dict[str, Any]:
        result = dict(row)
        if "asof_review_status" in result:
            result["review_status"] = result.pop("asof_review_status")
            result["review_reason"] = result.pop("asof_review_reason")
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
                old_sig = tuple(old_payload.get("signature", ()))
                # A candidate that only agrees with the current version (or states less than it) corroborates it;
                # only a disagreement or a strictly more precise statement is a new version.
                if tuple(sig) != old_sig and (signatures_conflict(old_sig, sig)
                                              or signature_refines(old_sig, sig)):
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
            self._record_reviews(conn, event_id, now)
            if candidate["event_type"] == "earnings" and candidate["status"] != "cancelled":
                self._retire_placeholder_near(conn, event_id, candidate, at=at or datetime.now(timezone.utc))
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
            if fiscal_label_status(payload) == "missing":
                raise ValueError("release confirmation requires a fiscal label; add one with `ats events label`")
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
            self._record_reviews(conn, event_id, _stamp(now))
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
            self._record_reviews(conn, event_id, _stamp(now))
            if restores := (base.get("metadata") or {}).get("withdraw_restores"):
                self._restore_placeholder(conn, restores, successor=event_id, at=now)
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

    # -- earnings prints without a fiscal label ------------------------------
    def place_unlabeled_earnings(self, candidate: ScheduleEventCandidate) -> ScheduleEventCandidate:
        """Give a dated earnings candidate without a fiscal quarter the identity it can be published under.

        Near a print an operator labelled, it revises that print (the label stays). Near a print a source labelled,
        it is left without identity (a stray second date, parked as before). Otherwise it becomes the issuer's
        placeholder.
        """
        entity = str(candidate.metadata.get("entity") or "").upper()
        if candidate.event_type != "earnings" or candidate.event_id or not entity or not candidate.event_date:
            return candidate
        placeholder = unlabeled_earnings_identity(entity)
        near = []
        for event in self.latest_events(limit=10_000):
            if (event["event_type"] != "earnings" or event["event_id"] == placeholder
                    or _earnings_entity(event["payload"], event["event_id"]) != entity):
                continue
            apart = _days_apart(event["event_date"], candidate.event_date)
            if apart is not None and apart <= NEARBY_PRINT_DAYS:
                near.append((apart, event))
        if not near:
            return candidate.model_copy(update={
                "stable_identity": placeholder, "label": f"{entity} earnings（缺财季标签）",
                "metadata": {**candidate.metadata, "fiscal_label": "", "fiscal_label_status": "missing"}})
        manual = [item for item in near if fiscal_label_status(item[1]["payload"]) == "manual"]
        if not manual:
            return candidate
        event = min(manual, key=lambda item: item[0])[1]
        period = event["reference_period"]
        return candidate.model_copy(update={
            "stable_identity": event["event_id"], "label": event["label"], "reference_period": period,
            "metadata": {**candidate.metadata, "fiscal_label": period, "fiscal_label_status": "manual"}})

    @staticmethod
    def _current(conn, event_id: str):
        return conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND valid_until='' "
                            "ORDER BY event_version DESC LIMIT 1", (event_id,)).fetchone()

    def _retire_placeholder(self, conn, placeholder: str, *, successor: str, at: datetime) -> None:
        current = self._current(conn, placeholder)
        if current is None or current["status"] != "planned":
            return
        payload = json.loads(current["payload_json"])
        payload["status"] = "cancelled"
        payload["metadata"] = {**payload.get("metadata", {}), "superseded_by": successor}
        self._publish_override_snapshot(conn, placeholder, payload, at=at)

    def _retire_placeholder_near(self, conn, event_id: str, payload: Mapping[str, Any], *,
                                 at: datetime) -> None:
        """A labelled print makes the placeholder for the same print redundant."""
        entity = _earnings_entity(payload, event_id)
        placeholder = unlabeled_earnings_identity(entity)
        if not entity or event_id == placeholder:
            return
        current = self._current(conn, placeholder)
        if current is None:
            return
        apart = _days_apart(current["event_date"], payload.get("event_date"))
        if apart is not None and apart <= NEARBY_PRINT_DAYS:
            self._retire_placeholder(conn, placeholder, successor=event_id, at=at)

    def _restore_placeholder(self, conn, placeholder: str, *, successor: str, at: datetime) -> None:
        current = self._current(conn, placeholder)
        if current is None:
            return
        payload = json.loads(current["payload_json"])
        if (payload.get("metadata") or {}).get("superseded_by") != successor:
            return
        payload["status"] = "planned"
        payload["metadata"] = {k: v for k, v in payload["metadata"].items() if k != "superseded_by"}
        self._publish_override_snapshot(conn, placeholder, payload, at=at)

    def _publish_manual_earnings(self, conn, payload: Mapping[str, Any], *, actor: str, reason: str,
                                 evidence: Any, restores: str, at: datetime) -> None:
        """Publish an operator-labelled print and its override record; withdrawing the record cancels it."""
        checked = ScheduleEventCandidate.model_validate({
            **{k: v for k, v in payload.items() if k not in {"event_id", "signature"}},
            "source_id": "manual-override", "fetched_at": _stamp(at)}).normalized()
        event_id = checked["event_id"]
        event = self._publish_override_snapshot(conn, event_id, dict(checked), at=at)
        withdrawn = {**checked, "status": "cancelled",
                     "metadata": {**checked["metadata"], "withdraw_restores": restores}}
        override_id = hashlib.sha256(
            f"{event_id}|{event['event_version']}|{actor}".encode()).hexdigest()[:32]
        conn.execute("INSERT INTO schedule_event_overrides(override_id,event_id,actor,reason,"
                     "evidence_json,action,base_snapshot_json,event_version,created_at) "
                     "VALUES(?,?,?,?,?,'apply',?,?,?)",
                     (override_id, event_id, actor, reason, _json(evidence), _json(withdrawn),
                      event["event_version"], _stamp(at)))

    @staticmethod
    def _require_adjudication(actor: str, reason: str, evidence: Any) -> None:
        if not actor.strip() or not reason.strip() or not evidence:
            raise ValueError("a manual fiscal label requires actor, reason and evidence")

    def label_earnings_event(self, event_id: str, *, fiscal_year: int, fiscal_quarter: int, actor: str,
                             reason: str, evidence: Any, at: datetime | None = None) -> dict[str, Any]:
        """Supply the fiscal label of a placeholder print: it moves to the labelled event identity."""
        self._require_adjudication(actor, reason, evidence)
        now = at or datetime.now(timezone.utc)
        with self._tx() as conn:
            current = self._current(conn, event_id)
            if current is None:
                raise KeyError(f"unknown calendar event {event_id!r}")
            old = json.loads(current["payload_json"])
            if fiscal_label_status(old) != "missing" or current["status"] != "planned":
                raise ValueError("only a planned event that lacks a fiscal label can be labelled")
            if conn.execute("SELECT 1 FROM schedule_event_candidates WHERE event_id=? "
                            "AND review_status='conflict'", (event_id,)).fetchone():
                raise ValueError("sources disagree on this date; settle it with `ats events override` first")
            entity = _earnings_entity(old, event_id)
            target = earnings_identity(entity, fiscal_year, fiscal_quarter)
            if target is None:
                raise ValueError("fiscal year and quarter (1-4) are required")
            existing = self._current(conn, target)
            if existing is not None and existing["status"] != "cancelled":
                raise ValueError(f"{target} already exists; correct that event with `ats events override`")
            period = f"FY{int(fiscal_year)}Q{int(fiscal_quarter)}"
            payload = {**old, "stable_identity": target, "source_event_ref": event_id,
                       "label": f"{entity} earnings {int(fiscal_year)} Q{int(fiscal_quarter)}",
                       "reference_period": period,
                       "metadata": {**old.get("metadata", {}), "entity": entity, "fiscal_label": period,
                                    "fiscal_label_status": "manual",
                                    "labeled_from": f"{event_id}@{current['event_version']}"}}
            sources = conn.execute("SELECT candidate_id,source_id,source_url FROM schedule_event_sources "
                                   "WHERE event_id=? AND event_version=?",
                                   (event_id, current["event_version"])).fetchall()
            self._publish_manual_earnings(conn, payload, actor=actor, reason=reason, evidence=evidence,
                                          restores=event_id, at=now)
            event = self._current(conn, target)
            # The sources that stated the date stay attached, so their later revisions are not cross-source conflicts.
            for source in sources:
                conn.execute("INSERT OR IGNORE INTO schedule_event_sources(event_id,event_version,"
                             "candidate_id,source_id,source_url,linked_at) VALUES(?,?,?,?,?,?)",
                             (target, event["event_version"], source["candidate_id"], source["source_id"],
                              source["source_url"], _stamp(now)))
            self._retire_placeholder(conn, event_id, successor=target, at=now)
            return self._event_dict(conn, event)

    def register_earnings_event(self, entity: str, *, fiscal_year: int, fiscal_quarter: int,
                                event_date: str, actor: str, reason: str, evidence: Any,
                                at: datetime | None = None) -> dict[str, Any]:
        """Enter a print no source lists yet, from the operator's evidence (e.g. the issuer's announcement)."""
        self._require_adjudication(actor, reason, evidence)
        entity = entity.strip().upper()
        target = earnings_identity(entity, fiscal_year, fiscal_quarter)
        if target is None:
            raise ValueError("entity, fiscal year and quarter (1-4) are required")
        now = at or datetime.now(timezone.utc)
        period = f"FY{int(fiscal_year)}Q{int(fiscal_quarter)}"
        with self._tx() as conn:
            existing = self._current(conn, target)
            if existing is not None and existing["status"] != "cancelled":
                raise ValueError(f"{target} already exists; correct that event with `ats events override`")
            placeholder = self._current(conn, unlabeled_earnings_identity(entity))
            if placeholder is not None and placeholder["status"] == "planned":
                raise ValueError(f"{placeholder['event_id']} is waiting for a label; use `ats events label`")
            payload = {"event_type": "earnings", "stable_identity": target, "source_event_ref": target,
                       "label": f"{entity} earnings {int(fiscal_year)} Q{int(fiscal_quarter)}",
                       "reference_period": period, "event_date": event_date, "time_precision": "date",
                       "status": "planned",
                       "metadata": {"entity": entity, "fiscal_label": period, "fiscal_label_status": "manual"}}
            self._publish_manual_earnings(conn, payload, actor=actor, reason=reason, evidence=evidence,
                                          restores="", at=now)
            return self._event_dict(conn, self._current(conn, target))

    @staticmethod
    def _event_dict(conn, row, *, as_of: datetime | None = None) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result["sources"] = [dict(source) for source in conn.execute(
            "SELECT s.source_id,s.source_url,s.candidate_id,c.fetched_at FROM schedule_event_sources s "
            "JOIN schedule_event_candidates c ON c.candidate_id=s.candidate_id "
            "WHERE s.event_id=? AND s.event_version=? AND s.linked_at<=? AND c.fetched_at<=? "
            "ORDER BY s.linked_at",
            (result["event_id"], result["event_version"], _stamp(as_of), _stamp(as_of)))]
        result["overrides"] = [dict(item) for item in conn.execute(
            "SELECT override_id,actor,reason,evidence_json,action,active,created_at FROM schedule_event_overrides "
            "WHERE event_id=? AND event_version=? AND created_at<=? ORDER BY created_at",
            (result["event_id"], result["event_version"], _stamp(as_of) if as_of else "9999"))]
        latest_override = conn.execute(
            "SELECT override_id,action FROM schedule_event_overrides WHERE event_id=? AND created_at<=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (result["event_id"], _stamp(as_of) if as_of else "9999")).fetchone()
        for item in result["overrides"]:
            item["evidence"] = json.loads(item.pop("evidence_json"))
            if as_of is not None:
                item["active"] = int(bool(latest_override and latest_override["action"] == "apply"
                                          and latest_override["override_id"] == item["override_id"]))
        result["release_confirmations"] = [dict(item) for item in conn.execute(
            "SELECT source_id,materials_json,confirmed_at FROM schedule_event_release_confirmations "
            "WHERE event_id=? AND event_version=? AND confirmed_at<=?",
            (result["event_id"], result["event_version"], _stamp(as_of) if as_of else "9999"))]
        for item in result["release_confirmations"]:
            item["materials"] = json.loads(item.pop("materials_json"))
        if as_of is None:
            reviews = conn.execute("SELECT review_status FROM schedule_event_candidates WHERE event_id=?",
                                   (result["event_id"],)).fetchall()
        else:
            reviews = conn.execute(
                "SELECT COALESCE((SELECT h.review_status FROM schedule_candidate_review_history h "
                "WHERE h.candidate_id=c.candidate_id AND h.recorded_at<=? "
                "ORDER BY h.recorded_at DESC,h.review_id DESC LIMIT 1),'history_unavailable') review_status "
                "FROM schedule_event_candidates c WHERE c.event_id=? AND c.fetched_at<=?",
                (_stamp(as_of), result["event_id"], _stamp(as_of))).fetchall()
        statuses = {row["review_status"] for row in reviews}
        result["quality_status"] = ("conflict" if "conflict" in statuses else
                                    "unknown" if "history_unavailable" in statuses else "ok")
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
            return [self._event_dict(conn, row, as_of=as_of) for row in rows]
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
                   as_of: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where, params = [], []
        if as_of is not None:
            where.append("fetched_at<=?")
            params.append(_stamp(as_of))
        if event_id:
            where.append("event_id=?")
            params.append(event_id)
        if review_status:
            where.append("asof_review_status=?" if as_of is not None else "review_status=?")
            params.append(review_status)
        sql = "SELECT * FROM schedule_event_candidates"
        if as_of is not None:
            sql = ("SELECT c.*,COALESCE((SELECT h.review_status FROM schedule_candidate_review_history h "
                   "WHERE h.candidate_id=c.candidate_id AND h.recorded_at<=? "
                   "ORDER BY h.recorded_at DESC,h.review_id DESC LIMIT 1),'history_unavailable') asof_review_status,"
                   "COALESCE((SELECT h.review_reason FROM schedule_candidate_review_history h "
                   "WHERE h.candidate_id=c.candidate_id AND h.recorded_at<=? "
                   "ORDER BY h.recorded_at DESC,h.review_id DESC LIMIT 1),'') asof_review_reason "
                   "FROM schedule_event_candidates c")
            params = [_stamp(as_of), _stamp(as_of), *params]
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY fetched_at DESC LIMIT ?"
        params.append(limit)
        conn = self._connect()
        try:
            return [self._candidate_dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def source_runs(self, *, source_id: str = "", as_of: datetime | None = None,
                    limit: int = 100) -> list[dict[str, Any]]:
        where, params = [], []
        if source_id:
            where.append("source_id=?")
            params.append(source_id)
        if as_of is not None:
            where.append("started_at<=? AND finished_at!='' AND finished_at<=?")
            params.extend([_stamp(as_of), _stamp(as_of)])
        sql = "SELECT * FROM schedule_calendar_source_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


__all__ = ["NEARBY_PRINT_DAYS", "MarketSession", "ScheduleCalendarStore", "ScheduleEventCandidate", "TimePrecision",
           "earnings_identity", "fiscal_label_status", "fomc_identity", "macro_identity",
           "unlabeled_earnings_identity"]
