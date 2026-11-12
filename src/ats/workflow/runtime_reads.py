"""Business-entry read binding: one explicit scope per invocation (narrowed in switch-trade r5 9.2).

A consumer reads only what it declared (`config/data/target_dataflow_coverage.yaml`, see `read_scope`) and only under
the business scope its entry was bound to: the entities, the time point and the consumer of that entry. Reads that
escape the scope are refused with `ReadUnavailable`, never served empty. Whether the product itself is fit to read is
checked where it is read (`consumer_api.read_input`, `read_scope.read_projection`, the Dispatcher's product health
gate). The route tables, qualification ledger, read windows, invocation receipts and read-stop latches that used to sit
in front of every read were migration machinery and were retired.
"""

from __future__ import annotations

import inspect
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps

from ats.workflow.evaluation_clock import now as evaluation_now

from .read_scope import ReadScope, ReadUnavailable, consumer_contract


@dataclass(frozen=True)
class ReadContext:
    identity: ReadScope
    mode: str
    publication_path: str = ""
    cutoff: datetime | None = None
    execution_plan_hash: str = ""


_CURRENT: ContextVar[ReadContext | None] = ContextVar("business_read_context", default=None)
_REQUESTS: ContextVar[dict | None] = ContextVar("requested_read_scopes", default=None)


def current_read_context():
    return _CURRENT.get()


def invocation_time(value):
    point = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(point, datetime) or point.tzinfo is None:
        raise ReadUnavailable("business as_of requires a timezone")
    return point.astimezone(UTC)


def check_time(identity, as_of):
    """The read's time point must fall inside the scope's time range."""
    point = invocation_time(as_of)
    window = identity.scope["time_range"]
    if not datetime.fromisoformat(window["start"]) <= point <= datetime.fromisoformat(window["end"]):
        raise ReadUnavailable("business as_of is outside the bound scope's time range")
    return point


def business_identity(consumer, *, kind, scope_id, entities, as_of=None, explicit=None,
                      event_id="", event_version=""):
    """The scope of one business request. An explicit scope may narrow the time range, nothing else."""
    contract = consumer_contract(consumer)
    point = as_of or evaluation_now(UTC)
    point = datetime.fromisoformat(point) if isinstance(point, str) else point
    if point.tzinfo is None:
        raise ReadUnavailable("business as_of requires a timezone")
    scope = {"kind": kind, "id": scope_id, "entities": sorted(set(entities)),
             "time_range": {"start": point.isoformat(), "end": point.isoformat()}}
    if event_id or event_version:
        scope.update(event_id=event_id, event_version=event_version)
    if explicit is None:
        return ReadScope(contract["domain_id"], consumer, contract["contract_version"], scope)
    supplied = explicit.as_row() if isinstance(explicit, ReadScope) else explicit
    if "scope" in supplied:
        if any(supplied.get(k) != v for k, v in (
                ("domain_id", contract["domain_id"]), ("consumer_id", consumer),
                ("contract_version", contract["contract_version"]))):
            raise ReadUnavailable("requested read contract mismatch")
        supplied = supplied["scope"]
    identity = ReadScope(contract["domain_id"], consumer, contract["contract_version"], supplied)
    actual = identity.scope
    expected = ReadScope(contract["domain_id"], consumer, contract["contract_version"], scope).scope
    if any(actual.get(k) != expected.get(k) for k in ("kind", "id", "entities", "event_id", "event_version")):
        raise ReadUnavailable("requested read scope does not match business request")
    check_time(identity, point)
    return identity


def gate_read(identity):
    """Recheck at every read: the consumer is declared, and an isolated run still has its complete isolation."""
    consumer_contract(identity.consumer_id)
    if os.environ.get("ATS_RUN_MODE") == "isolated":
        from ..execution.broker_write_guard import startup
        from .isolation import verified_isolation_root

        startup(caller="business_read_gate", mode="isolated")
        if verified_isolation_root() is None:
            raise ReadUnavailable("isolated read environment is incomplete")


