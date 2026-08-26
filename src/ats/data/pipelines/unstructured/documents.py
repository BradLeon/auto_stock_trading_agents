"""Document acquisition and normalization pipeline surface."""

from ats.data.documents import gather, strip_xbrl_boilerplate
from ats.data.document_assets import identify, ingest, read_document, read_external

__all__ = [
    "gather",
    "identify",
    "ingest",
    "read_document",
    "read_external",
    "strip_xbrl_boilerplate",
]
