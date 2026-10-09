"""Actual scheduler claims and publication fencing, shared by resident and CLI work.

YAML describes wake-ups, not a live ownership change. Only this ledger grants
execution. SQLite write arbitration spans the actual publication, so generation
changes cannot race a publisher. No source/queue lifecycle operation lives here.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

import yaml

from ..config import REPO_ROOT


class ScheduleAuthorityError(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_scope_owners (
 workflow TEXT NOT NULL, scope TEXT NOT NULL, owner TEXT NOT NULL,
 generation INTEGER NOT NULL, frozen INTEGER NOT NULL DEFAULT 0,
 token TEXT NOT NULL DEFAULT '', PRIMARY KEY(workflow,scope));
CREATE TABLE IF NOT EXISTS schedule_runtime_claims (
 key TEXT PRIMARY KEY, workflow TEXT NOT NULL, scope TEXT NOT NULL,
 identity TEXT NOT NULL, owner TEXT NOT NULL, generation INTEGER NOT NULL,
 status TEXT NOT NULL, result_refs TEXT NOT NULL DEFAULT '[]',
 result_json TEXT NOT NULL DEFAULT 'null', actor TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedule_runtime_history (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, key TEXT NOT NULL,
 payload TEXT NOT NULL, at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS schedule_runtime_no_update BEFORE UPDATE ON schedule_runtime_history
BEGIN SELECT RAISE(ABORT,'schedule history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS schedule_runtime_no_delete BEFORE DELETE ON schedule_runtime_history
BEGIN SELECT RAISE(ABORT,'schedule history is append-only'); END;
CREATE TABLE IF NOT EXISTS schedule_runtime_manifest (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), config_hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS schedule_runtime_claim_no_delete BEFORE DELETE ON schedule_runtime_claims
BEGIN SELECT RAISE(ABORT,'schedule claim history cannot be erased'); END;
CREATE TRIGGER IF NOT EXISTS schedule_runtime_identity_no_update BEFORE UPDATE OF key,workflow,scope,identity ON schedule_runtime_claims
BEGIN SELECT RAISE(ABORT,'schedule logical identity is immutable'); END;
"""
_ACTIVE = ContextVar("schedule_runtime_claim", default=None)
_WAKE = ContextVar("schedule_runtime_wake", default=None)
_LOGICAL = ContextVar("schedule_runtime_logical", default=None)
_PUBLISHING = ContextVar("schedule_runtime_publication", default=False)
_FINALIZING = ContextVar("schedule_runtime_finalization", default=False)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=lambda item: item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item))


def state_path():
    return Path(os.environ.get("ATS_DISPATCH_STATE_PATH", REPO_ROOT / "var/phase_f_dispatch.sqlite"))


def _connect(*, write=False):
    path = state_path().resolve()
    if not path.is_file():
        raise ScheduleAuthorityError("schedule authority missing; explicitly initialize before startup")
    conn = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True,
                           timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _history(conn, action, key, payload):
    conn.execute("INSERT INTO schedule_runtime_history(action,key,payload,at) VALUES(?,?,?,?)",
                 (action, key, canonical(payload), datetime.now(UTC).isoformat()))


def initialize(*, config_dir=None):
    """Explicit first installation. Existing state is never reset by YAML reload."""
    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    path = root / "workflow/workflow_owners.yaml"
    config = yaml.safe_load(path.read_text())
    target = state_path()
    if target.is_file():
        with closing(sqlite3.connect(target)) as existing:
            if existing.execute("SELECT 1 FROM sqlite_master WHERE name='schedule_runtime_manifest'").fetchone():
                startup(config_dir=config_dir)
                return
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target, isolation_level=None)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM schedule_runtime_manifest").fetchone() is None:
                for workflow, entry in config["workflows"].items():
                    if entry["mode"] not in {"legacy", "dispatcher", "shadow"}:
                        raise ScheduleAuthorityError("invalid initial owner")
                    conn.execute("INSERT INTO schedule_scope_owners VALUES(?,?,?,1,0,'')",
                                 (workflow, "*", entry["mode"]))
                conn.execute("INSERT INTO schedule_runtime_manifest VALUES(1,?)",
                             (hashlib.sha256(path.read_bytes()).hexdigest(),))
                _history(conn, "initialize", "", {"config": str(path), "source_lifecycle": "unchanged"})
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def startup(*, config_dir=None):
    """Read-only live-state check. An isolated candidate explicitly gets its own ledger."""
    try:
        with closing(_connect()) as conn:
            if conn.execute("SELECT 1 FROM schedule_runtime_manifest").fetchone() is None:
                raise ScheduleAuthorityError("schedule authority not initialized")
            rows = conn.execute("SELECT * FROM schedule_scope_owners").fetchall()
            conn.execute("SELECT key FROM schedule_runtime_claims LIMIT 1")
            conn.execute("SELECT seq FROM schedule_runtime_history LIMIT 1")
            if not rows or any(row["owner"] not in {"legacy", "dispatcher", "shadow"}
                               or row["generation"] < 1 for row in rows):
                raise ScheduleAuthorityError("invalid schedule owner state")
    except sqlite3.Error as exc:
        raise ScheduleAuthorityError("schedule authority unreadable") from exc


