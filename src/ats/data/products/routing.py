"""Released unstructured-data routing for Agent and Workflow consumers."""

from __future__ import annotations

class UnstructuredReadRouter:
    """Route immutable reads only to the released platform repository."""

    # Every entry here must resolve on the platform repository. `unmapped_observations`
    # belongs on this side of the boundary: it reads the data-layer observations table
    # (the raw material for induction), not a Workflow-memory conclusion. It was left
    # out when the router was written, which made the chain report crash with
    # "'unmapped_observations' belongs to Workflow memory".
    _READ_METHODS = {
        "documents", "latest_document_version", "document_pages",
        "search_document_chunks",
        "documents_by_id", "observations", "observations_by_id",
        "observation_failures", "facts", "fact_projections",
        "document_processing", "unmapped_observations",
    }

    def __init__(self, *, consumer: str, platform_repository, legacy_repository=None,
                 mode: str = "platform"):
        self.consumer = consumer
        self.platform = platform_repository
        self.legacy = legacy_repository
        self.mode = mode

    def close(self) -> None:
        self.platform.close()

    def __getattr__(self, name: str):
        if name in self._READ_METHODS:
            platform_method = getattr(self.platform, name)

            def routed(*args, **kwargs):
                return platform_method(*args, **kwargs)

            return routed
        # Workflow-memory operations (verdict snapshots, claim proposals, ...) are
        # explicitly NOT data-product reads. `report._render` renders from one object,
        # so they must still resolve: they delegate to the caller's own store rather
        # than reaching into the data layer. Without this the report silently lost
        # every verdict and proposal — the caller only saw a warning it had to know
        # to look for.
        legacy = object.__getattribute__(self, "legacy")
        if legacy is not None and hasattr(legacy, name):
            return getattr(legacy, name)
        raise AttributeError(
            f"{name!r} belongs to Workflow memory, not a data-product read")


def get_unstructured_read_router(*, consumer: str, legacy_repository=None) -> UnstructuredReadRouter:
    """Build a post-cutover platform-only router.

    ``legacy_repository`` remains an ignored keyword solely to make the boundary
    explicit at existing caller sites during this release.
    """
    from ..rollout_modes import read_mode
    from ..stores.unstructured import get_platform_unstructured_repository

    mode = read_mode(consumer)
    platform = get_platform_unstructured_repository()
    return UnstructuredReadRouter(
        consumer=consumer, platform_repository=platform, mode=mode,
        legacy_repository=legacy_repository)


__all__ = ["UnstructuredReadRouter", "get_unstructured_read_router"]
