"""Reversible unstructured-read routing for Agent and Workflow consumers."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable


log = logging.getLogger("ats.data.products.routing")


def _signature(rows: Any) -> str:
    """Compare query results without relying on database-specific row objects."""
    return json.dumps(rows, ensure_ascii=False, default=str, sort_keys=True,
                      separators=(",", ":"))


class UnstructuredReadRouter:
    """Route accepted document/evidence reads; retain Workflow memory writes.

    Only immutable data reads are eligible for platform routing.  Unknown methods
    deliberately fall through to the supplied legacy store because they represent
    decision state, processing leases, or other Workflow memory not in this cutover.
    """

    _READ_METHODS = {
        "documents", "latest_document_version", "search_document_chunks",
        "observations", "facts", "fact_projections",
        "document_processing",
    }

    def __init__(self, *, consumer: str, legacy_repository, platform_repository=None,
                 mode: str):
        self.consumer = consumer
        self.legacy = legacy_repository
        self.platform = platform_repository
        self.mode = mode

    def close(self) -> None:
        if self.platform is not None:
            self.platform.close()

    def __getattr__(self, name: str):
        legacy_method = getattr(self.legacy, name)
        if name not in self._READ_METHODS:
            return legacy_method

        def routed(*args, **kwargs):
            if self.mode == "legacy":
                return legacy_method(*args, **kwargs)
            if self.platform is None:
                # A shadow/fallback activation must never make a Workflow unavailable
                # when the target database has not yet been provisioned.
                return legacy_method(*args, **kwargs)
            platform_method = getattr(self.platform, name)
            platform_value = platform_method(*args, **kwargs)
            if self.mode == "platform":
                return platform_value
            if self.mode == "fallback":
                return platform_value if platform_value else legacy_method(*args, **kwargs)
            legacy_value = legacy_method(*args, **kwargs)
            if _signature(legacy_value) != _signature(platform_value):
                log.warning("unstructured shadow mismatch: consumer=%s method=%s", self.consumer, name)
            return legacy_value

        return routed


def get_unstructured_read_router(*, consumer: str, legacy_repository) -> UnstructuredReadRouter:
    """Build a per-consumer router from the existing release-overlay mechanism."""
    from ...structured import read_mode
    from ..stores.unstructured import get_platform_unstructured_repository

    mode = read_mode(consumer)
    try:
        platform = get_platform_unstructured_repository()
    except Exception as exc:  # noqa: BLE001 - legacy is the declared recovery path
        log.warning("unstructured platform unavailable: consumer=%s (%s)", consumer, exc)
        platform = None
    return UnstructuredReadRouter(consumer=consumer, legacy_repository=legacy_repository,
                                  platform_repository=platform, mode=mode)


__all__ = ["UnstructuredReadRouter", "get_unstructured_read_router"]