def identity(workflow, scope, trigger):
    """Scope + planned instant/event version; manual replay may name the same tick."""
    body = trigger.model_dump() if hasattr(trigger, "model_dump") else dict(trigger)
    if body.get("event_id") or body.get("event_version"):
        if not body.get("event_id") or not body.get("event_version"):
            raise ScheduleAuthorityError("event id/version required")
        logical = {"event_id": body["event_id"], "event_version": str(body["event_version"])}
    elif body.get("scheduled_for"):
        point = datetime.fromisoformat(body["scheduled_for"])
        if point.tzinfo is None:
            raise ScheduleAuthorityError("planned instant requires timezone")
        logical = {"scheduled_for": point.astimezone(UTC).isoformat()}
    elif body.get("trigger_id"):
        logical = {"trigger_id": body["trigger_id"]}
    else:
        raise ScheduleAuthorityError("explicit logical trigger required")
    value = {"workflow": workflow, "scope": scope, **logical}
    return hashlib.sha256(canonical(value).encode()).hexdigest(), value


@dataclass(frozen=True)
class Lease:
    key: str
    workflow: str
    scope: str
    owner: str
    generation: int
    actor: str
    ledger: str


def _owner(conn, workflow, scope, requested):
    from .joint_cutover import assert_schedule_open
    assert_schedule_open(workflow,json.loads(scope))
    row = conn.execute("SELECT * FROM schedule_scope_owners WHERE workflow=? AND scope=?",
                       (workflow, scope)).fetchone()
    if row is None:
        template = conn.execute("SELECT * FROM schedule_scope_owners WHERE workflow=? AND scope='*'",
                                (workflow,)).fetchone()
        if template is None:
            raise ScheduleAuthorityError("workflow has no declared schedule owner")
        from .isolation import verified_isolation_root
        # Isolated experimental execution does not activate any production route.
        initial = requested if verified_isolation_root() is not None else template["owner"]
        conn.execute("INSERT INTO schedule_scope_owners VALUES(?,?,?,1,0,'')",
                     (workflow, scope, initial))
        row = conn.execute("SELECT * FROM schedule_scope_owners WHERE workflow=? AND scope=?",
                           (workflow, scope)).fetchone()
    return row


