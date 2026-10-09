"""Business-entry read gates. A scope is frozen per invocation, never per product."""

from __future__ import annotations

from ats.workflow.evaluation_clock import now as evaluation_now

import hashlib
import inspect
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps

from .cutover_routing import RouteUnavailable, _consumer_contract, read_route
from .scoped_routes import RouteIdentity


@dataclass(frozen=True)
class ReadContext:
    identity: RouteIdentity
    mode: str
    publication_path: str = ""
    route: str = "legacy"
    cutoff: datetime | None = None
    fallback_input: object | None = None
    recovery_generation: int = 0
    joint_epoch: int = 0


_CURRENT: ContextVar[ReadContext | None] = ContextVar("business_read_context", default=None)
_REQUESTS: ContextVar[dict | None] = ContextVar("requested_read_scopes", default=None)


def current_read_context():
    return _CURRENT.get()


def business_identity(consumer, *, kind, scope_id, entities, as_of=None, explicit=None,
                      event_id="", event_version=""):
    contract = _consumer_contract(consumer)
    point = as_of or evaluation_now(UTC)
    point = datetime.fromisoformat(point) if isinstance(point, str) else point
    if point.tzinfo is None:
        raise RouteUnavailable("business as_of requires a timezone")
    scope = {"kind": kind, "id": scope_id, "entities": sorted(set(entities)),
             "time_range": {"start": point.isoformat(), "end": point.isoformat()}}
    if event_id or event_version:
        scope.update(event_id=event_id, event_version=event_version)
    if explicit is not None:
        supplied = explicit.as_row() if isinstance(explicit, RouteIdentity) else explicit
        if "scope" in supplied:
            if any(supplied.get(k) != v for k, v in (
                    ("domain_id", contract["domain_id"]), ("consumer_id", consumer),
                    ("contract_version", contract["contract_version"]))):
                raise RouteUnavailable("requested read contract mismatch")
            supplied = supplied["scope"]
        identity = RouteIdentity(contract["domain_id"], consumer,
                                 contract["contract_version"], supplied)
        actual = identity.scope
        if any(actual.get(k) != scope.get(k) for k in ("kind", "id", "entities",
                                                      "event_id", "event_version")):
            raise RouteUnavailable("requested read scope does not match business request")
        if as_of is not None and actual["time_range"]["end"] != point.astimezone(UTC).isoformat():
            raise RouteUnavailable("requested read time does not match business as_of")
        if as_of is None and not (datetime.fromisoformat(actual["time_range"]["start"]) <= point <=
                                  datetime.fromisoformat(actual["time_range"]["end"])):
            raise RouteUnavailable("current business time is outside requested window")
        return identity
    return RouteIdentity(contract["domain_id"], consumer, contract["contract_version"], scope)


def gate_read(identity):
    from .joint_cutover import assert_scope_open
    from .cutover import CutoverError
    try:
        assert_scope_open(identity)
    except CutoverError as exc:
        raise RouteUnavailable(str(exc)) from exc
    if os.environ.get("ATS_RUN_MODE") == "isolated":
        from ..execution.broker_write_guard import startup
        from .isolation import verified_isolation_root

        startup(caller="business_read_gate", mode="isolated")
        if verified_isolation_root() is None:
            raise RouteUnavailable("isolated read environment is incomplete")
    decision = read_route(consumer_id=identity.consumer_id, identity=identity)
    if not decision.qualification:
        from ..data.assurance import qualification

        result = qualification(domain_id=identity.domain_id, consumer_id=identity.consumer_id,
                               contract_version=identity.contract_version, scope=identity.scope)
        decision.qualification = {"status": result.get("status"), "reasons": result.get("reasons", [])}
    return decision


