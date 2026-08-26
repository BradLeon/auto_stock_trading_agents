"""Document asset storage surface under the unified namespace."""

from ats.data.document_assets import identify, ingest, read_document, read_external, stable_key
from ats.data.source_cache import inventory, load, path_for, store

__all__ = [
    "identify",
    "ingest",
    "inventory",
    "load",
    "path_for",
    "read_document",
    "read_external",
    "stable_key",
    "store",
]