def execute(workflow, scope, trigger, action, *, owner="legacy", actor="runtime", config_dir=None):
    if actor == "runtime":
        actor = "runtime-" + uuid4().hex
    startup(config_dir=config_dir)
    key, logical = identity(workflow, scope, trigger)
    scope_json = canonical(scope)
    parent = _ACTIVE.get()
    if parent and parent.workflow == workflow and parent.scope == scope_json:
        with publication_guard():
            pass
        return action()
    observe("arrival", key, {"owner": owner, "actor": actor,
                            "kind": trigger.kind if hasattr(trigger, "kind") else trigger.get("kind"),
                            "identity": logical})
    with closing(_connect(write=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            state = _owner(conn, workflow, scope_json, owner)
            held = conn.execute("SELECT * FROM schedule_runtime_claims WHERE key=?", (key,)).fetchone()
            if held is None and conn.execute("SELECT 1 FROM schedule_runtime_history WHERE key=? AND action='claim' LIMIT 1", (key,)).fetchone():
                raise ScheduleAuthorityError("claim record missing despite durable history; recovery required")
            resuming = bool(held and held["status"] in {"pending", "failed"}
                            and held["owner"] == owner and held["generation"] == state["generation"])
            if resuming and held["status"] == "failed" and held["actor"] == actor:
                raise ScheduleAuthorityError("retry requires a distinct attempt actor; the old lease cannot be revived")
            if held is not None and not resuming:
                _history(conn, "skip", key, {"status": held["status"], "owner": owner,
                                           "generation": state["generation"]})
                conn.commit()
                return {"schedule_skipped": True, "key": key, "status": held["status"],
                        "result_refs": json.loads(held["result_refs"]),
                        "result": json.loads(held["result_json"])}
            if state["frozen"] or state["owner"] != owner:
                raise ScheduleAuthorityError("schedule frozen or caller is not current owner")
            lease = Lease(key, workflow, scope_json, owner, state["generation"], actor, str(state_path().resolve()))
            if resuming:
                conn.execute("UPDATE schedule_runtime_claims SET status='running',actor=?,updated_at=? WHERE key=?",
                             (actor, datetime.now(UTC).isoformat(), key))
            else:
                conn.execute("INSERT INTO schedule_runtime_claims(key,workflow,scope,identity,owner,generation,status,actor,updated_at) VALUES(?,?,?,?,?,?,'running',?,?)",
                             (key, workflow, scope_json, canonical(logical), owner, lease.generation,
                              actor, datetime.now(UTC).isoformat()))
            _history(conn, "claim", key, {"owner": owner, "generation": lease.generation, "actor": actor})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    token = _ACTIVE.set(lease)
    logical_token = _LOGICAL.set(trigger)
    try:
        result = action()
        with publication_guard():
            conn = _PUBLISHING.get()
            conn.execute("UPDATE schedule_runtime_claims SET status='complete',result_json=?,updated_at=? WHERE key=?",
                         (canonical(result), datetime.now(UTC).isoformat(), key))
            _history(conn, "complete", key, {"owner": owner, "generation": lease.generation,
                "reason": "actual execution completed", "result_refs": json.loads(conn.execute(
                    "SELECT result_refs FROM schedule_runtime_claims WHERE key=?", (key,)).fetchone()[0])})
        return result
    except Exception as exc:
        if str(state_path().resolve()) != lease.ledger:
            raise
        with closing(_connect(write=True)) as conn:
            conn.execute("UPDATE schedule_runtime_claims SET status='failed' WHERE key=? AND owner=? AND generation=? AND actor=? AND result_refs='[]' AND status='running' AND NOT EXISTS (SELECT 1 FROM schedule_runtime_history h WHERE h.key=schedule_runtime_claims.key AND h.action='publication')",
                         (key, owner, lease.generation, actor))
            _history(conn, "execution_error", key, {"error": str(exc), "generation": lease.generation})
        raise
    finally:
        _LOGICAL.reset(logical_token)
        _ACTIVE.reset(token)


@contextmanager
def publication_guard():
    lease = _ACTIVE.get()
    if lease is None or _PUBLISHING.get():
        yield
        return
    from .joint_cutover import assert_schedule_open
    assert_schedule_open(lease.workflow,json.loads(lease.scope))
    if str(state_path().resolve()) != lease.ledger:
        raise ScheduleAuthorityError("schedule authority path changed after claim; refusing publication")
    with closing(_connect(write=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT * FROM schedule_scope_owners WHERE workflow=? AND scope=?",
                             (lease.workflow, lease.scope)).fetchone()
        row = conn.execute("SELECT * FROM schedule_runtime_claims WHERE key=?", (lease.key,)).fetchone()
        valid = (state and row and state["owner"] == row["owner"] == lease.owner
                 and state["generation"] == row["generation"] == lease.generation
                 and row["actor"] == lease.actor
                 and (row["status"] == "running" or (_FINALIZING.get() and row["status"] == "complete"))
                 and (not _FINALIZING.get() or not state["frozen"]))
        if not valid:
            _history(conn, "publication_refused", lease.key, {"owner": lease.owner,
                "generation": lease.generation, "current": dict(state) if state else None})
            conn.commit()
            raise ScheduleAuthorityError("schedule publication owner/generation fenced")
        flag = _PUBLISHING.set(conn)
        try:
            yield
            assert_schedule_open(lease.workflow,json.loads(lease.scope))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _PUBLISHING.reset(flag)


def publication_write(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _FINALIZING.get() and function.__name__ not in {"promote_score_run", "stamp_score_run"}:
            raise ScheduleAuthorityError("completed-claim finalization cannot publish new analysis")
        with publication_guard():
            try:
                if args and hasattr(args[0], "conn"):
                    bind_publication(args[0].conn, "writer:" + function.__name__)
                    if function.__name__ in {"record_score_run", "promote_score_run", "stamp_score_run"} and _ACTIVE.get():
                        import inspect

                        values = inspect.signature(function).bind(*args, **kwargs).arguments
                        symbol, label = values["symbol"].upper(), values["fiscal_label"]
                        lease = _ACTIVE.get()
                        if json.loads(lease.scope) != {"kind": "entity", "id": symbol}:
                            raise ScheduleAuthorityError("score publication scope differs from its claim")
                        logical = _LOGICAL.get()
                        if logical and logical.event_id.startswith("earnings:") and earnings_event_identity(symbol, label) != logical.event_id:
                            raise ScheduleAuthorityError("score fiscal period differs from its claimed event")
                        if function.__name__ == "record_score_run":
                            bind_publication(args[0].conn, score_reference(symbol, label, values["version"]))
                        elif _FINALIZING.get():
                            prior = args[0].latest_score_run(symbol, label)
                            reference = score_reference(symbol, label, prior["version"]) if prior else ""
                            bound = args[0].conn.execute("SELECT 1 FROM schedule_result_publications WHERE claim_key=? AND reference=? AND owner=? AND generation=? AND actor=?",
                                (lease.key, reference, lease.owner, lease.generation, lease.actor)).fetchone()
                            if bound is None:
                                raise ScheduleAuthorityError("original claimed score result required for finalization")
                result = function(*args, **kwargs)
            except Exception:
                # A rejected binding or failed INSERT must not leave an
                # uncommitted result marker on the caller's Memory connection.
                if args and hasattr(args[0], "conn"):
                    args[0].conn.rollback()
                raise
            conn, lease = _PUBLISHING.get(), _ACTIVE.get()
            if conn and lease:
                ref = str(result) if isinstance(result, (str, Path)) else ""
                row = conn.execute("SELECT result_refs FROM schedule_runtime_claims WHERE key=?", (lease.key,)).fetchone()
                refs = list(dict.fromkeys([*json.loads(row[0]), *([ref] if ref else [])]))
                conn.execute("UPDATE schedule_runtime_claims SET result_refs=? WHERE key=?", (canonical(refs), lease.key))
                _history(conn, "publication", lease.key, {"writer": function.__name__, "ref": ref,
                                                         "generation": lease.generation})
            return result
    return wrapped


def score_reference(symbol, label, version):
    return "score:" + canonical({"symbol": symbol, "fiscal_label": label, "version": version})


def finalize_metadata(workflow, scope, trigger, action, *, owner="legacy", reason):
    """Finalize an existing result under its original lease, without rerunning analysis."""
    startup()
    if not reason:
        raise ScheduleAuthorityError("metadata finalization reason required")
    key, _ = identity(workflow, scope, trigger)
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM schedule_runtime_claims WHERE key=?", (key,)).fetchone()
        if row is None or row["status"] != "complete" or row["owner"] != owner:
            raise ScheduleAuthorityError("recorded completed result required for metadata finalization")
        lease = Lease(key, workflow, canonical(scope), owner, row["generation"], row["actor"], str(state_path().resolve()))
    active, flag = _ACTIVE.set(lease), _FINALIZING.set(True)
    logical = _LOGICAL.set(trigger)
    try:
        with publication_guard():
            result = action()
            _history(_PUBLISHING.get(), "finalize_metadata", key, {"reason": reason, "generation": lease.generation})
            return result
    finally:
        _LOGICAL.reset(logical)
        _FINALIZING.reset(flag)
        _ACTIVE.reset(active)


def publication_report(function):
    """Fence files as results too; isolated runs publish only beneath their root."""
    import inspect
    signature = inspect.signature(function)

    @publication_write
    @wraps(function)
    def wrapped(*args, **kwargs):
        from .isolation import verified_isolation_root

        root = verified_isolation_root()
        bound = signature.bind(*args, **kwargs)
        cfg = bound.arguments.get("cfg")
        if root is not None and cfg is not None and cfg.output_dir:
            folder = root / "reports"
            folder.mkdir(parents=True, exist_ok=True)
            bound.arguments["cfg"] = cfg.model_copy(update={"output_dir": str(folder)})
        return function(*bound.args, **bound.kwargs)
    return wrapped


def bind_publication(conn, reference):
    """Commit identity with the real result, closing the cross-database crash gap."""
    lease = _ACTIVE.get()
    if lease is None:
        return
    existing = conn.execute("SELECT owner,generation,actor FROM schedule_result_publications WHERE claim_key=?",
                            (lease.key,)).fetchall()
    if any((row[0], row[1], row[2]) != (lease.owner, lease.generation, lease.actor) for row in existing):
        raise ScheduleAuthorityError("logical trigger already published by another attempt; recover completion instead of republishing")
    conn.execute("INSERT OR IGNORE INTO schedule_result_publications VALUES(?,?,?,?,?)",
                 (lease.key, reference, lease.owner, lease.generation, lease.actor))


@contextmanager
def wake(trigger):
    token = _WAKE.set(trigger)
    try:
        yield
    finally:
        _WAKE.reset(token)


def scheduled_cli(workflow, *, kind="portfolio", argument=None, scope_id="portfolio", restore=None, inherit_owner=False):
    """Resident cron/event and direct manual calls meet at the same public entry."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            active = _ACTIVE.get()
            import inspect
            bound = inspect.signature(function).bind(*args, **kwargs)
            bound.apply_defaults()
            selected = workflow(bound.arguments) if callable(workflow) else workflow
            ident = argument(bound.arguments) if callable(argument) else (bound.arguments.get(argument, scope_id) if argument else scope_id)
            scope = {"kind": kind, "id": str(ident).upper() if kind == "entity" else str(ident)}
            if active and active.workflow == selected and active.scope == canonical(scope):
                with publication_guard():
                    pass
                return function(*args, **kwargs)
            trigger = _WAKE.get() or _LOGICAL.get()
            if trigger is None:
                from .run_contracts import TriggerContext
                trigger = TriggerContext(kind="manual", trigger_id="manual-" + uuid4().hex)
            trigger = adapt_wake(selected, trigger)
            selected_owner = active.owner if active and inherit_owner else "legacy"
            result = execute(selected, scope, trigger, lambda: function(*args, **kwargs), owner=selected_owner, actor=selected_owner + "-" + uuid4().hex)
            if isinstance(result, dict) and result.get("schedule_skipped") and function.__module__.endswith("runtime.cli"):
                return 0
            if isinstance(result, dict) and result.get("schedule_skipped"):
                if result["status"] != "complete":
                    raise ScheduleAuthorityError("logical work unfinished or void; explicit disposition required")
                return restore(result["result"]) if restore else result["result"]
            return result
        return wrapped
    return decorate


def adapt_wake(workflow, trigger, *, config_dir=None):
    """A legacy cascade's work resolves to the corresponding workflow's planned slot."""
    if trigger.kind != "schedule" or trigger.schedule_id not in {"daily_cycle", "weekly_review"}:
        return trigger
    from zoneinfo import ZoneInfo
    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    schedules = yaml.safe_load((root / "workflow/phase_e_schedules.yaml").read_text())["workflows"]
    alias = "sector-review" if workflow == "layer-review" else workflow
    if alias not in schedules:
        return trigger
    cron = schedules[alias]["cron"]
    point = datetime.fromisoformat(trigger.scheduled_for).astimezone(ZoneInfo(cron["timezone"]))
    point = point.replace(hour=int(cron["hour"]), minute=int(cron["minute"]), second=0, microsecond=0)
    return trigger.model_copy(update={"schedule_id": workflow, "scheduled_for": point.isoformat()})


def snapshot():
    with closing(_connect()) as conn:
        return {"owners": [dict(row) for row in conn.execute("SELECT * FROM schedule_scope_owners")],
                "claims": [dict(row) for row in conn.execute("SELECT * FROM schedule_runtime_claims")],
                "history": [dict(row) for row in conn.execute("SELECT * FROM schedule_runtime_history ORDER BY seq")]}


def check_owner(workflow, scope, owner):
    startup()
    with closing(_connect(write=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = _owner(conn, workflow, canonical(scope), owner)
        if state["frozen"] or state["owner"] != owner:
            conn.rollback()
            raise ScheduleAuthorityError("schedule caller is fenced by current SQL owner/generation")
        conn.commit()
        return dict(state)


def current_owner(workflow, scope, *, candidate=None):
    startup()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT owner,scope FROM schedule_scope_owners WHERE workflow=? AND scope IN (?, '*') ORDER BY scope='*' LIMIT 1",
                           (workflow, canonical(scope))).fetchone()
        if row is None:
            raise ScheduleAuthorityError("schedule workflow owner missing")
        from .isolation import verified_isolation_root
        return candidate if candidate and row[1] == '*' and verified_isolation_root() is not None else row[0]


def record_reuse(refs):
    """Record validated, existing results without pretending to publish them again."""
    with publication_guard():
        conn, lease = _PUBLISHING.get(), _ACTIVE.get()
        if conn is None or lease is None or not refs:
            raise ScheduleAuthorityError("claimed reuse needs verified projection references")
        conn.execute("UPDATE schedule_runtime_claims SET result_refs=? WHERE key=?", (canonical(list(refs)), lease.key))
        _history(conn, "reuse", lease.key, {"refs": list(refs), "generation": lease.generation})


def reload_authority(*, config_dir=None):
    """Controlled reload reads SQL; changing YAML never installs a new generation."""
    startup(config_dir=config_dir)
    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    with closing(_connect(write=True)) as conn:
        _history(conn, "reload", "", {"yaml_hash": hashlib.sha256((root / "workflow/workflow_owners.yaml").read_bytes()).hexdigest(),
                                       "authority": "SQL", "source_lifecycle": "unchanged"})
    return snapshot()


def observe(action, key, payload):
    with closing(_connect(write=True)) as conn:
        _history(conn, action, key, payload)


def config_event_context(event, *, config_dir=None):
    """Use the same stable calendar identity as the managed configuration adapter."""
    from ..data.calendar_refresh import _manual_overlay_candidates
    from .run_contracts import TriggerContext

    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    label = event.label if hasattr(event, "label") else event["label"]
    day = str(event.date if hasattr(event, "date") else event["date"])
    matches = [item for item in _manual_overlay_candidates(root)
               if item.label == label and str(item.event_date) == day]
    if len(matches) != 1:
        raise ScheduleAuthorityError("configured calendar identity ambiguous or missing")
    item = matches[0]
    version = "config:" + hashlib.sha256(canonical(item.trigger_signature()).encode()).hexdigest()
    # Reading an existing published calendar never creates it or acquires a lease.
    from ..data.runtime.repository import platform_data_db_path
    path = Path(platform_data_db_path())
    if path.is_file():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='schedule_events'").fetchone():
                return TriggerContext(kind="event", event_id=item.event_id, event_version=version)
            row = conn.execute("SELECT event_version,payload_json FROM schedule_events WHERE event_id=? ORDER BY event_version DESC LIMIT 1", (item.event_id,)).fetchone()
            if row:
                body = json.loads(row[1])
                if str(body.get("event_date")) != day:
                    raise ScheduleAuthorityError("configured event date differs from current published version")
                version = str(row[0])
    return TriggerContext(kind="event", event_id=item.event_id, event_version=version)


def earnings_event_identity(symbol, fiscal_label):
    import re


    text = str(fiscal_label).upper().strip()
    period = re.fullmatch(r"(?:FY)?(\d{2}|\d{4})\s*Q([1-4])", text)
    inverted = re.fullmatch(r"Q([1-4])\s*(?:FY)?(\d{2}|\d{4})", text)
    if period is None and inverted is None:
        raise ScheduleAuthorityError("canonical fiscal year/quarter required for score identity")
    year, quarter = (int(period[1]), period[2]) if period else (int(inverted[2]), inverted[1])
    if year < 100:
        year += 2000
    return f"earnings:{str(symbol).upper()}:FY{year}Q{quarter}:release"


def released_earnings_context(symbol, fiscal_label):
    """A score-window wake names the released event, not another earnings run."""
    from ..data.runtime.repository import platform_data_db_path
    from .run_contracts import TriggerContext

    event_id = earnings_event_identity(symbol, fiscal_label)
    path = Path(platform_data_db_path())
    if not path.is_file():
        raise ScheduleAuthorityError("published earnings event version missing; score not claimed")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='schedule_events'").fetchone():
            raise ScheduleAuthorityError("published earnings event version missing; score not claimed")
        row = conn.execute("SELECT event_version,status FROM schedule_events WHERE event_id=? AND valid_until='' ORDER BY event_version DESC LIMIT 1", (event_id,)).fetchone()
        if row is None or row[1] != "released":
            raise ScheduleAuthorityError("current released earnings event version required; score not claimed")
    return TriggerContext(kind="event", event_id=event_id, event_version=str(row[0]))


def expected_triggers(start, end, *, config_dir=None):
    """Independent cron/config event oracle. Never infers coverage from claims."""
    from zoneinfo import ZoneInfo

    from apscheduler.triggers.cron import CronTrigger

    from ..agent.task_projection import ProjectionScope
    from ..config import AppConfig
    from .phase_e import _scopes
    from .run_contracts import TriggerContext

    if start.tzinfo is None or end.tzinfo is None or start > end:
        raise ScheduleAuthorityError("valid timezone-aware comparison window required")
    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    raw = yaml.safe_load((root / "settings.yaml").read_text())
    settings = AppConfig.model_validate(raw.get("app", raw)).schedule
    schedules = yaml.safe_load((root / "workflow/phase_e_schedules.yaml").read_text())["workflows"]
    pead = yaml.safe_load((root / "pead.yaml").read_text())
    output = {}
    wake_expected = []

    def add(workflow, scope, trigger, path):
        for target in _scopes(workflow, ProjectionScope.model_validate(scope), root):
            key, logical = identity(workflow, target.model_dump(mode="json"), trigger)
            row = output.setdefault(key, {"key": key, "identity": logical, "paths": []})
            if path not in row["paths"]:
                row["paths"].append(path)

    def ticks(cron):
        engine = CronTrigger(**cron)
        point = engine.get_next_fire_time(None, start)
        while point is not None and point <= end:
            yield point
            point = engine.get_next_fire_time(point, point)

    for workflow, entry in schedules.items():
        if entry.get("enabled"):
            for point in ticks(entry["cron"]):
                wake_expected.append({"job_id": "phase_e:" + workflow, "planned": point.astimezone(UTC).isoformat(), "owner": "dispatcher"})
                add(workflow, entry["scope"], TriggerContext(kind="schedule", schedule_id=workflow, scheduled_for=point.isoformat()), "dispatcher")
    h, m = map(int, settings.run_at.split(":"))
    stages = settings.daily_stages
    legacy_daily = []
    if stages.pead_daily:
        legacy_daily += ["information-brief", "fundamental-routine"]
    if stages.technical_daily:
        legacy_daily.append("technical-review")
    daily_cron = {"day_of_week": "mon-fri", "hour": h, "minute": m, "timezone": settings.timezone}
    for point in ticks(daily_cron):
        if any(stages.model_dump().values()):
            wake_expected.append({"job_id": "daily_cycle", "planned": point.astimezone(UTC).isoformat(), "owner": "legacy"})
        for workflow in legacy_daily:
            trigger = adapt_wake(workflow, TriggerContext(kind="schedule", schedule_id="daily_cycle", scheduled_for=point.isoformat()), config_dir=root)
            add(workflow, schedules[workflow]["scope"], trigger, "legacy")
    if settings.jobs.weekly_review:
        h, m = map(int, settings.weekly_review_at.split(":"))
        for point in ticks({"day_of_week": "sat", "hour": h, "minute": m, "timezone": settings.weekly_review_tz}):
            wake_expected.append({"job_id": "weekly_review", "planned": point.astimezone(UTC).isoformat(), "owner": "legacy"})
            for workflow, enabled in [("macro-review", pead.get("macro_review", {}).get("enabled", False)),
                                      ("sector-review", pead.get("sector_review", {}).get("enabled", False))]:
                if enabled:
                    trigger = adapt_wake(workflow, TriggerContext(kind="schedule", schedule_id="weekly_review", scheduled_for=point.isoformat()), config_dir=root)
                    if workflow == "sector-review":
                        for name in pead.get("sector_review", {}).get("sectors", []):
                            add(workflow, {"kind": "sector", "id": name}, trigger, "legacy")
                            add("layer-review", {"kind": "sector", "id": name}, trigger, "legacy")
                    else:
                        add(workflow, schedules[workflow]["scope"], trigger, "legacy")
    jobs = []
    for name, hhmm in (pead.get("schedule", {}).get("score_windows", {}) or {}).items():
        if getattr(settings.jobs, "pead_score_" + name, True):
            h, m = map(int, str(hhmm).split(":"))
            jobs.append(("pead_score_" + name, {"day_of_week": "mon-fri", "hour": h, "minute": m, "timezone": settings.timezone}))
    if settings.jobs.factset_weekly_ingest:
        semantic = os.environ.get("ATS_FACTSET_SCHEDULE_SEMANTIC") == "1"
        h, m = map(int, (settings.factset_month_end_at if semantic else settings.factset_refresh_at).split(":"))
        jobs.append(("factset_monthly_ingest" if semantic else "factset_weekly_ingest", {
            **({"day": "last"} if semantic else {"day_of_week": "sat"}), "hour": h, "minute": m,
            "timezone": settings.factset_month_end_tz if semantic else settings.factset_refresh_tz}))
    app = AppConfig.model_validate(raw.get("app", raw))
    if app.journal.enabled and settings.jobs.journal_reconcile:
        h, m = map(int, app.journal.reconcile_at.split(":"))
        jobs.append(("journal_reconcile", {"day_of_week": "mon-fri", "hour": h, "minute": m, "timezone": settings.timezone}))
    for job_id, cron in jobs:
        for point in ticks(cron):
            wake_expected.append({"job_id": job_id, "planned": point.astimezone(UTC).isoformat(), "owner": "legacy"})
    events = yaml.safe_load((root / "events.yaml").read_text()).get("events", [])
    for event in events:
        day = str(event["date"])
        if not (start.astimezone(ZoneInfo(settings.timezone)).date().isoformat() <= day <= end.astimezone(ZoneInfo(settings.timezone)).date().isoformat()):
            continue
        for route in event.get("triggers", []):
            workflow = "macro-review" if route == "macro" else "sector-review" if route.startswith("sector") else "information-brief" if route.startswith("pead:") else None
            if workflow is None:
                raise ScheduleAuthorityError("unknown configured event route")
            enabled = stages.pead_event_triggers if route.startswith("pead:") else stages.macro_sector_event_triggers
            if not enabled:
                continue
            trigger = config_event_context(event, config_dir=root)
            if route.startswith("pead:"):
                scope = {"kind": "entity", "id": route.split(":", 1)[1]}
            elif route.startswith("sector:"):
                scope = {"kind": "sector", "id": route.split(":", 1)[1]}
            else:
                scope = schedules[workflow]["scope"]
            add(workflow, scope, trigger, "legacy")
    from ..data.runtime.repository import platform_data_db_path
    calendar_path = Path(platform_data_db_path())
    if calendar_path.is_file():
        with closing(sqlite3.connect(calendar_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='schedule_events'").fetchone():
                routes = yaml.safe_load((root / "workflow/event_routes.yaml").read_text()).get("events", [])
                for record in conn.execute("SELECT * FROM schedule_events WHERE valid_until='' AND status IN ('planned','released')"):
                    body = json.loads(record["payload_json"])
                    for route in routes:
                        types = route["event_type"] if isinstance(route["event_type"], list) else [route["event_type"]]
                        if record["event_type"] not in types or record["status"] != route.get("required_event_state"):
                            continue
                        day = datetime.fromisoformat(record["event_date"]).date()
                        local_start = start.astimezone(ZoneInfo(route.get("window_timezone", settings.timezone))).date()
                        local_end = end.astimezone(ZoneInfo(route.get("window_timezone", settings.timezone))).date()
                        lead = int(route.get("lead_days_before", 0))
                        if not (local_start <= day <= local_end or (lead and 0 <= (day-local_start).days <= lead)):
                            continue
                        entity = str(body.get("metadata", {}).get("entity") or body.get("entity") or "")
                        if not entity and record["event_id"].startswith("earnings:"):
                            entity = record["event_id"].split(":")[1]
                        scope = {"kind": "entity", "id": entity} if route["scope_resolver"] == "event_entity" else {"kind": "portfolio", "id": ""}
                        context = TriggerContext(kind="event", event_id=record["event_id"], event_version=str(record["event_version"]))
                        for workflow in route["workflow_ids"]:
                            add(workflow, scope, context, "dispatcher")
    files = ["settings.yaml", "events.yaml", "pead.yaml", "workflow/phase_e_schedules.yaml", "workflow/event_routes.yaml", "workflow/workflow_owners.yaml"]
    return {"window": [start.isoformat(), end.isoformat()], "config_hashes": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files},
            "expected": sorted(output.values(), key=lambda row: row["key"]), "wake_expected": wake_expected}


def compare_triggers(expected):
    state = snapshot()
    arrivals = {}
    for record in state["history"]:
        if record["action"] == "arrival":
            arrivals.setdefault(record["key"], []).append(json.loads(record["payload"]))
    claims = {row["key"]: row for row in state["claims"]}
    rows = []
    for item in expected["expected"]:
        observed = {entry["owner"] for entry in arrivals.get(item["key"], [])}
        claim = claims.get(item["key"])
        rows.append({**item, "observed": sorted(observed), "missing_paths": sorted(set(item["paths"]) - observed),
            "both_missing": not observed, "status": claim["status"] if claim else "missing",
            "missing_results": not claim or not json.loads(claim["result_refs"])})
    duplicates = [key for key, entries in arrivals.items() if len(entries) > 1 and key in {row["key"] for row in rows}]
    misfires = [row for row in state["history"] if row["action"] == "wake_misfire"]
    seen = {(row["key"], json.loads(row["payload"])["planned"]) for row in state["history"] if row["action"] == "wake_seen"}
    missing_wakes = [item for item in expected.get("wake_expected", []) if (item["job_id"], item["planned"]) not in seen]
    return {"config_hashes": expected["config_hashes"], "window": expected["window"], "rows": rows,
            "duplicates": duplicates, "misfires": misfires,
            "missing_wakes": missing_wakes,
            "clean": bool(rows or expected.get("wake_expected")) and not missing_wakes and all(not row["missing_paths"] and not row["missing_results"] and row["status"] == "complete" for row in rows)}


def freeze(workflow, scope, *, actor, reason):
    from .isolation import verified_isolation_root
    if verified_isolation_root() is None:
        from .joint_cutover import authorize_schedule
        authorize_schedule(workflow,scope)
    if not actor or not reason:
        raise ScheduleAuthorityError("freeze actor/reason required")
    scope = canonical(scope)
    with closing(_connect(write=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM schedule_scope_owners WHERE workflow=? AND scope=?", (workflow, scope)).fetchone()
        if row is None or row["frozen"]:
            raise ScheduleAuthorityError("scope missing or already frozen")
        token = uuid4().hex
        conn.execute("UPDATE schedule_scope_owners SET frozen=1,token=? WHERE workflow=? AND scope=?", (token, workflow, scope))
        _history(conn, "freeze", token, {"workflow": workflow, "scope": scope, "actor": actor, "reason": reason})
        conn.commit()
    return token


def frozen_inventory(token):
    with closing(_connect()) as conn:
        state = conn.execute("SELECT * FROM schedule_scope_owners WHERE frozen=1 AND token=?", (token,)).fetchone()
        if state is None:
            raise ScheduleAuthorityError("valid frozen token required before inventory")
        return [dict(row) for row in conn.execute("SELECT * FROM schedule_runtime_claims WHERE workflow=? AND scope=? AND status IN ('running','failed','pending')",
                                                (state["workflow"], state["scope"]))]


def cancel_freeze(token, *, actor, reason):
    from .isolation import verified_isolation_root
    if verified_isolation_root() is None or not actor or not reason:
        raise ScheduleAuthorityError("freeze recovery requires isolated operator/reason")
    with closing(_connect(write=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT * FROM schedule_scope_owners WHERE frozen=1 AND token=?", (token,)).fetchone()
        if state is None:
            raise ScheduleAuthorityError("valid frozen token required")
        conn.execute("UPDATE schedule_scope_owners SET frozen=0,token='' WHERE token=?", (token,))
        _history(conn, "cancel_freeze", token, {"actor": actor, "reason": reason, "state": dict(state), "generation": state["generation"]})
        conn.commit()


def handover(token, *, to_owner, dispositions, actor, reason):
    """Atomic fenced handover. Production coordinator will supply its gates in 10.2/5.15."""
    from .isolation import verified_isolation_root
    if verified_isolation_root() is None:
        from .joint_cutover import authorize_schedule
        with closing(_connect()) as guard_conn:
            state=guard_conn.execute("SELECT workflow,scope FROM schedule_scope_owners WHERE frozen=1 AND token=?",(token,)).fetchone()
        if state is None:raise ScheduleAuthorityError("frozen token missing")
        authorize_schedule(state["workflow"],state["scope"])
    if to_owner not in {"legacy", "dispatcher", "shadow"} or not actor or not reason:
        raise ScheduleAuthorityError("owner/actor/reason required")
    with closing(_connect(write=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            state = conn.execute("SELECT * FROM schedule_scope_owners WHERE frozen=1 AND token=?", (token,)).fetchone()
            if state is None:
                raise ScheduleAuthorityError("frozen token missing")
            rows = conn.execute("SELECT * FROM schedule_runtime_claims WHERE workflow=? AND scope=? AND status IN ('running','failed','pending')",
                                (state["workflow"], state["scope"])).fetchall()
            if set(dispositions) != {row["key"] for row in rows}:
                raise ScheduleAuthorityError("each unfinished claim needs one explicit disposition")
            for row in rows:
                item = dispositions[row["key"]]
                mode = item.get("kind")
                if mode not in {"carry_over", "already_run", "void"} or not item.get("reason"):
                    raise ScheduleAuthorityError("disposition kind/reason required")
                if mode == "already_run":
                    raise ScheduleAuthorityError("already_run requires recorded completion, not operator assertion; let the worker finish")
                if mode == "carry_over":
                    if json.loads(row["result_refs"]):
                        raise ScheduleAuthorityError("partly published trigger must finish or be voided; cannot carry over published results")
                    # Keep identity/history; the old lease loses its generation.
                    conn.execute("UPDATE schedule_runtime_claims SET owner=?,generation=?,status='pending' WHERE key=?",
                                 (to_owner, state["generation"] + 1, row["key"]))
                else:
                    conn.execute("UPDATE schedule_runtime_claims SET status='void' WHERE key=?", (row["key"],))
                _history(conn, "disposition", row["key"], {**item, "actor": actor, "old_generation": state["generation"]})
            conn.execute("UPDATE schedule_scope_owners SET owner=?,generation=generation+1,frozen=0,token='' WHERE token=?",
                         (to_owner, token))
            _history(conn, "handover", token, {"from": dict(state), "to_owner": to_owner,
                "to_generation": state["generation"] + 1, "actor": actor, "reason": reason,
                "unfinished_inventory": [row["key"] for row in rows],
                "publisher_fence": "same SQLite writer transaction", "source_lifecycle": "unchanged"})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