def guard_input(consumer, query, *, as_of=None, business_scope=None):
    """Check a native input request against the bound business scope before it can serve data."""
    current = current_read_context()
    if current and current.identity.consumer_id != consumer:
        raise ReadUnavailable("input consumer escapes bound business request")
    if current:
        identity = current.identity
        if business_scope is not None:
            supplied = business_scope.as_row() if isinstance(business_scope, ReadScope) else business_scope
            supplied = supplied.get("scope", supplied)
            candidate = ReadScope(identity.domain_id, consumer, identity.contract_version, supplied)
            if candidate != identity:
                raise ReadUnavailable("input business scope escapes bound request")
    elif business_scope is not None:
        supplied = business_scope.as_row() if isinstance(business_scope, ReadScope) else business_scope
        scope = supplied.get("scope", supplied)
        identity = business_identity(consumer, kind=scope["kind"], scope_id=scope["id"],
                                     entities=scope["entities"], as_of=as_of, explicit=supplied)
    else:
        raise ReadUnavailable("native input requires an explicit business scope or bound entry")
    entities = query.get("entities") or ([query["entity"]] if query.get("entity") else [])
    bound = identity.scope
    if not set(entities) <= set(bound["entities"]):
        raise ReadUnavailable("input entity escapes bound business scope")
    if bound.get("kind") == "decision" and query.get("cycle_id") is not None and str(query["cycle_id"]) != bound["id"]:
        raise ReadUnavailable("input cycle escapes bound business scope")
    if as_of is not None:
        point = as_of.astimezone(UTC).isoformat()
        expected = (current.cutoff.isoformat() if current and current.cutoff else
                    as_of.astimezone(UTC).isoformat())
        if point != expected:
            raise ReadUnavailable("input time escapes bound business scope")
    gate_read(identity)


@contextmanager
def bind_read(identity, *, as_of=None, mode=None, publication_path="", execution_plan_hash=""):
    """Bind one business scope and its time point for the reads made inside this entry."""
    window = identity.scope["time_range"]
    start, end = datetime.fromisoformat(window["start"]), datetime.fromisoformat(window["end"])
    parent = current_read_context()
    nested = parent is not None and parent.identity == identity
    if nested:
        # A nested entry is the same worker: it keeps the outer time point.
        cutoff = parent.cutoff
        if as_of is not None and check_time(identity, as_of) != cutoff:
            raise ReadUnavailable("nested entry cannot change invocation as_of")
    else:
        cutoff = check_time(identity, as_of if as_of is not None else end if start == end else evaluation_now(UTC))
    gate_read(identity)
    context = ReadContext(identity, mode or os.environ.get("ATS_RUN_MODE", "production"),
                          str(publication_path), cutoff,
                          parent.execution_plan_hash if nested else execution_plan_hash)
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


def configured_entities(kind, scope_id, *, sector="", config_dir=None):
    from pathlib import Path

    from ..config import REPO_ROOT
    from .yaml_cache import load_yaml

    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    if kind == "entity":
        return [scope_id.upper()]
    if kind in {"sector", "layer"}:
        candidates = sorted((root / "sectors").glob("*.yaml"))
        if kind == "sector" or sector:
            candidates = [path for path in candidates
                          if path.stem.lower() == (sector or scope_id).lower()]
        matches = []
        for path in candidates:
            cfg = load_yaml(path) or {}
            layers = cfg.get("layers", [])
            if kind == "layer":
                layers = [layer for layer in layers if layer.get("key") == scope_id]
                if not layers:
                    continue
            symbols = [str(t["symbol"]).upper() for layer in layers for t in layer.get("tickers", [])]
            symbols += [str(t["symbol"]).upper() for layer in layers for t in layer.get("peers", [])]
            if kind == "sector":
                symbols += [cfg[k] for k in ("sector_etf", "benchmark") if cfg.get(k)]
            matches.append(sorted(set(symbols)))
        if len(matches) != 1 or not matches[0]:
            raise ReadUnavailable(f"business {kind} scope is empty or ambiguous: {scope_id}")
        return matches[0]
    cfg = load_yaml(root / "pead.yaml") or {}
    return [str(s).upper() for s in cfg.get("targets", [])]


def consumer_entities(consumer, kind, scope_id, *, sector="", config_dir=None):
    """Return the configured consumer scope without discovery expansions.

    Information uses the explicit target only; peers and publisher-only entities
    are not added as discovery fallbacks.
    """
    entities = configured_entities(kind, scope_id, sector=sector, config_dir=config_dir)
    return sorted(set(entities))