def guard_input(consumer, query, *, as_of=None, business_scope=None):
    """Recheck the bound business identity before a native input can serve data."""
    current = current_read_context()
    if current and current.identity.consumer_id != consumer:
        raise RouteUnavailable("input consumer escapes bound business request")
    if current:
        identity = current.identity
        if business_scope is not None:
            supplied = business_scope.as_row() if isinstance(business_scope, RouteIdentity) else business_scope
            supplied = supplied.get("scope", supplied)
            candidate = RouteIdentity(identity.domain_id, consumer, identity.contract_version, supplied)
            if candidate != identity:
                raise RouteUnavailable("input business scope escapes bound request")
    elif business_scope is not None:
        supplied = business_scope.as_row() if isinstance(business_scope, RouteIdentity) else business_scope
        scope = supplied.get("scope", supplied)
        identity = business_identity(consumer, kind=scope["kind"], scope_id=scope["id"],
                                     entities=scope["entities"], as_of=as_of, explicit=supplied)
    else:
        raise RouteUnavailable("native input requires an explicit business scope or bound entry")
    entities = query.get("entities") or ([query["entity"]] if query.get("entity") else [])
    if not set(entities) <= set(identity.scope["entities"]):
        raise RouteUnavailable("input entity escapes bound business scope")
    if as_of is not None:
        point = as_of.astimezone(UTC).isoformat()
        expected = (current.cutoff.isoformat() if current and current.cutoff else
                    identity.scope["time_range"]["end"])
        if point != expected:
            raise RouteUnavailable("input time escapes bound business scope")
    decision = gate_read(identity)
    if decision.route != "target":
        from .isolation import verified_isolation_root

        # Candidate data in a fully isolated run can establish future evidence.
        # The native API has no legacy implementation: it must not silently serve
        # target data just because the old business route is still allowed.
        if verified_isolation_root() is None:
            raise RouteUnavailable("native target input requires an enabled exact business scope")
    return decision


@contextmanager
def bind_read(identity, *, mode=None, publication_path="", fallback_input=None):
    from .joint_cutover import epoch
    joint_epoch=epoch(identity)
    window = identity.scope["time_range"]
    start, end = datetime.fromisoformat(window["start"]), datetime.fromisoformat(window["end"])
    parent = current_read_context()
    if fallback_input is None and parent and parent.identity == identity:
        fallback_input = parent.fallback_input
    now = evaluation_now(UTC)
    cutoff = (parent.cutoff if parent and parent.identity == identity else
              now if start <= now <= end else end)
    try:
        decision = gate_read(identity)  # Recheck TTL/revocation on every entry, including reuse.
    except RouteUnavailable as exc:
        from .read_recovery import ReadStopped

        if isinstance(exc, ReadStopped):
            raise
        if fallback_input is None:
            raise
        from .consumer_reads import InternalFallback
        from .cutover_routing import RouteDecision

        if not isinstance(fallback_input, InternalFallback):
            raise RouteUnavailable("actual fallback adapter required")
        fallback_input = fallback_input.prepare(identity)  # Proof and actual readable data.
        decision = RouteDecision(identity.consumer_id, identity.domain_id,
                                 identity.contract_version, identity.scope, fallback_input.route)
    else:
        fallback_input = None
    from .read_recovery import ReadStopped, current_policy

    policy = current_policy(identity)
    if policy and policy["stopped"]:
        raise ReadStopped("scope stopped before context binding")
    if epoch(identity)!=joint_epoch:
        raise RouteUnavailable("joint authority changed during worker binding")
    context = ReadContext(identity, mode or os.environ.get("ATS_RUN_MODE", "production"),
                          str(publication_path), decision.route, cutoff, fallback_input,
                          policy["event_id"] if policy else 0,
                          joint_epoch)
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


def configured_entities(kind, scope_id, *, sector="", config_dir=None):
    from pathlib import Path

    import yaml

    from ..config import REPO_ROOT

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
            cfg = yaml.safe_load(path.read_text()) or {}
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
            raise RouteUnavailable(f"business {kind} scope is empty or ambiguous: {scope_id}")
        return matches[0]
    cfg = yaml.safe_load((root / "pead.yaml").read_text()) or {}
    return [str(s).upper() for s in cfg.get("targets", [])]


