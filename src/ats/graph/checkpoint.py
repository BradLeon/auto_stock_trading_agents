"""Checkpointer factory.

Phase 2 uses an in-process MemorySaver, which is enough to demonstrate the
interrupt/resume cycle within a single CLI run. A persistent SqliteSaver
(cross-process resume — required for the Feishu/Discord async approval flow) is
wired in a later phase; the seam is here.
"""

from __future__ import annotations

import inspect
from typing import Any


def _allowed_types() -> list[type]:
    """Every Pydantic type that can ride inside a checkpointed TradingState.

    LangGraph allowlists by (module, name); passing the classes themselves lets
    the serializer derive those keys. We trust our own schema package.
    """
    from pydantic import BaseModel

    from .. import schemas as schemas_pkg
    from ..schemas import (  # noqa: F401 - ensure submodules are imported
        channel, decision, fundamentals, macro, market, memory, portfolio, reports, risk,
    )
    from . import state as state_mod

    modules = [channel, decision, fundamentals, macro, market, memory, portfolio, reports,
               risk, state_mod, schemas_pkg]
    types: set[type] = set()
    for mod in modules:
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, BaseModel) and obj is not BaseModel:
                types.add(obj)
    return list(types)


def _serializer() -> Any:
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(allowed_msgpack_modules=_allowed_types())


def get_checkpointer(persist: bool = False) -> Any:
    if persist:
        # Phase 8+: from langgraph.checkpoint.sqlite import SqliteSaver
        # return SqliteSaver.from_conn_string("./var/checkpoints.sqlite")
        raise NotImplementedError("persistent checkpointer lands in a later phase")
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver(serde=_serializer())