def task_identity(plan, task):
    from pathlib import Path

    from ..config import REPO_ROOT
    from .planning import _file_hash

    root = Path(plan.config_root or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    for relative, expected in plan.source_config_hashes.items():
        source = root / relative.removeprefix("config/")
        if not source.is_file() or _file_hash(source) != expected:
            raise ReadUnavailable(f"business plan configuration drifted: {relative}")
    consumer = {"layer-review": "layer", "sector-review": "sector",
                "information-brief": "information", "fundamental-routine": "fundamental",
                "fundamental-event": "fundamental", "macro-review": "macro",
                "technical-review": "technical"}[task.task_id]
    scopes = plan.task_inputs.get("read_scopes", {}) or {}
    explicit = scopes.get(task.instance_key, scopes.get(f"{consumer}:{task.scope.key}"))
    entities = consumer_entities(consumer, task.scope.kind, task.scope.id,
                                   sector=plan.request_scope.id if plan.request_scope.kind == "sector" else "",
                                   config_dir=plan.config_root or None)
    event_id = plan.task_inputs.get("event_id", "")
    event_version = plan.task_inputs.get("event_version", "")
    if plan.trigger.get("kind") == "event":
        actual = (plan.trigger["event_id"], str(plan.trigger["event_version"]))
        if (event_id and event_id != actual[0]) or (event_version and str(event_version) != actual[1]):
            raise ReadUnavailable("task Event metadata differs from actual trigger")
        event_id, event_version = actual
    return business_identity(consumer, kind=task.scope.kind, scope_id=task.scope.id or "portfolio",
                             entities=entities, as_of=plan.as_of, explicit=explicit,
                             event_id=event_id, event_version=event_version)


def check_plan_reads(plan, *, collect=False):
    """Every task of a plan has a valid business scope under the current configuration."""
    refused = {}
    for task in plan.tasks:
        try:
            gate_read(task_identity(plan, task))
        except ReadUnavailable as exc:
            if not collect:
                raise
            refused[task.instance_key] = str(exc)
    return refused


def decision_read(consumer):
    """Bind a real decision node's role (Risk, Trader) to the cycle's own orders."""
    def decorate(function):
        @wraps(function)
        def entry(state, **kwargs):
            orders = [order for order in state.decisions if order.action != "hold"]
            if not orders:
                return function(state, **kwargs)
            explicit = kwargs.pop("read_scope", None)
            now = evaluation_now(UTC)
            entities = sorted({order.symbol for order in orders})
            identity = business_identity(consumer, kind="decision", scope_id=state.cycle_id,
                                         entities=entities, as_of=now, explicit=explicit)
            with bind_read(identity, as_of=now):
                return function(state, **kwargs)
        return entry
    return decorate


def scoped_read(consumer):
    """Wrap the real CLI entry; explicit read_scope also serves embedded callers."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def entry(*args, **kwargs):
            explicit = kwargs.pop("read_scope", (_REQUESTS.get() or {}).get(consumer))
            as_of = kwargs.pop("read_as_of", None)
            current = current_read_context()
            values = signature.bind(*args, **kwargs)
            values.apply_defaults()
            values = values.arguments
            if as_of is None and current and current.identity.consumer_id == consumer:
                as_of = current.cutoff
            if as_of is None and values.get("state") is not None:
                as_of = values["state"].as_of
            if as_of is None and values.get("date"):
                as_of = datetime.fromisoformat(values["date"]).replace(tzinfo=UTC)
            symbol = values.get("symbol")
            if values.get("request") is not None:
                symbol = values["request"].symbol
            if "args" in values:
                symbol = getattr(values["args"], "symbol", None)
            name = values.get("name", "")
            layer = values.get("layer_key", "all")
            if symbol:
                kind, scope_id = "entity", str(symbol).upper()
            elif consumer == "layer" and layer != "all":
                kind, scope_id = "layer", layer
            elif consumer in {"layer", "sector"}:
                kind, scope_id = "sector", name
            else:
                kind, scope_id = "portfolio", "portfolio"
            entities = consumer_entities(consumer, kind, scope_id, sector=name if kind == "layer" else "")
            if current and current.identity.consumer_id == consumer and explicit is None:
                scope = current.identity.scope
                if scope["kind"] != kind or scope["id"] != scope_id or scope["entities"] != sorted(set(entities)):
                    raise ReadUnavailable("nested business request escapes bound scope")
                identity = current.identity
                as_of = as_of or current.cutoff
            else:
                as_of = as_of or evaluation_now(UTC)
                identity = business_identity(consumer, kind=kind, scope_id=scope_id,
                                             entities=entities, as_of=as_of, explicit=explicit)
            with bind_read(identity, as_of=as_of, mode=current.mode if current else None,
                           publication_path=current.publication_path if current else ""):
                return function(*args, **kwargs)
        return entry
    return decorate


def cli_read_request(function):
    @wraps(function)
    def entry(argv=None):
        import sys

        arguments = list(sys.argv[1:] if argv is None else argv)
        supplied = {}
        for index, value in enumerate(arguments):
            if value == "--read-scope-json":
                supplied = json.loads(arguments[index + 1])
            elif value.startswith("--read-scope-json="):
                supplied = json.loads(value.split("=", 1)[1])
        if not isinstance(supplied, dict):
            raise ReadUnavailable("read scopes must be an object keyed by consumer")
        token = _REQUESTS.set(supplied)
        try:
            return function(argv)
        finally:
            _REQUESTS.reset(token)
    return entry
