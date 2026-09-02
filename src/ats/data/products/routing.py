"""Released unstructured-data routing for Agent and Workflow consumers."""

from __future__ import annotations

class UnstructuredReadRouter:
    """Route immutable reads only to the released platform repository."""

    _READ_METHODS = {
        "documents", "latest_document_version", "search_document_chunks",
        "observations", "observation_failures", "facts", "fact_projections",
        "document_processing",
    }

    def __init__(self, *, consumer: str, platform_repository, mode: str = "platform"):
        self.consumer = consumer
        self.platform = platform_repository
        self.mode = mode

    def close(self) -> None:
        self.platform.close()

    def __getattr__(self, name: str):
        if name not in self._READ_METHODS:
            raise AttributeError(
                f"{name!r} belongs to Workflow memory, not a data-product read")
        platform_method = getattr(self.platform, name)

        def routed(*args, **kwargs):
            return platform_method(*args, **kwargs)

        return routed


def get_unstructured_read_router(*, consumer: str, legacy_repository=None) -> UnstructuredReadRouter:
    """Build a post-cutover platform-only router.

    ``legacy_repository`` remains an ignored keyword solely to make the boundary
    explicit at existing caller sites during this release.
    """
    from ..rollout_modes import read_mode
    from ..stores.unstructured import get_platform_unstructured_repository

    mode = read_mode(consumer)
    platform = get_platform_unstructured_repository()
    return UnstructuredReadRouter(consumer=consumer, platform_repository=platform, mode=mode)


__all__ = ["UnstructuredReadRouter", "get_unstructured_read_router"]
