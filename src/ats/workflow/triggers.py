"""Persistent trigger identity, misfire policy and dispatcher wake-up service.

Cron libraries only wake this service. The ledger—not APScheduler state—is the
authority for whether a logical manual, scheduled or event run already exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable, Iterable, Mapping
import uuid

import yaml

from ..config import REPO_ROOT
from .run_contracts import TriggerContext
from .store import WorkflowStore


def _utc(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("scheduled_for must include an explicit timezone")
    return dt.astimezone(timezone.utc)


def trigger_key(context: TriggerContext, workflow_id: str | None = None) -> str:
    """Stable idempotency key; scheduled identity uses the original planned tick."""
    ctx = context.model_copy(deep=True)
    if workflow_id:
        ctx.workflow_id = workflow_id
    ctx.validate_for_use()
    workflow = ctx.workflow_id
    if not workflow:
        raise ValueError("trigger context requires workflow_id")
    if ctx.kind == "manual":
        identity = ["manual", ctx.trigger_id, workflow]
    elif ctx.kind == "schedule":
        identity = ["schedule", ctx.schedule_id, _utc(ctx.scheduled_for).isoformat(), workflow]
    else:
        identity = ["event", ctx.event_id, ctx.event_version, workflow]
    digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    return f"trigger-{digest[:40]}"


@dataclass(frozen=True)
class TriggerPolicy:
    version: str = "1"
    misfire_action: str = "catch_up"
    misfire_grace_seconds: int = 21_600
    max_lookback_seconds: int = 172_800
    compensation_budget: int = 2
    require_trading_session: bool = False
    allowed_windows: tuple[str, ...] = ()
    lease_seconds: int = 300

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "TriggerPolicy":
        raw = dict(value or {})
        result = cls(
            version=str(raw.get("version", "1")),
            misfire_action=str(raw.get("misfire_action", "catch_up")),
            misfire_grace_seconds=int(raw.get("misfire_grace_seconds", 21_600)),
            max_lookback_seconds=int(raw.get("max_lookback_seconds", 172_800)),
            compensation_budget=int(raw.get("compensation_budget", 2)),
            require_trading_session=bool(raw.get("require_trading_session", False)),
            allowed_windows=tuple(str(x) for x in raw.get("allowed_windows", ())),
            lease_seconds=int(raw.get("lease_seconds", 300)),
        )
        if result.misfire_action not in {"catch_up", "skip"}:
            raise ValueError("misfire_action must be 'catch_up' or 'skip'")
        if min(result.misfire_grace_seconds, result.max_lookback_seconds,
               result.compensation_budget) < 0 or result.lease_seconds < 3:
            raise ValueError("trigger policy limits must be non-negative and lease >= 3s")
        return result


def load_trigger_policies(config_dir: str | Path | None = None) -> dict[str, TriggerPolicy]:
    root = Path(config_dir or REPO_ROOT / "config")
    path = root / "workflow" / "trigger_policies.yaml"
    if not path.exists():
        return {"default": TriggerPolicy()}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    version = str(payload.get("version", "1"))
    raw = payload.get("workflows", {}) or {}
    policies = {str(name): TriggerPolicy.from_mapping({"version": version, **settings})
                for name, settings in raw.items()}
    policies.setdefault("default", TriggerPolicy(version=version))
    return policies


def evaluate_misfire(*, scheduled_for: str | datetime, now: datetime,
                     policy: TriggerPolicy, compensation_count: int = 0) -> tuple[str, str, float]:
    """Return (dispatch|skip, reason, lag_seconds) for a cron tick."""
    planned, actual = _utc(scheduled_for), now.astimezone(timezone.utc)
    lag = max(0.0, (actual - planned).total_seconds())
    if lag == 0:
        action, reason = "dispatch", "on_time"
    elif policy.misfire_action == "skip":
        action, reason = "skip", "policy_skip"
    elif lag > policy.misfire_grace_seconds:
        action, reason = "skip", "misfire_grace_exceeded"
    elif lag > policy.max_lookback_seconds:
        action, reason = "skip", "max_lookback_exceeded"
    elif compensation_count >= policy.compensation_budget:
        action, reason = "skip", "compensation_budget_exhausted"
    else:
        action, reason = "dispatch", "catch_up"
    if policy.require_trading_session:
        from zoneinfo import ZoneInfo

        market_day = planned.astimezone(ZoneInfo("America/New_York")).date()
        try:
            from ..runtime.scheduler import is_trading_session

            is_session = is_trading_session(market_day)
        except Exception:  # missing/unavailable calendar must never bypass a required gate
            return "skip", "trading_calendar_unavailable", lag
        if not is_session:
            return "skip", "non_trading_session", lag
    return action, reason, lag


def _deterministic_run_id(key: str) -> str:
    return "run-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _request_fingerprint(context: TriggerContext, request: Mapping[str, Any]) -> dict[str, Any]:
    trigger = context.model_dump(mode="json")
    # requested_at records when this delivery was observed; it is not logical identity.
    trigger.pop("requested_at", None)
    if context.kind == "schedule":
        trigger["scheduled_for"] = _utc(context.scheduled_for).isoformat(timespec="microseconds")
    return {"trigger": trigger, "request": dict(request)}


class TriggerService:
    """Claim a durable trigger, build the frozen plan, and invoke Dispatcher once."""

    def __init__(self, store: WorkflowStore | None = None, *, owner_id: str = "",
                 policy: TriggerPolicy | None = None):
        self.store = store or WorkflowStore()
        self.owner_id = owner_id or f"worker-{uuid.uuid4().hex}"
        self.policy = policy or TriggerPolicy()

    def dispatch(self, context: TriggerContext, *, request: Mapping[str, Any],
                 plan_factory: Callable[[TriggerContext, Mapping[str, Any], str], Any],
                 dispatcher: Any, now: datetime | None = None) -> dict[str, Any]:
        ctx = context.model_copy(deep=True)
        ctx.validate_for_use()
        if not ctx.workflow_id:
            raise ValueError("workflow_id is required for trigger dispatch")
        when = now or datetime.now(timezone.utc)
        key = trigger_key(ctx)
        logical_id = {"trigger_id": ctx.trigger_id} if ctx.kind == "manual" else {}
        request_fingerprint = _request_fingerprint(ctx, request)
        row = self.store.record_trigger(
            trigger_key=key, kind=ctx.kind, workflow_id=ctx.workflow_id,
            request=request_fingerprint, schedule_id=ctx.schedule_id,
            scheduled_for=ctx.scheduled_for, event_id=ctx.event_id,
            event_version=ctx.event_version, policy_version=self.policy.version, at=when)
        if row["status"] in {"complete", "incomplete", "failed", "skipped",
                              "superseded", "waiting_material"}:
            return {"trigger_key": key, **logical_id, "run_id": row.get("run_id", ""),
                    "status": row["status"], "duplicate": True,
                    "reason": row.get("reason_code", "already_terminal")}
        lag = None
        if ctx.kind == "schedule":
            action, reason, lag = evaluate_misfire(
                scheduled_for=ctx.scheduled_for, now=when, policy=self.policy)
            if action == "skip":
                if row["status"] == "running":
                    resolved = self.store.resolve_expired_trigger(
                        key, status="skipped", reason_code=reason,
                        actual_lag_seconds=lag, at=when)
                    current = self.store.get_trigger(key) or row
                    if not resolved:
                        return {"trigger_key": key, **logical_id,
                                "run_id": current.get("run_id", ""),
                                "status": current["status"], "reason": "active_lease",
                                "duplicate": True}
                else:
                    self.store.resolve_planned_trigger(
                        key, status="skipped", reason_code=reason,
                        actual_lag_seconds=lag, at=when)
                return {"trigger_key": key, **logical_id, "status": "skipped", "reason": reason,
                        "actual_lag_seconds": lag, "duplicate": row["status"] != "planned"}
        # A resumed/replayed logical trigger uses the first ledger observation time,
        # so its as-of plan remains byte-for-byte stable across process restarts.
        ctx.requested_at = row["created_at"]
        run_id = _deterministic_run_id(key)
        claimed = self.store.claim_trigger(
            trigger_key=key, kind=ctx.kind, workflow_id=ctx.workflow_id,
            request=request_fingerprint, owner_id=self.owner_id,
            lease_seconds=self.policy.lease_seconds, run_id=run_id,
            schedule_id=ctx.schedule_id, scheduled_for=ctx.scheduled_for,
            event_id=ctx.event_id, event_version=ctx.event_version,
            policy_version=self.policy.version, at=when)
        if not claimed["acquired"]:
            return {"trigger_key": key, **logical_id, "run_id": claimed.get("run_id", ""),
                    "status": claimed["status"], "duplicate": True,
                    "reason": claimed.get("reason_code", "already_claimed")}
        self.store.bind_trigger_run(key, owner_id=self.owner_id, run_id=run_id)
        if int(claimed.get("attempt_count", 1)) > 1:
            previous_run = self.store.get_run(run_id)
            if previous_run and previous_run.get("status") in {"failed", "incomplete"}:
                compensation = next((item for item in reversed(self.store.trigger_history(key))
                                     if item["reason_code"] == "explicit_compensation"), {})
                detail = compensation.get("detail", {})
                self.store.reopen_run_for_compensation(
                    run_id, actor=str(detail.get("actor") or self.owner_id),
                    reason=str(detail.get("reason") or "trigger compensation"))

        stop = Event()
        lease_lost = Event()

        def renew() -> None:
            while not stop.wait(max(1.0, self.policy.lease_seconds / 3)):
                try:
                    if not self.store.renew_trigger(
                            key, owner_id=self.owner_id,
                            lease_seconds=self.policy.lease_seconds):
                        lease_lost.set()
                        return
                except Exception:
                    lease_lost.set()
                    return

        heartbeat = Thread(target=renew, name=f"trigger-lease-{key[-8:]}", daemon=True)
        heartbeat.start()
        try:
            plan = plan_factory(ctx, request, run_id)
            result = dispatcher.dispatch(plan, trigger_key=key,
                                         policy_version=self.policy.version)
            status = "complete" if result.status == "complete" else "incomplete"
            if lease_lost.is_set():
                return {"trigger_key": key, **logical_id, "run_id": run_id,
                        "status": "lease_lost",
                        "result": result.as_dict()}
            self.store.finish_trigger(key, owner_id=self.owner_id, status=status,
                                      reason_code="", actual_lag_seconds=lag)
            return {"trigger_key": key, **logical_id, "run_id": run_id, "status": status,
                    "duplicate": False, "result": result.as_dict()}
        except Exception as exc:
            if not lease_lost.is_set():
                self.store.finish_trigger(key, owner_id=self.owner_id, status="failed",
                                          reason_code=type(exc).__name__,
                                          actual_lag_seconds=lag)
            raise
        finally:
            stop.set()
            heartbeat.join(timeout=1)


def reconcile_schedule(store: WorkflowStore, *, expected: Iterable[tuple[TriggerContext, Mapping[str, Any]]],
                       now: datetime, policy: TriggerPolicy,
                       compensation_count: int = 0) -> list[dict[str, Any]]:
    """Record durable schedule ticks missing from the ledger and decide catch-up/skip."""
    decisions: list[dict[str, Any]] = []
    used_compensation = max(0, int(compensation_count))
    for context, request in expected:
        ctx = context.model_copy(deep=True)
        ctx.validate_for_use()
        if ctx.kind != "schedule":
            raise ValueError("schedule reconciliation accepts only schedule triggers")
        key = trigger_key(ctx)
        fingerprint = _request_fingerprint(ctx, request)
        existing = next((item for item in store.list_triggers(workflow_id=ctx.workflow_id,
                                                              limit=10_000)
                         if item["trigger_key"] == key), None)
        if existing:
            decisions.append({"trigger_key": key, "status": existing["status"],
                              "decision": "already_recorded"})
            continue
        store.record_trigger(
            trigger_key=key, kind="schedule", workflow_id=ctx.workflow_id,
            request=fingerprint, schedule_id=ctx.schedule_id,
            scheduled_for=ctx.scheduled_for, policy_version=policy.version, at=now)
        action, reason, lag = evaluate_misfire(scheduled_for=ctx.scheduled_for, now=now,
                                               policy=policy,
                                               compensation_count=used_compensation)
        if action == "skip":
            store.resolve_planned_trigger(key, status="skipped", reason_code=reason,
                                          actual_lag_seconds=lag, at=now)
        elif reason == "catch_up":
            used_compensation += 1
        decisions.append({"trigger_key": key, "status": "planned" if action == "dispatch"
                          else "skipped", "decision": reason,
                          "actual_lag_seconds": lag})
    return decisions


__all__ = ["TriggerPolicy", "TriggerService", "evaluate_misfire", "load_trigger_policies",
           "reconcile_schedule", "trigger_key"]
