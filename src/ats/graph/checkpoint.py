"""Checkpointer factory.

Phase 2 uses an in-process MemorySaver, which is enough to demonstrate the
interrupt/resume cycle within a single CLI run. A persistent SqliteSaver
(cross-process resume — required for the Feishu/Discord async approval flow) is
wired in a later phase; the seam is here.
"""

from __future__ import annotations

from typing import Any

# Our Pydantic state is serialized into checkpoints between supersteps; LangGraph
# requires the modules holding those types to be allowlisted for safe msgpack
# deserialization. We trust our own schema package.
ALLOWED_MODULES = (
    "ats.schemas.market",
    "ats.schemas.reports",
    "ats.schemas.risk",
    "ats.schemas.decision",
    "ats.schemas.portfolio",
    "ats.schemas.memory",
    "ats.schemas.channel",
    "ats.graph.state",
)


def _serializer() -> Any:
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(allowed_msgpack_modules=tuple((m,) for m in ALLOWED_MODULES))


def get_checkpointer(persist: bool = False) -> Any:
    if persist:
        # Phase 8+: from langgraph.checkpoint.sqlite import SqliteSaver
        # return SqliteSaver.from_conn_string("./var/checkpoints.sqlite")
        raise NotImplementedError("persistent checkpointer lands in a later phase")
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver(serde=_serializer())
