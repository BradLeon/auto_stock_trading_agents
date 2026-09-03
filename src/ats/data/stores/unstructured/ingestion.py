"""Composite store for data ingestion with explicit Workflow-memory separation."""

from __future__ import annotations

from .platform import get_platform_unstructured_store


_DATA_METHODS = {
    "save_document", "save_document_candidate", "save_document_alias",
    "document_by_external_id", "document_by_content_hash", "document_by_story",
    "latest_document_version", "link_document_entities", "register_data_source",
    "begin_ingestion", "finish_ingestion", "newsletter_cursor",
    "save_newsletter_cursor", "documents", "documents_by_alias_source",
    "document_versions", "document_candidates", "data_source_health",
    "document_source_health", "document_candidate_health",
    "document_quality_inventory",
    "link_document_artifact", "document_artifacts",
    "save_document_pages", "document_pages", "begin_document_processing",
    "finish_document_processing", "document_processing",
}


class DataIngestionStore:
    """Writes reusable assets to platform; delegates only Workflow memory to memory."""

    def __init__(self):
        from ats.memory import get_store
        self.data = get_platform_unstructured_store()
        self.memory = get_store()

    def close(self) -> None:
        self.data.close()

    def __getattr__(self, name: str):
        if name in _DATA_METHODS:
            return getattr(self.data, name)
        return getattr(self.memory, name)


def get_data_ingestion_store() -> DataIngestionStore:
    return DataIngestionStore()


__all__ = ["DataIngestionStore", "get_data_ingestion_store"]