def task_identity(plan, task):
    from pathlib import Path

    from ..config import REPO_ROOT

    root = Path(plan.config_root or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    for relative, expected in plan.source_config_hashes.items():
        source = root / relative.removeprefix("config/")
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise RouteUnavailable(f"business plan configuration drifted: {relative}")
    consumer = {"layer-review": "layer", "sector-review": "sector",
                "information-brief": "information", "fundamental-routine": "fundamental",
                "fundamental-event": "fundamental", "macro-review": "macro",
                "technical-review": "technical"}[task.task_id]
    scopes = plan.task_inputs.get("read_scopes", {}) or {}
    explicit = scopes.get(task.instance_key, scopes.get(f"{consumer}:{task.scope.key}"))
    entities = configured_entities(task.scope.kind, task.scope.id,
                                   sector=plan.request_scope.id if plan.request_scope.kind == "sector" else "",
                                   config_dir=plan.config_root or None)
    return business_identity(consumer, kind=task.scope.kind, scope_id=task.scope.id or "portfolio",
                             entities=entities, as_of=plan.as_of, explicit=explicit,
                             event_id=plan.task_inputs.get("event_id", ""),
                             event_version=plan.task_inputs.get("event_version", ""))


def check_plan_reads(plan, *, collect=False):
    from .cutover_wiring import guard_analyst_output

    guard_analyst_output(what="Dispatcher analyst execution")
    refused = {}
    for task in plan.tasks:
        try:
            gate_read(task_identity(plan, task))
        except RouteUnavailable as exc:
            if not collect:
                raise
            refused[task.instance_key] = str(exc)
    return refused


def decision_read(consumer):
    """Bind a real decision node's role independently of the Chief's authority."""
    def decorate(function):
        @wraps(function)
        def entry(state, **kwargs):
            orders = [order for order in state.decisions if order.action != "hold"]
            if not orders:
                return function(state, **kwargs)
            explicit = kwargs.pop("read_scope", None)
            now = evaluation_now(UTC)
            entities = sorted({order.symbol for order in orders})
            supplied = explicit or {"kind": "decision", "id": state.cycle_id, "entities": entities,
                "time_range": {"start": (now-timedelta(minutes=5)).isoformat(),
                               "end": (now+timedelta(minutes=5)).isoformat()}}
            identity = business_identity(consumer, kind="decision", scope_id=state.cycle_id,
                                         entities=entities, explicit=supplied)
            with bind_read(identity):
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
            fallback_input = kwargs.pop("read_fallback", None)
            as_of = kwargs.pop("read_as_of", None)
            current = current_read_context()
            values = signature.bind(*args, **kwargs)
            values.apply_defaults()
            values = values.arguments
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
            entities = configured_entities(kind, scope_id, sector=name if kind == "layer" else "")
            if current and current.identity.consumer_id == consumer and explicit is None:
                scope = current.identity.scope
                if scope["kind"] != kind or scope["id"] != scope_id or scope["entities"] != sorted(set(entities)):
                    raise RouteUnavailable("nested business request escapes bound scope")
                identity = current.identity
            else:
                identity = business_identity(consumer, kind=kind, scope_id=scope_id,
                                             entities=entities, as_of=as_of, explicit=explicit)
            if consumer not in {"chief", "risk", "trader", "clerk"} and function.__name__ != "run_sector_html":
                from .cutover_wiring import guard_analyst_output

                guard_analyst_output(what="analyst business entry")
            with bind_read(identity, mode=current.mode if current else None,
                           publication_path=current.publication_path if current else "",
                           fallback_input=fallback_input):
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
            raise RouteUnavailable("read scopes must be an object keyed by consumer")
        token = _REQUESTS.set(supplied)
        try:
            return function(argv)
        finally:
            _REQUESTS.reset(token)
    return entry
