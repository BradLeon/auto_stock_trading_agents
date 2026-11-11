"""What a consumer may read and under which business scope (switch-trade r5 9.2).

The long-term read contract after the route tables and qualification ledger were retired: a consumer's declaration
(domain, contract version, allowed products) comes from `config/data/target_dataflow_coverage.yaml`; a read happens
under one explicit business scope (`ReadScope`); a read that cannot be served raises `ReadUnavailable` and is never
degraded into an empty result. `record_read` leaves one audit line per actual read.
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class ReadUnavailable(RuntimeError):
    """The read cannot be served under its declared scope. Never silently degraded."""

    reason_code = "no_safe_route"


_TRACE: ContextVar[list | None] = ContextVar("consumer_read_trace", default=None)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _stamp(value: str) -> str:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("business time window must have a timezone")
    return stamp.astimezone(UTC).isoformat()


@dataclass(frozen=True, init=False)
class ReadScope:
    domain_id: str
    consumer_id: str
    contract_version: str
    scope_json: str

    def __init__(self, domain_id: str, consumer_id: str, contract_version: str,
                 scope: dict[str, Any]):
        for key, value in (("domain_id", domain_id), ("consumer_id", consumer_id),
                           ("contract_version", contract_version)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} is required")
            object.__setattr__(self, key, value)
        # Copy before normalizing: callers cannot mutate a persisted identity.
        normalized = json.loads(canonical_json(scope))
        if not isinstance(normalized, dict) or not normalized.get("kind") or not normalized.get("id"):
            raise ValueError("business scope requires kind/id")
        entities = normalized.get("entities")
        if not isinstance(entities, list) or not entities or any(
                not isinstance(e, str) or not e.strip() for e in entities):
            raise ValueError("business scope requires explicit entities")
        normalized["entities"] = sorted(set(entities))
        window = normalized.get("time_range")
        if not isinstance(window, dict) or set(window) != {"start", "end"}:
            raise ValueError("business scope requires time_range start/end")
        window["start"], window["end"] = _stamp(window["start"]), _stamp(window["end"])
        if datetime.fromisoformat(window["start"]) > datetime.fromisoformat(window["end"]):
            raise ValueError("business time window is reversed")
        if bool(normalized.get("event_id")) != bool(normalized.get("event_version")):
            raise ValueError("event_id and event_version must be supplied together")
        object.__setattr__(self, "scope_json", canonical_json(normalized))

    @property
    def scope(self) -> dict[str, Any]:
        return json.loads(self.scope_json)

    def as_row(self) -> dict[str, Any]:
        return {"domain_id": self.domain_id, "consumer_id": self.consumer_id,
                "contract_version": self.contract_version, "scope": self.scope}

    @property
    def key(self) -> str:
        return hashlib.sha256(canonical_json(self.as_row()).encode()).hexdigest()



def consumer_contract(consumer_id: str) -> dict[str, Any]:
    """The consumer's declared domain, contract version and scope.

    Read from the same manifest `qualification()` reads, so the gate cannot be
    called with a domain that disagrees with the one it validates against.
    """
    from ..config import REPO_ROOT
    from .yaml_cache import load_yaml

    coverage = REPO_ROOT / "config" / "data" / "target_dataflow_coverage.yaml"
    manifest = load_yaml(coverage)
    for consumer in manifest.get("consumers", []):
        if consumer.get("id") == consumer_id:
            products = sorted(consumer.get("products", ()))
            return {
                "domain_id": str(consumer.get("domain")),
                "contract_version": str(consumer.get("contract_version")),
                "scope": {"consumer": consumer_id, "products": products},
            }
    raise ReadUnavailable(
        f"consumer {consumer_id!r} has no contract in the coverage manifest; a "
        f"reader cannot be qualified for a consumer that is not declared")


@contextmanager
def trace_reads():
    """Collect actual reads in this task; no shared research or production writes."""
    rows = []
    token = _TRACE.set(rows)
    try:
        yield rows
    finally:
        _TRACE.reset(token)


def record_read(consumer, api, *, refs=(), status="complete", **detail):
    row = {"consumer": consumer, "api": api, "refs": list(refs), "status": status, **detail}
    from .runtime_reads import current_read_context

    context = current_read_context()
    if context is not None and context.identity.consumer_id == consumer:
        row.update(business_identity=context.identity.as_row(), invocation_as_of=context.cutoff.isoformat(),
                   run_mode=context.mode)
    logger = logging.getLogger("ats.consumer_reads")
    (logger.warning if status in {"blocked", "unavailable"} else logger.info)(
        "consumer_read %s", json.dumps(row, sort_keys=True, default=str))
    rows = _TRACE.get()
    if rows is not None:
        rows.append(row)


def read_projection(store, *, consumer, role, scope, at=None, projection_id="", content_hash="",
                    require_usable=True):
    """Read only allowed, published, reusable opinions; preserve concrete refs."""
    from ..agent.task_projection import PAYLOAD_SCHEMA_BY_ROLE, reuse_decision
    from ..agent.task_projection import content_hash as hash_projection
    from ..agents.chief.assemble import _envelope_from_row
    from ..config import REPO_ROOT
    from .yaml_cache import load_yaml

    owners = {"information_brief": "information", "layer_analysis": "layer",
              "sector_allocation": "sector", "fundamental_expectation_update": "fundamental",
              "fundamental_event_review": "fundamental", "macro_review": "macro",
              "technical_review": "technical"}
    manifest = load_yaml(REPO_ROOT / "config/data/target_dataflow_coverage.yaml")
    contract = next(row for row in manifest["consumers"] if row["id"] == consumer)
    if owners.get(role) != consumer and owners.get(role) not in contract["allowed_projection_inputs"]:
        record_read(consumer, "task_projection", status="blocked", producer=owners.get(role),
                    role=role, scope=scope.model_dump(mode="json"), reason_code="undeclared_projection_input")
        raise PermissionError("undeclared_projection_input")
    from .runtime_reads import current_read_context, gate_read

    context = current_read_context()
    if context:
        if context.identity.consumer_id != consumer:
            raise ReadUnavailable("projection consumer escapes business request")
        if scope.kind == "entity" and scope.id not in context.identity.scope["entities"]:
            raise ReadUnavailable("projection entity escapes business request")
        gate_read(context.identity)
        at = at or context.cutoff
        point = datetime.fromisoformat(at) if isinstance(at, str) else at
        if point != context.cutoff:
            raise ReadUnavailable("projection time escapes invocation cutoff")
    row = store.get_task_projection(projection_id) if projection_id else None
    if not projection_id:
        rows = store.task_projection_envelopes(agent_role=role, scope_kind=scope.kind,
                                               scope_id=scope.id, limit=1)
        row = rows[0] if rows else None
    if row and (row.get("agent_role") != role or row.get("scope_kind") != scope.kind
                or row.get("scope_id") != scope.id or
                (content_hash and row.get("content_hash") != content_hash)):
        raise ReadUnavailable("projection reference scope/hash mismatch")
    if row:
        envelope = _envelope_from_row(row, scope)
        schema = PAYLOAD_SCHEMA_BY_ROLE[role]
        validated_payload = schema.model_validate(envelope.payload)
        if (envelope.schema_name != schema.role_schema_name() or envelope.schema_version != "v1"
                or envelope.content_hash != hash_projection(
                    role=role, scope=scope, as_of=envelope.as_of,
                    schema_name=envelope.schema_name, schema_version=envelope.schema_version,
                    payload=validated_payload, input_refs=envelope.input_refs,
                    data_vintage_refs=envelope.data_vintage_refs)):
            record_read(consumer, "task_projection", status="blocked", refs=[envelope.projection_id])
            raise ReadUnavailable("projection schema/content integrity mismatch")
        ok, reason = reuse_decision(_envelope_from_row(row, scope), scope=scope, at=at)
        if not ok:
            record_read(consumer, "task_projection", status=reason, refs=[row["projection_id"]])
            if require_usable:
                return None
    record_read(consumer, "task_projection", status="complete" if row else "missing",
                producer=owners.get(role), role=role, scope=scope.model_dump(mode="json"),
                refs=[row["projection_id"]] if row else [],
                content_hash=row.get("content_hash", "") if row else "")
    return row
