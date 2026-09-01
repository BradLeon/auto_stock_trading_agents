"""Repository contracts shared by structured and unstructured data stores."""

from __future__ import annotations

from typing import Any, Protocol


class StructuredObservationStore(Protocol):
    def observations(self, **filters: Any) -> list[dict]: ...

    def ingestion_history(self, **filters: Any) -> list[dict]: ...


class DocumentStore(Protocol):
    def documents(self, **filters: Any) -> list[dict]: ...

    def search_document_chunks(self, query: str, **filters: Any) -> list[dict]: ...

    def facts(self, **filters: Any) -> list[dict]: ...

    def observation_failures(self, **filters: Any) -> list[dict]: ...

    def document_processing(self, **filters: Any) -> list[dict]: ...


class IngestionRunStore(Protocol):
    def ingestion_runs(self, **filters: Any) -> list[dict]: ...
