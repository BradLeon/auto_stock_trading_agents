"""Scoped transport recording/replay, never an alternative business implementation.

The wrappers intercept declared data/model interfaces only. Role computation,
contracts, scope guards, risk checks and all result publishers still execute.
"""
from __future__ import annotations

import dataclasses
import importlib
import sys
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from functools import wraps
from unittest.mock import patch

from pydantic import BaseModel

from .shadow_replay import digest

_ACTIVE = ContextVar("business_input_tape", default=None)


def implementation_hashes():
    """Conservatively pin business code plus role prompts and model adapters.

    Qualification's manifest surface is necessary but does not enumerate all
    model prompts/helpers. A pair therefore pins the complete ats source tree.
    """
    from ..config import REPO_ROOT
    from .assurance_surface import fingerprint, load_surface

    paths = set(load_surface().all_paths())
    paths.update(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / 'src/ats').rglob('*')
                 if p.is_file() and p.suffix in {'.py', '.md'})
    return fingerprint(sorted(paths))


def encode(value):
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, BaseModel):
        return {"$model": type(value).__module__ + ":" + type(value).__name__, "value": value.model_dump(mode="json")}
    if dataclasses.is_dataclass(value):
        return {"$dataclass": type(value).__module__ + ":" + type(value).__name__,
                "value": {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    if isinstance(value, tuple):
        return {"$tuple": [encode(x) for x in value]}
    if isinstance(value, list):
        return [encode(x) for x in value]
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported frozen input type: {type(value)}")


def decode(value):
    if isinstance(value, list):
        return [decode(x) for x in value]
    if not isinstance(value, dict):
        return value
    if "$datetime" in value:
        return datetime.fromisoformat(value["$datetime"])
    if "$date" in value:
        return date.fromisoformat(value["$date"])
    if "$tuple" in value:
        return tuple(decode(x) for x in value["$tuple"])
    for tag in ("$model", "$dataclass"):
        if tag in value:
            module, name = value[tag].split(":")
            if not (module.startswith(("ats.schemas.", "ats.data."))
                    or module.startswith("ats.agents.") and module.endswith(".outputs")) or not name.isidentifier():
                raise ValueError("frozen type outside data/schema contracts")
            cls = getattr(importlib.import_module(module), name)
            if tag == "$model" and issubclass(cls, BaseModel):
                return cls.model_validate(value["value"])
            if tag == "$dataclass" and dataclasses.is_dataclass(cls):
                return cls(**{k: decode(v) for k, v in value["value"].items()})
            raise ValueError("invalid frozen data type")
    return {k: decode(v) for k, v in value.items()}


# Small explicit interface allowlist, no patching of compute/role/risk functions.
BOUNDARIES = {
    "ats.data.products.unstructured": ("admitted_documents", "platform_earnings_document_package"),
    "ats.data.products.sector_inputs": ("curated_knowledge_packet", "industry_notes", "industry_named",
        "fetch_named", "factset_sector_context", "factset_sector_material", "regional_monthly",
        "consensus_for", "constituent_financials", "sector_price_history", "sector_prices"),
    "ats.data.products.macro_inputs": ("regional_monthly", "factset_macro_material", "search_news"),
    "ats.data.runtime.macro": ("fetch", "fetch_series"),
    "ats.data.runtime.market_data": ("fetch_snapshot", "fetch_close_history_many"),
    "ats.data.runtime.execution_prices": ("fetch_execution_price",),
    "ats.trader.portfolio": ("snapshot",),
    "ats.data.execution_prices": ("session_context",),
    "ats.data.fundamentals": ("fetch_light",),
}


def _business_scope():
    from .runtime_reads import current_read_context
    current = current_read_context()
    if current is None:
        return {}
    return {"consumer": current.identity.consumer_id, "scope": current.identity.scope}


class Tape:
    def __init__(self, *, replay=None, rows=None):
        self.replay = replay
        self.rows = {} if rows is None else rows
        self.calls = []
        self.faults = []
        self.reads = []
        self.clock_counts = {}

    def clock(self, tz, action):
        frame = sys._getframe(2)  # caller of evaluation_clock.now
        site = []
        # Distinguish validation inside the input transport from validation by
        # the business caller; replay intentionally skips transport internals.
        while frame is not None and len(site) < 3:
            module = frame.f_globals.get("__name__", "")
            if module.startswith("ats."):
                site.append({"module":module,"function":frame.f_code.co_name,"line":frame.f_lineno})
                if frame.f_code.co_name == "_execute": break
            frame = frame.f_back
        scope = digest({"business":_business_scope(),"site":site})
        ordinal = self.clock_counts.get(scope, 0)
        self.clock_counts[scope] = ordinal + 1
        return self.call("evaluation_clock.now", {"tz": str(tz), "site":site,"ordinal": ordinal}, action)


    def call(self, api, request, action, *, model=False):
        from .isolation import verified_isolation_root
        if verified_isolation_root() is None:
            raise PermissionError("input tape requires complete isolation")
        request = {"api": api, "request": request, "business": _business_scope()}
        key = digest(request)
        if self.replay is not None:
            if key not in self.rows:
                self.faults.append(request)
                raise ValueError(f"uncaptured business input; provider fallback forbidden: {api}")
            result = decode(self.rows[key]["result"])
            self.replay.used.append(key)
        elif key in self.rows:
            result = decode(self.rows[key]["result"])
        else:
            result = action()
            row = {"request": request, "result": encode(result), "model_response": model}
            if key in self.rows and self.rows[key] != row:
                raise ValueError(f"input drift during capture: {api}")
            self.rows[key] = row
        self.calls.append({"key": key, "request": request, "result_hash": digest(encode(result))})
        return result


def governed_read(consumer, product, scope, *, as_of=None):
    tape = _ACTIVE.get()
    if tape is None:
        return None
    # This is a derived output of the current run's actual audit chain, not an
    # external input. Always let the governed API rebuild and validate it.
    if getattr(tape, "record_clock", False) and product == "APPROVED_EXECUTION_AUTHORIZATION":
        return None
    from ..data.consumer_api import _json
    scope = _json(scope)
    if tape.replay is None:
        from ..data.consumer_api import ConsumerInput
        row = next((r for r in tape.reads if r["request"]["consumer"] == consumer
                    and r["request"]["product"] == product and r["request"]["scope"] == scope), None)
        return ConsumerInput.model_validate(row["input"]) if row else None
    stage = scope.get("purpose", "business_read")
    try:
        result = tape.replay.read_input(consumer, product, scope=scope, stage=stage, as_of=as_of)
    except Exception:
        tape.faults.append({"api": "consumer_input", "consumer": consumer, "product": product, "scope": scope})
        raise
    tape.calls.append({"api": "consumer_input", "consumer": consumer, "product": product,
                       "scope": scope, "input_hash": digest(result.model_dump(mode="json"))})
    return result


def capture_governed(packet):
    tape = _ACTIVE.get()
    if tape is not None and tape.replay is None:
        if getattr(tape, "record_clock", False) and packet.product == "APPROVED_EXECUTION_AUTHORIZATION":
            tape.calls.append({"api":"derived_authorization", "refs":packet.input_refs})
            return packet
        request = {"consumer": packet.consumer, "product": packet.product, "scope": packet.scope,
                   "stage": packet.scope.get("purpose", "business_read")}
        row = {"request": request, "surface": {"persistent": "persistent_refs", "runtime": "market_runtime",
            "internal": "history_state", "configuration": "ruleset_version", "authorization": "history_state"}[packet.input_mode],
            "input": packet.model_dump(mode="json")}
        if packet.product == "PORTFOLIO_DATA":
            row["surface"] = "account_state"
        prior = next((r for r in tape.reads if r["request"] == request), None)
        if prior is not None and prior != row:
            raise ValueError("governed input drift during capture")
        if prior is None:
            tape.reads.append(row)
    return packet


@contextmanager
def bind_tape(tape):
    """Inject a recorded transport only in one verified isolated business call."""
    from .isolation import verified_isolation_root
    if verified_isolation_root() is None:
        raise PermissionError("transport binding requires complete isolation")
    modules = [importlib.import_module(m) for m in BOUNDARIES]
    # Preload business adapters so already-imported model aliases are rebound too.
    for name in ("ats.agents.information.extract", "ats.agents.information.documents",
                 "ats.agents.layer.layer_review", "ats.agents.sector.rotation", "ats.agents.sector.structure",
                 "ats.agents.macro.review", "ats.agents.fundamental.routine", "ats.graph.pead",
                 "ats.agents.chief.decide"):
        importlib.import_module(name)
    base = importlib.import_module("ats.agents.base")
    model_functions = {base.run_structured}
    # Tests may replace external model aliases; retain them as the capture transport.
    for module in list(sys.modules.values()):
        if module and getattr(module, "__name__", "").startswith("ats.agents."):
            fn = getattr(module, "run_structured", None)
            if callable(fn):
                model_functions.add(fn)
    token = _ACTIVE.set(tape)
    try:
        with ExitStack() as stack:
            if tape.replay is not None:
                import socket
                def refuse_network(*args, **kwargs):
                    tape.faults.append({"api": "network"})
                    raise PermissionError("network forbidden during business replay")
                stack.enter_context(patch.object(socket.socket, "connect", refuse_network))
                stack.enter_context(patch.object(socket, "create_connection", refuse_network))
                stack.enter_context(patch.object(socket, "getaddrinfo", refuse_network))
            if getattr(tape, "record_clock", False):
                from .evaluation_clock import recorded
                stack.enter_context(recorded(tape.clock))
            # The broker read facade is an input, never its submit facade.
            from ..broker.ibkr import IBKRBroker
            original_portfolio = IBKRBroker.get_portfolio
            def portfolio(broker):
                return tape.call("ats.broker.ibkr.get_portfolio", {}, lambda: original_portfolio(broker))
            stack.enter_context(patch.object(IBKRBroker, "get_portfolio", portfolio))
            channel = importlib.import_module("ats.channel")
            class OfflineChannel:
                def push(self, notification):
                    tape.calls.append({"api": "notification_suppressed", "kind": notification.kind})
            stack.enter_context(patch.object(channel, "get_channel", lambda *a, **k: OfflineChannel()))
            for module in modules:
                for name in BOUNDARIES[module.__name__]:
                    original = getattr(module, name)
                    api = module.__name__ + "." + name
                    def wrap(fn, identifier):
                        @wraps(fn)
                        def read(*args, **kwargs):
                            return tape.call(identifier, {"args": encode(args), "kwargs": encode({k:v for k,v in kwargs.items() if k not in {"repository", "store", "products"}})},
                                             lambda: fn(*args, **kwargs))
                        return read
                    stack.enter_context(patch.object(module, name, wrap(original, api)))
            replacements = {}
            for fn in model_functions:
                def wrap_model(original):
                    @wraps(original)
                    def model(agent, schema, context, **kwargs):
                        # The fixed transport is a recorded response at this role/schema;
                        # differing prompts remain in the actual call trace for review.
                        value = tape.call("model:" + agent + ":" + schema.__name__,
                            {"agent": agent, "schema": schema.__module__ + ":" + schema.__name__, "kwargs": encode(kwargs)},
                            lambda: original(agent, schema, context, **kwargs), model=True)
                        tape.calls[-1]["prompt_hash"] = digest(context)
                        return schema.model_validate(value.model_dump(mode="json"))
                    return model
                replacements[fn] = wrap_model(fn)
            for module in list(sys.modules.values()):
                if module and getattr(module, "__name__", "").startswith("ats.agents."):
                    fn = getattr(module, "run_structured", None)
                    if fn in replacements:
                        stack.enter_context(patch.object(module, "run_structured", replacements[fn]))
            yield tape
        if tape.faults:
            raise ValueError("business swallowed an uncaptured input failure")
    finally:
        _ACTIVE.reset(token)
